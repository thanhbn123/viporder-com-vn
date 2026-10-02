"""Shared test fixtures.

Everything runs offline. The only HTTP provider that could reach the network is
never constructed in this suite; where it is exercised it is given an
``httpx.MockTransport``.

No test ever points ``DATABASE_URL`` at the repository: every database is a
fresh SQLite file inside pytest's ``tmp_path``.
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

# Importing `app.main` builds a module-level app with the ambient environment.
# Point that at a throwaway location BEFORE the import so merely importing the
# package never writes a database into the working tree.
_MODULE_TMP = Path(tempfile.mkdtemp(prefix="viporder-import-"))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_MODULE_TMP / 'import-side-effect.db'}")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.main import create_app  # noqa: E402

DEFAULT_PASSWORD = "secret-at-least-8"

VALID_PAYLOAD: dict[str, Any] = {
    "full_name": "Nguyễn Văn A",
    "phone": "0912345678",
    "password": DEFAULT_PASSWORD,
    # Required since the fabricated values were removed from the provider
    # adapter: the confirmation is the customer's, and a mismatch is a 422.
    "confirm_password": DEFAULT_PASSWORD,
    "email": "a@example.com",
    "province": "Bắc Ninh",
    "service_interest": "transport",
    "consent": True,
    "accept_terms": True,
    "attribution": {
        "utm_source": "facebook",
        "utm_medium": "cpc",
        "utm_campaign": "g01",
        "utm_content": "ad1",
        "utm_term": "nhaphang",
        "landing_page": "https://viporder.com.vn/?utm_source=facebook",
        "referrer": "https://facebook.com/",
    },
}


def payload(**overrides: Any) -> dict[str, Any]:
    """A valid registration body with selected fields overridden."""
    body = {k: (dict(v) if isinstance(v, dict) else v) for k, v in VALID_PAYLOAD.items()}
    for key, value in overrides.items():
        if value is Ellipsis:
            body.pop(key, None)
        else:
            body[key] = value
    # A real client echoes what the customer typed into the confirmation field, so
    # overriding `password` carries the confirmation with it. This is a helper
    # convenience, NOT a relaxation: the schema still requires the field and still
    # rejects a mismatch. Tests that are *about* the mismatch pass
    # `confirm_password=` explicitly and win, because the loop above ran first.
    if "password" in overrides and overrides["password"] is not Ellipsis:
        if "confirm_password" not in overrides:
            body["confirm_password"] = overrides["password"]
    return body


@dataclass
class Harness:
    """A running app plus the handles a test needs to inspect it."""

    app: Any
    database: Database
    settings: Settings
    client: TestClient

    def post_registration(self, body: dict[str, Any] | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
        return self.client.post(
            "/api/v1/registrations", json=body if body is not None else payload(), **kwargs
        )

    def lead_rows(self) -> list[Any]:
        from app.models import Lead

        with self.database.session() as session:
            return list(session.query(Lead).order_by(Lead.created_at).all())

    def lead_row(self, lead_id: str) -> Any:
        from app.models import Lead

        with self.database.session() as session:
            return session.get(Lead, lead_id)

    def db_path(self) -> Path:
        url = self.settings.database_url
        return Path(url[len("sqlite:///") :])

    def dispose(self) -> None:
        """Close the pool and flush SQLite sidecar files."""
        self.database.engine.dispose()
        for suffix in ("", "-wal", "-shm"):
            path = Path(str(self.db_path()) + suffix)
            if path.exists():
                path.unlink()


@pytest.fixture
def make_settings(tmp_path: Path):
    def _make(**overrides: Any) -> Settings:
        defaults: dict[str, Any] = {
            "database_url": f"sqlite:///{tmp_path / (uuid.uuid4().hex + '.db')}",
            "khaibao9610_mode": "mock",
            "mock_provider_behaviour": "success",
            "rate_limit_enabled": False,
            "admin_api_token": "",
            "auto_create_schema": True,
        }
        defaults.update(overrides)
        return Settings(**defaults)

    return _make


@pytest.fixture
def make_harness(make_settings):
    """Build an app + TestClient with explicit settings.

    ``make_harness(provider=..., rate_limit_enabled=True, ...)``
    """
    harnesses: list[Harness] = []

    def _make(*, provider: Any = None, database: Database | None = None, **overrides: Any):
        settings = make_settings(**overrides)
        db = database or Database(settings.database_url)
        app = create_app(settings, provider=provider, database=db, create_schema=True)
        client = TestClient(app, raise_server_exceptions=False)
        client.__enter__()
        harness = Harness(app=app, database=db, settings=settings, client=client)
        harnesses.append(harness)
        return harness

    yield _make

    for harness in harnesses:
        try:
            harness.client.__exit__(None, None, None)
        finally:
            harness.dispose()


@pytest.fixture
def harness(make_harness) -> Iterator[Harness]:
    yield make_harness()


@pytest.fixture
def client(harness: Harness) -> TestClient:
    return harness.client
