"""PostgreSQL smoke tests — the things that are only true on a real server.

Why this file is separate from the rest of the suite
---------------------------------------------------
Every other test in this project runs on SQLite, deliberately: it is fast, it
needs no server, and it keeps the suite runnable anywhere. But three things the
production database actually has to get right **cannot** be verified on SQLite:

1. **The partial unique index.** ``models.py`` declares the "one REGISTERED lead
   per phone" rule with ``postgresql_where`` as well as ``sqlite_where``. SQLite
   accepting it says nothing about PostgreSQL.
2. **The JSON column.** ``response_body`` is ``sa.JSON``, which renders as
   ``json`` on PostgreSQL and as TEXT on SQLite. Storing Python objects in it is
   dialect-specific behaviour.
3. **Migration DDL.** ``op.add_column`` with a JSON type, the guarded ALTERs in
   ``0002``/``0003``, and the enum-as-VARCHAR defaults all execute as real DDL on
   a real server rather than through SQLite's much more permissive parser.

Before this file existed, all three were documented as "never executed on
PostgreSQL". That was an honest gap, and this closes it.

These tests are **skipped** unless ``TEST_DATABASE_URL`` points at a PostgreSQL
server, so the default SQLite run is completely unaffected. CI sets it and runs a
``postgres:16`` service. Locally:

    TEST_DATABASE_URL=postgresql+psycopg://user@127.0.0.1:5432/db \\
        python -m pytest tests/test_postgres.py -v

The database named in ``TEST_DATABASE_URL`` **is emptied** (the leads table is
dropped and re-migrated). Never point it at anything you care about.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parent.parent

URL = os.environ.get("TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not URL,
    reason="TEST_DATABASE_URL is not set — PostgreSQL tests skipped (expected on the SQLite run)",
)

# Guard against someone aiming this at a database that is not obviously a test
# database. Dropping tables is not something to do by accident.
_FORBIDDEN_HINTS = ("prod", "live", "master", "main")


def _run_alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    """Drive alembic the way an operator does: as a subprocess."""
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def pg_url() -> str:
    if not URL.startswith("postgresql"):
        pytest.skip(f"TEST_DATABASE_URL is not a PostgreSQL URL: {URL!r}")
    name = URL.rsplit("/", 1)[-1].split("?")[0].lower()
    if not name or any(h in name for h in _FORBIDDEN_HINTS):
        pytest.fail(
            f"refusing to run destructive PostgreSQL tests against database {name!r}; "
            "point TEST_DATABASE_URL at a dedicated test database"
        )
    engine = sa.create_engine(URL)
    try:
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment problem
        pytest.skip(f"cannot reach PostgreSQL at TEST_DATABASE_URL: {exc}")
    finally:
        engine.dispose()
    return URL


@pytest.fixture(scope="module")
def migrated(pg_url: str) -> str:
    """Empty the database and migrate it from scratch, the way a deploy does."""
    engine = sa.create_engine(pg_url)
    with engine.begin() as conn:
        conn.execute(sa.text("DROP TABLE IF EXISTS leads CASCADE"))
        conn.execute(sa.text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    engine.dispose()

    result = _run_alembic(pg_url, "upgrade", "head")
    assert result.returncode == 0, (
        f"alembic upgrade head FAILED on PostgreSQL:\n{result.stdout}\n{result.stderr}"
    )
    return pg_url


# ---------------------------------------------------------------------------
# 1. The schema that actually landed
# ---------------------------------------------------------------------------


def test_migrations_run_on_postgresql(migrated: str) -> None:
    engine = sa.create_engine(migrated)
    try:
        names = set(sa.inspect(engine).get_table_names())
        assert "leads" in names, f"leads table missing after migration; found {sorted(names)}"
        assert "alembic_version" in names, "alembic did not stamp a revision"
    finally:
        engine.dispose()


def test_every_orm_column_exists_on_postgresql(migrated: str) -> None:
    """Catches drift between the migrations and the model on the real dialect."""
    from app.models import Lead

    engine = sa.create_engine(migrated)
    try:
        actual = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    expected = {c.name for c in Lead.__table__.columns}
    assert expected - actual == set(), f"missing on PostgreSQL: {sorted(expected - actual)}"
    assert actual - expected == set(), f"unexpected on PostgreSQL: {sorted(actual - expected)}"


# ---------------------------------------------------------------------------
# 2. The partial unique index — the rule the whole design rests on
# ---------------------------------------------------------------------------


def test_partial_unique_index_exists_and_has_the_right_predicate(migrated: str) -> None:
    """SQLite accepting this says nothing. PostgreSQL must actually carry it."""
    engine = sa.create_engine(migrated)
    try:
        # pg_indexes gives the real DDL PostgreSQL stored, predicate included.
        with engine.connect() as conn:
            row = conn.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE tablename = 'leads' AND indexname = 'uq_leads_live_phone'"
                )
            ).fetchone()
    finally:
        engine.dispose()

    assert row is not None, "partial unique index uq_leads_live_phone was not created"
    ddl = row[0]
    assert "UNIQUE" in ddl, ddl
    assert "WHERE" in ddl, f"index is not partial on PostgreSQL: {ddl}"
    assert "REGISTER_LEAD" in ddl, ddl
    # The rule covers BOTH live states. Covering only REGISTERED was the first
    # version, and it left the window CI found: an attempt starting just after
    # another completes could still call the provider a second time.
    assert "REGISTERED" in ddl, ddl
    assert "in_flight_at IS NOT NULL" in ddl, ddl


def test_index_refuses_a_second_registered_lead_for_the_same_phone(migrated: str) -> None:
    engine = sa.create_engine(migrated)
    insert = sa.text(
        "INSERT INTO leads (lead_id, lead_type, registration_status, full_name, phone,"
        " phone_display, tracking_token, created_at, updated_at) "
        "VALUES (:id, 'REGISTER_LEAD', :st, 'A', :ph, :ph, :tok, now(), now())"
    )
    try:
        with engine.begin() as conn:
            conn.execute(
                insert,
                {"id": str(uuid.uuid4()), "st": "REGISTERED", "ph": "+84900001234", "tok": "a"},
            )
        # A PENDING lead for the same phone must be ALLOWED: that customer is not
        # registered yet, and refusing them would strand them permanently.
        with engine.begin() as conn:
            conn.execute(
                insert, {"id": str(uuid.uuid4()), "st": "PENDING", "ph": "+84900001234", "tok": "b"}
            )
        # A second REGISTERED lead for the same phone must be REFUSED.
        with pytest.raises(sa.exc.IntegrityError) as excinfo:
            with engine.begin() as conn:
                conn.execute(
                    insert,
                    {"id": str(uuid.uuid4()), "st": "REGISTERED", "ph": "+84900001234", "tok": "c"},
                )
        assert "uq_leads_live_phone" in str(excinfo.value)
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM leads WHERE phone = '+84900001234'"))
        engine.dispose()


# ---------------------------------------------------------------------------
# 3. The JSON column — dialect-specific behaviour
# ---------------------------------------------------------------------------


def test_response_body_is_a_real_json_column_and_round_trips(migrated: str) -> None:
    engine = sa.create_engine(migrated)
    try:
        with engine.connect() as conn:
            data_type = conn.execute(
                sa.text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'leads' AND column_name = 'response_body'"
                )
            ).scalar()
        assert data_type == "json", f"expected a json column on PostgreSQL, got {data_type!r}"

        payload = {
            "error": {"code": "DUPLICATE_PHONE"},
            "n": 1,
            "ok": False,
            "vi": "Đăng ký thành công",
        }
        lead_id = str(uuid.uuid4())
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO leads (lead_id, lead_type, registration_status, full_name,"
                    " phone, phone_display, tracking_token, response_status, response_body,"
                    " created_at, updated_at) "
                    "VALUES (:id,'REGISTER_LEAD','FAILED','A','+84900005678','x','t',409,"
                    " CAST(:body AS json), now(), now())"
                ),
                {"id": lead_id, "body": json.dumps(payload, ensure_ascii=False)},
            )
        with engine.connect() as conn:
            got = conn.execute(
                sa.text("SELECT response_body FROM leads WHERE lead_id = :id"),
                {"id": lead_id},
            ).scalar()
        # psycopg returns a dict for a json column; non-ASCII must survive intact.
        assert got == payload, f"round-trip mismatch: {got!r}"
    finally:
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM leads WHERE phone = '+84900005678'"))
        engine.dispose()


# ---------------------------------------------------------------------------
# 4. A real registration through the application, on PostgreSQL
# ---------------------------------------------------------------------------


def test_registration_works_end_to_end_on_postgresql(migrated: str) -> None:
    """Drive the actual app against PostgreSQL, not just the schema."""
    from fastapi.testclient import TestClient

    from app.config import Settings, get_settings
    from app.main import create_app

    settings = Settings(
        database_url=migrated,
        khaibao9610_mode="mock",
        mock_provider_behaviour="success",
        rate_limit_enabled=False,
        admin_api_token="",
        auto_create_schema=False,  # we migrated it ourselves, like a deploy does
    )
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings

    phone = "+84900009090"
    body = {
        "full_name": "Khách PostgreSQL",
        "phone": phone,
        "password": "pg-canary-not-a-real-secret",
        "email": "",  # the browser sends this empty
        "consent": True,
        # service_interest deliberately absent
    }
    try:
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/registrations",
                json=body,
                headers={"Idempotency-Key": "pg-e2e-key"},
            )
            assert first.status_code == 201, first.text
            code = first.json()["external_customer_code"]
            assert code, first.text

            # Duplicate phone: refused, and must not leak the earlier customer's code.
            second = client.post(
                "/api/v1/registrations",
                json=body,
                headers={"Idempotency-Key": "pg-e2e-key-2"},
            )
            assert second.status_code == 409, second.text
            assert code not in second.text, f"customer code leaked in {second.text}"

            # Replay: the SAME key and body must return the stored response verbatim.
            replay = client.post(
                "/api/v1/registrations",
                json=body,
                headers={"Idempotency-Key": "pg-e2e-key"},
            )
            assert replay.status_code == first.status_code, (
                f"replay status {replay.status_code} != original {first.status_code}"
            )
            assert replay.json() == first.json(), "replay body differs from the original"

        # And the password must not be anywhere in the row.
        engine = sa.create_engine(migrated)
        try:
            with engine.connect() as conn:
                row = (
                    conn.execute(sa.text("SELECT * FROM leads WHERE phone = :p"), {"p": phone})
                    .mappings()
                    .one()
                )
        finally:
            engine.dispose()
        assert row["registration_status"] == "REGISTERED"
        assert row["external_customer_code"] == code
        assert row["consent_version"], "consent evidence not recorded"
        assert row["request_fingerprint"], "fingerprint not stored"
        joined = " ".join(str(v) for v in row.values())
        assert "pg-canary-not-a-real-secret" not in joined, "PASSWORD PERSISTED ON POSTGRESQL"
    finally:
        engine = sa.create_engine(migrated)
        with engine.begin() as conn:
            conn.execute(sa.text("DELETE FROM leads WHERE phone = :p"), {"p": phone})
        engine.dispose()


# ---------------------------------------------------------------------------
# 5. A trap worth pinning: the mock's phone-suffix convention
# ---------------------------------------------------------------------------


def test_mock_suffix_convention_hijacks_phones_ending_0000_to_0004() -> None:
    """Documents a real trap that cost me a red test.

    ``MockRegistrationProvider.behaviour_for`` lets the last four digits of the
    phone override the configured behaviour, so a single process can answer
    differently per request. The consequence is that any test phone ending
    ``0000``-``0004`` is silently hijacked — a phone ending ``0003`` will time
    out no matter what ``MOCK_PROVIDER_BEHAVIOUR`` says, and the failure looks
    like a broken application rather than a colliding fixture.

    This test exists so the next person to pick a round test number finds out
    in one second instead of debugging a registration flow.
    """
    from app.providers.mock import SUFFIX_BEHAVIOURS, MockRegistrationProvider

    provider = MockRegistrationProvider(behaviour="success")
    for suffix, expected in sorted(SUFFIX_BEHAVIOURS.items()):
        assert provider.behaviour_for(f"+8490000{suffix}") == expected, (
            f"phone ending {suffix} should map to {expected}"
        )

    # A phone that does NOT collide keeps the configured behaviour.
    assert provider.behaviour_for("+84900009090") == "success"
