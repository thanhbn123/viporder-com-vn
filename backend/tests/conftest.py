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
from sqlalchemy import Connection, create_engine, text

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

#: The PostgreSQL-only module, and what this session collected / executed from it.
#: Used by the exit guard at the bottom of this file.
_PG_MODULE = "tests/test_postgres.py"
_pg_collected: set[str] = set()
_pg_executed: set[str] = set()

#: Sweep findings, printed with the engine banner.
_SWEEP_NOTES: list[str] = []

#: How many of those notes the banner already printed. Anything appended after
#: the banner (a failed drop, the PostgreSQL guard) is printed in the terminal
#: summary instead — otherwise it would be written to a channel nobody reads.
_NOTES_PRINTED = 0

#: How old (seconds) a leftover ``t_*`` schema must be before it is swept.
#: Six hours: a live run's schema is seconds old, so a concurrent run is never a
#: candidate. Overridable so the sweep itself can be measured — see
#: ``_sweep_stale_schemas``.
STALE_SCHEMA_SECONDS = 6 * 60 * 60


def _suite_engine() -> str:
    """The engine the HARNESS will use for the default suite.

    ONE SOURCE, and it is the same decision ``_isolated_url`` makes. This is why
    it is a function rather than an inline expression: the banner used to derive
    the answer from ``DATABASE_URL``, which is wrong in BOTH directions. With
    ``DATABASE_URL=<postgres>`` and no ``TEST_DATABASE_URL`` the banner claimed
    "postgresql" for a run in which every harness test built SQLite.
    """
    return "postgresql" if _is_postgres(os.environ.get("TEST_DATABASE_URL", "")) else "sqlite"


def _redact_url(url: str) -> str:
    """Hide the password in a printed URL. A DSN is a credential."""
    if "://" not in url or "@" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    if ":" not in creds:
        return url
    return f"{scheme}://{creds.split(':', 1)[0]}:***@{host}"


def pytest_report_collectionfinish(config: pytest.Config, items: list[pytest.Item]) -> str:
    """Print the engine for EVERY run, including the ``-q`` runs.

    WHY NOT ``pytest_report_header``. The banner used to be a report header, and
    ``-q`` SUPPRESSES report headers — while ``-q`` is how every documented
    command and every CI job invokes pytest. MEASURED:

        TEST_DATABASE_URL=<pg> pytest tests/test_engine_identity.py
          -> banner printed, and it said "default suite engine = sqlite"  (FALSE)

        TEST_DATABASE_URL=<pg> pytest tests/test_engine_identity.py -q
          -> no banner at all

    So the one thing the banner exists for — no result is ambiguous about its
    engine — was absent from every run that produces a result. Output from
    ``pytest_report_collectionfinish`` survives ``-q`` (measured), and it is
    printed BEFORE the tests, which is where a header belongs.

    WHY IT EXISTS AT ALL. A previous verification of this project reported a green
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
        f"    DATABASE_URL         = {_redact_url(ambient) or '<unset>'}",
        f"    TEST_DATABASE_URL    = {_redact_url(test_url) or '<unset>'}",
        f"    default suite engine = {_suite_engine()}"
        f"  <- what `harness`/`make_settings` actually build",
        f"    PostgreSQL-only tests = {'RUN' if _is_postgres(test_url) else 'SKIPPED'}",
    ]
    if _is_postgres(ambient) and not _is_postgres(test_url):
        lines.append(
            "    *** WARNING: DATABASE_URL points at PostgreSQL but TEST_DATABASE_URL is "
            "unset. The harness suite below still builds SQLite (see `_isolated_url`), "
            "but `app.main`'s module-level app was built against PostgreSQL at import. "
            "`test_engine_identity.py` fails this run on purpose. ***"
        )
    lines.extend(_SWEEP_NOTES)
    global _NOTES_PRINTED
    _NOTES_PRINTED = len(_SWEEP_NOTES)
    return "\n".join(lines)


def pytest_terminal_summary(terminalreporter: Any, exitstatus: int, config: pytest.Config) -> None:
    """Print notes that only exist AFTER the run (a failed drop, the guard).

    The banner is printed before collection, so anything appended to
    ``_SWEEP_NOTES`` later has no other channel. ``write_line`` survives ``-q``.
    """
    for note in _SWEEP_NOTES[_NOTES_PRINTED:]:
        terminalreporter.write_line(note)


def pytest_collection_modifyitems(
    session: pytest.Session, config: pytest.Config, items: list[pytest.Item]
) -> None:
    for item in items:
        if _PG_MODULE in item.nodeid.replace("\\", "/"):
            _pg_collected.add(item.nodeid)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call" and _PG_MODULE in report.nodeid.replace("\\", "/"):
        _pg_executed.add(report.nodeid)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Collect schemas orphaned by a previous run before this one starts."""
    test_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not _is_postgres(test_url):
        return  # the SQLite run must not touch a server at all
    try:
        _sweep_stale_schemas(test_url)
    except Exception as exc:  # noqa: BLE001 - a broken sweep must not break the run
        # Logged, never swallowed: a sweep that silently stops working is exactly
        # the failure this file already has one instance of (`except: pass`).
        _SWEEP_NOTES.append(
            f"    schema sweep: FAILED ({type(exc).__name__}: {exc}) — "
            f"orphaned t_* schemas were NOT collected"
        )


def _schema_age_seconds(conn: Connection, name: str) -> float | None:
    """Age of a schema, from the newest relation file inside it.

    ``pg_stat_file`` reads the file's ``modification`` time. This needs no
    bookkeeping inside the schema, so it also dates schemas created before this
    sweep existed. Returns ``None`` when the schema cannot be dated — an EMPTY
    schema, or a server where the caller may not read file metadata. Undatable
    schemas are reported and left alone: guessing is how a live schema gets
    dropped.
    """
    age = conn.execute(
        text(
            "SELECT extract(epoch FROM now() - max("
            "  (pg_stat_file(pg_relation_filepath(c.oid::regclass))).modification)) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = :name AND c.relkind IN ('r', 'i', 'S')"
        ),
        {"name": name},
    ).scalar()
    return None if age is None else float(age)


