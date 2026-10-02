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
from sqlalchemy import create_engine, text

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
            "database_url": _isolated_url(tmp_path),
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

# ---------------------------------------------------------------------------
# G14 — which database did this run ACTUALLY use?
# ---------------------------------------------------------------------------

def pytest_report_header(config: pytest.Config) -> list[str]:
    """Print the engine every run, so no result is ambiguous about its backend.

    WHY THIS EXISTS. A previous verification of this project reported a green
    "SQLite" run while ``TEST_DATABASE_URL`` — or ``DATABASE_URL`` — was still
    exported in the shell, so **both runs were PostgreSQL** and the default path
    was never measured. The command's *name* said SQLite; the engine did not.

    ``os.environ.setdefault`` below is the mechanism that let it happen: an
    inherited ``DATABASE_URL`` wins silently over the intended default. The fix is
    not to remember to ``env -u`` things — it is to make the engine impossible to
    be wrong about, by printing it and by asserting it (``test_engine_identity``).
    """
    ambient = os.environ.get("DATABASE_URL", "")
    test_url = os.environ.get("TEST_DATABASE_URL", "")
    lines = [
        "",
        "database engines for this run:",
        f"    DATABASE_URL       = {ambient or '<unset>'}",
        f"    TEST_DATABASE_URL  = {test_url or '<unset>'}",
        f"    default suite engine = {_scheme(ambient) if ambient else 'sqlite'}",
        f"    PostgreSQL-only tests = {'RUN' if _is_postgres(test_url) else 'SKIPPED'}",
    ]
    if _is_postgres(ambient) and not _is_postgres(test_url):
        lines.append(
            "    *** WARNING: DATABASE_URL points at PostgreSQL but TEST_DATABASE_URL "
            "is unset. The default suite will use PostgreSQL, NOT SQLite. ***"
        )
    return lines


def _isolated_url(tmp_path: Path) -> str:
    """A database URL that nothing else shares.

    SQLite gets a throwaway file. PostgreSQL gets a **unique schema** inside the
    database named by ``TEST_DATABASE_URL``, selected through ``search_path`` —
    because there is no free throwaway *database* per test, and without isolation
    the suite would share one set of tables and its tests would collide.

    WHY THIS EXISTS. Before it, the shared ``harness`` fixture built SQLite
    unconditionally. Setting ``TEST_DATABASE_URL`` therefore ran **twelve** tests
    on PostgreSQL and the other ~516 on SQLite — while the run was reported as
    "backend (real PostgreSQL), 528 passed". The number was real; the label was
    not, and it is the same class of mistake as the green "SQLite" run that was
    secretly PostgreSQL.
    """
    test_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not _is_postgres(test_url):
        return f"sqlite:///{tmp_path / (uuid.uuid4().hex + '.db')}"
    schema = "t_" + uuid.uuid4().hex[:16]
    _create_schema(test_url, schema)
    _SCHEMAS_TO_DROP.append((test_url, schema))
    sep = "&" if "?" in test_url else "?"
    return f"{test_url}{sep}options=-csearch_path%3D{schema}"


#: Schemas this session created, dropped at exit. Leaving them behind would make
#: the test database grow a new schema every run, forever.
_SCHEMAS_TO_DROP: list[tuple[str, str]] = []


def _create_schema(test_url: str, schema: str) -> None:
    """The schema must exist before `metadata.create_all` can create tables in it.

    psycopg says `InvalidSchemaName: no schema has been selected to create in` —
    and it says it *per table*, so the failure reads as 51 unrelated errors.
    """
    engine = create_engine(test_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    finally:
        engine.dispose()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Drop the schemas this run created, so the test database does not grow."""
    for test_url, schema in _SCHEMAS_TO_DROP:
        try:
            engine = create_engine(test_url)
            with engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            engine.dispose()
        except Exception:  # noqa: BLE001 - cleanup must never fail the run
            pass


def _scheme(url: str) -> str:
    return url.split(":", 1)[0] if "://" in url else "sqlite"


def _is_postgres(url: str) -> bool:
    return _scheme(url).startswith("postgres")
