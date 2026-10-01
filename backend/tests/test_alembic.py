"""Alembic must create the real schema — and the real schema must match the ORM.

``alembic upgrade head`` is run the way an operator runs it: as a subprocess,
against a throwaway SQLite file, with ``DATABASE_URL`` in the environment. The
test then compares the migrated database to ``Lead.__table__`` so a migration
that drifts from the models fails here instead of in production.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _orm_columns() -> set[str]:
    from app.models import Lead

    return {c.name for c in Lead.__table__.columns}


def _orm_indexes() -> set[str]:
    from app.models import Lead

    return {i.name for i in Lead.__table__.indexes if i.name}


# Derived, never hand-listed. These were two hand-maintained sets of 29 names and
# 7 index names — the same fact written down twice — and they silently stopped
# enumerating the moment a migration added a column, because the assertions use
# `<=`. Deriving them means a new column is covered the instant the model has it.
EXPECTED_COLUMNS = _orm_columns()
EXPECTED_INDEXES = _orm_indexes()


def _run_alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def migrated_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    result = _run_alembic(url, "upgrade", "head")
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"
    return url


def test_alembic_upgrade_head_succeeds(migrated_url: str) -> None:
    assert Path(migrated_url[len("sqlite:///") :]).exists()


def test_alembic_creates_the_leads_table(migrated_url: str) -> None:
    engine = sa.create_engine(migrated_url)
    try:
        inspector = sa.inspect(engine)
        assert "leads" in inspector.get_table_names()
    finally:
        engine.dispose()


def test_alembic_creates_every_column(migrated_url: str) -> None:
    engine = sa.create_engine(migrated_url)
    try:
        columns = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    assert EXPECTED_COLUMNS <= columns


def test_alembic_creates_every_index(migrated_url: str) -> None:
    engine = sa.create_engine(migrated_url)
    try:
        index_names = {i["name"] for i in sa.inspect(engine).get_indexes("leads")}
    finally:
        engine.dispose()
    assert EXPECTED_INDEXES <= index_names


def test_alembic_schema_matches_the_orm_metadata(migrated_url: str) -> None:
    """The migrated table and ``Lead.__table__`` must agree.

    Compared: column names, nullability and the set of index names. Not
    compared: exact server defaults and constraint names, which differ in
    spelling between SQLite and PostgreSQL without being a real divergence.
    """
    from app.models import Lead

    engine = sa.create_engine(migrated_url)
    try:
        inspector = sa.inspect(engine)
        migrated = {c["name"]: c for c in inspector.get_columns("leads")}
        index_names = {i["name"] for i in inspector.get_indexes("leads")}
    finally:
        engine.dispose()

    orm_columns = {c.name: c for c in Lead.__table__.columns}
    assert set(migrated) == set(orm_columns), "column sets differ"

    for name, column in orm_columns.items():
        assert migrated[name]["nullable"] == column.nullable, f"nullability differs: {name}"

    orm_indexes = {i.name for i in Lead.__table__.indexes}
    assert orm_indexes == index_names, "index sets differ"


def test_alembic_downgrade_removes_the_table(migrated_url: str) -> None:
    result = _run_alembic(migrated_url, "downgrade", "base")
    assert result.returncode == 0, result.stderr

    engine = sa.create_engine(migrated_url)
    try:
        assert "leads" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_alembic_is_a_no_op_when_the_schema_already_exists(tmp_path: Path) -> None:
    """AUTO_CREATE_SCHEMA then `alembic upgrade head` must not explode."""
    from app.db import Database
    from app.models import Base

    url = f"sqlite:///{tmp_path / 'precreated.db'}"
    database = Database(url)
    Base.metadata.create_all(database.engine)
    database.dispose()

    result = _run_alembic(url, "upgrade", "head")
    assert result.returncode == 0, result.stderr


def test_alembic_offline_mode_renders_sql(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'offline.db'}"
    result = _run_alembic(url, "upgrade", "head", "--sql")
    assert result.returncode == 0, result.stderr
    assert "CREATE TABLE leads" in result.stdout


def test_migration_revision_is_named_as_documented() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_current_head() == "0004_phone_claim"


def test_alembic_upgrades_from_0001_to_head(tmp_path: Path) -> None:
    """0001, then head — asserting the columns 0002 adds actually appear.

    Named honestly: this jumps to head, so it does NOT exercise 0003 or 0004 as
    individual steps. `test_every_revision_applies_one_step_at_a_time` does that.
    """
    url = f"sqlite:///{tmp_path / 'stepped.db'}"

    first = _run_alembic(url, "upgrade", "0001_create_leads")
    assert first.returncode == 0, first.stderr

    engine = sa.create_engine(url)
    try:
        after_0001 = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    assert "consent_given_at" not in after_0001

    second = _run_alembic(url, "upgrade", "head")
    assert second.returncode == 0, second.stderr

    engine = sa.create_engine(url)
    try:
        after_0002 = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    assert {"consent_given_at", "consent_version", "request_fingerprint"} <= after_0002


def test_0002_is_a_no_op_when_the_columns_already_exist(tmp_path: Path) -> None:
    """The AUTO_CREATE_SCHEMA path: metadata made the columns, Alembic follows."""
    from app.db import Database
    from app.models import Base

    url = f"sqlite:///{tmp_path / 'precreated2.db'}"
    database = Database(url)
    Base.metadata.create_all(database.engine)
    database.dispose()

    for revision in ("0001_create_leads", "head"):
        result = _run_alembic(url, "upgrade", revision)
        assert result.returncode == 0, f"{revision}: {result.stderr}"

    engine = sa.create_engine(url)
    try:
        columns = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    assert {"consent_given_at", "consent_version", "request_fingerprint"} <= columns


def test_0001_refuses_a_wrong_shaped_leads_table(tmp_path: Path) -> None:
    """A differently-shaped `leads` must not be silently stamped as migrated.

    The old guard returned early whenever *anything* named `leads` existed,
    which marks an unrelated table as being at this revision. Now it verifies.
    """
    url = f"sqlite:///{tmp_path / 'wrongshape.db'}"

    engine = sa.create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text("CREATE TABLE leads (lead_id TEXT PRIMARY KEY, something_else TEXT)")
            )
    finally:
        engine.dispose()

    result = _run_alembic(url, "upgrade", "head")

    assert result.returncode != 0, "must fail loudly, not stamp a foreign table"
    assert "not the table this revision creates" in (result.stderr + result.stdout)

    # Alembic creates its bookkeeping table before running anything, so the
    # meaningful assertion is that no revision was *stamped*: the database must
    # not claim to be at head.
    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        if "alembic_version" in inspector.get_table_names():
            with engine.connect() as connection:
                stamped = connection.execute(
                    sa.text("SELECT version_num FROM alembic_version")
                ).fetchall()
            assert stamped == [], f"refused migration must not stamp, got {stamped}"
    finally:
        engine.dispose()


def test_0003_adds_the_stored_response_columns(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'stepped3.db'}"

    assert _run_alembic(url, "upgrade", "0002_consent_and_fingerprint").returncode == 0

    engine = sa.create_engine(url)
    try:
        before = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    assert "response_status" not in before
    assert "response_body" not in before

    assert _run_alembic(url, "upgrade", "head").returncode == 0

    engine = sa.create_engine(url)
    try:
        after = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    assert {"response_status", "response_body"} <= after


def test_0003_is_a_no_op_when_the_columns_already_exist(tmp_path: Path) -> None:
    """The AUTO_CREATE_SCHEMA path, one revision deeper."""
    from app.db import Database
    from app.models import Base

    url = f"sqlite:///{tmp_path / 'precreated3.db'}"
    database = Database(url)
    Base.metadata.create_all(database.engine)
    database.dispose()

    for revision in ("0001_create_leads", "0002_consent_and_fingerprint", "head"):
        result = _run_alembic(url, "upgrade", revision)
        assert result.returncode == 0, f"{revision}: {result.stderr}"

    engine = sa.create_engine(url)
    try:
        columns = {c["name"] for c in sa.inspect(engine).get_columns("leads")}
    finally:
        engine.dispose()
    assert {"response_status", "response_body"} <= columns


def test_a_json_body_survives_a_migration_round_trip(tmp_path: Path) -> None:
    """The stored reply is JSON, so it must still load as a dict afterwards."""
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.db import Database
    from app.main import create_app

    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    assert _run_alembic(url, "upgrade", "head").returncode == 0

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=url,
        rate_limit_enabled=False,
        auto_create_schema=False,
        mock_provider_behaviour="duplicate",
    )
    database = Database(url)
    client = TestClient(
        create_app(settings, database=database, create_schema=False),
        raise_server_exceptions=False,
    )
    with client:
        headers = {"Idempotency-Key": "migration-round-trip"}
        body = {
            "full_name": "Nguyễn Văn A",
            "phone": "0912000011",
            "password": "secret-at-least-8",
            "email": "",
            "consent": True,
        }
        first = client.post("/api/v1/registrations", json=body, headers=headers)
        replay = client.post("/api/v1/registrations", json=body, headers=headers)

    assert first.status_code == 409, first.text
    assert replay.status_code == 409, replay.text
    assert replay.json() == first.json()


def test_every_revision_applies_one_step_at_a_time(tmp_path: Path) -> None:
    """Walk the entire migration chain, one revision per command.

    This is how a production deploy applies migrations, and it was not actually
    tested: the neighbouring test steps 0001 then jumps to head, so 0003 and 0004
    were only ever applied as part of a single jump. A migration that works in a
    jump and fails on its own is precisely the kind of thing that turns up during
    a deploy.

    The chain is derived from the script directory rather than hand-listed, so a
    new migration is covered the moment it is added instead of when someone
    remembers to extend a hard-coded list.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    script = ScriptDirectory.from_config(config)

    chain = [rev.revision for rev in script.walk_revisions()][::-1]  # base -> head
    assert len(chain) >= 4, f"expected at least four revisions, got {chain}"
    assert script.get_current_head() == chain[-1]

    url = f"sqlite:///{tmp_path / 'stepped-all.db'}"
    for revision in chain:
        result = _run_alembic(url, "upgrade", revision)
        assert result.returncode == 0, (
            f"upgrading to {revision} on its own failed:\n{result.stderr}"
        )

    # Stepping to the head again must be a no-op, not an error.
    again = _run_alembic(url, "upgrade", chain[-1])
    assert again.returncode == 0, again.stderr

    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        columns = {c["name"] for c in inspector.get_columns("leads")}
        indexes = {i["name"] for i in inspector.get_indexes("leads")}
    finally:
        engine.dispose()

    # The last migration's artefacts must be present after the walk.
    assert "in_flight_at" in columns, f"0004 did not apply; columns: {sorted(columns)}"
    assert "uq_leads_in_flight_phone" in indexes, (
        f"0004's partial index is missing; indexes: {sorted(indexes)}"
    )
    # And the earlier ones too — a later migration must not remove them.
    assert {"consent_version", "request_fingerprint", "response_body"} <= columns
    assert "uq_leads_registered_phone" in indexes