def _sweep_stale_schemas(test_url: str) -> None:
    """Drop ``t_*`` schemas orphaned by a session that was KILLED.

    WHY. ``pytest_sessionfinish`` drops this session's schemas, but ``kill -9``
    never runs it. MEASURED: **52** leftover ``t_*`` schemas after one aborted
    run; 0 after a clean run. Each one holds ~50 tables of catalogue bloat in a
    database that is shared, and until now nothing ever collected them.

    AGE-BASED, because a CONCURRENT run must not be damaged and age is the only
    evidence that separates an orphan from a live schema: a live one was written
    to seconds ago, an orphan hours ago. Threshold ``STALE_SCHEMA_SECONDS``
    (6 h), overridable through ``VIPORDER_TEST_SCHEMA_STALE_SECONDS`` so the
    sweep can be measured and negative-controlled without waiting six hours.
    """
    threshold = float(os.environ.get("VIPORDER_TEST_SCHEMA_STALE_SECONDS", STALE_SCHEMA_SECONDS))
    engine = create_engine(test_url)
    dropped: list[str] = []
    kept: list[str] = []
    undatable: list[str] = []
    failures: list[str] = []
    try:
        with engine.connect() as conn:
            names = [
                row[0]
                for row in conn.execute(
                    text(r"SELECT nspname FROM pg_namespace WHERE nspname LIKE 't\_%' ORDER BY 1")
                )
            ]
            for name in names:
                if any(schema == name for _, schema in _SCHEMAS_TO_DROP):
                    continue  # ours, created this session (unreachable, but cheap)
                try:
                    age = _schema_age_seconds(conn, name)
                except Exception as exc:  # noqa: BLE001
                    undatable.append(f"{name} (cannot read file age: {type(exc).__name__})")
                    continue
                if age is None:
                    undatable.append(f"{name} (empty — no relation to date it by)")
                    continue
                if age < threshold:
                    kept.append(name)
                    continue
                try:
                    conn.execute(text(f'DROP SCHEMA IF EXISTS "{name}" CASCADE'))
                    conn.commit()
                    dropped.append(name)
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{name} ({type(exc).__name__}: {exc})")
    finally:
        engine.dispose()

    _SWEEP_NOTES.append(
        f"    stale t_* schema sweep: dropped {len(dropped)}, kept {len(kept)} "
        f"younger than {threshold:g}s, undatable {len(undatable)}, failed {len(failures)}"
    )
    if dropped:
        _SWEEP_NOTES.append(f"      dropped: {', '.join(dropped)}")
    if undatable:
        _SWEEP_NOTES.append(f"      NOT swept (needs a human): {', '.join(undatable)}")
    if failures:
        _SWEEP_NOTES.append(f"      DROP FAILED: {', '.join(failures)}")


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
    if _suite_engine() != "postgresql":
        return f"sqlite:///{tmp_path / (uuid.uuid4().hex + '.db')}"
    assert _is_postgres(test_url)  # the two answers come from the same place
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
    """Drop this run's schemas, and refuse to let a PostgreSQL run prove nothing."""
    for test_url, schema in _SCHEMAS_TO_DROP:
        try:
            engine = create_engine(test_url)
            with engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            engine.dispose()
        except Exception as exc:  # noqa: BLE001 - cleanup must not abort the run
            # LOGGED, not swallowed. `except Exception: pass` here hid a failed
            # drop for as long as this code existed, and a failed drop is exactly
            # the leak the sweep at session start exists to clean up.
            _SWEEP_NOTES.append(f"    schema drop FAILED for {schema}: {type(exc).__name__}: {exc}")
    _guard_postgres_tests_ran(session)


def _guard_postgres_tests_ran(session: pytest.Session) -> None:
    """A session that SELECTED the PostgreSQL module must EXECUTE it.

    The second half of the fix for a green PostgreSQL job whose assertions never
    ran. ``pg_url`` now fails loudly instead of skipping, which covers an
    unreachable server; this covers the rest — a future ``pytest.skip`` inside a
    test, a fixture that errors, or anything else that turns a collected
    PostgreSQL test into a non-result. MEASURED before the fix, with an
    unreachable URL: ``1 passed, 6 skipped``, **exit code 0**.

    Scoped to "collected but not executed", so it does not fire for a run that
    legitimately selects other files (CI runs ``test_concurrency.py`` with the
    same ``TEST_DATABASE_URL``) and does not fire on the SQLite run, where these
    tests are skipped by design and ``TEST_DATABASE_URL`` is unset.
    """
    if not _is_postgres(os.environ.get("TEST_DATABASE_URL", "")):
        return
    missing = sorted(_pg_collected - _pg_executed)
    if not missing:
        return
    _SWEEP_NOTES.append(
        f"\nPOSTGRESQL GUARD: TEST_DATABASE_URL is set and {len(_pg_collected)} "
        f"PostgreSQL test(s) were collected, but {len(missing)} never executed:"
    )
    for nodeid in missing:
        _SWEEP_NOTES.append(f"    {nodeid}")
    _SWEEP_NOTES.append(
        "The engine-proof job must execute its assertions. This session is reported as a FAILURE."
    )
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def _scheme(url: str) -> str:
    return url.split(":", 1)[0] if "://" in url else "sqlite"


def _is_postgres(url: str) -> bool:
    return _scheme(url).startswith("postgres")
