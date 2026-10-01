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

EXPECTED_COLUMNS = {
    "lead_id",
    "lead_type",
    "registration_status",
    "full_name",
    "phone",
    "phone_display",
    "email",
    "province",
    "service_interest",
    "source",
    "medium",
    "campaign",
    "content",
    "term",
    "landing_page",
    "referrer",
    "external_customer_id",
    "external_customer_code",
    "idempotency_key",
    "tracking_token",
    "attempt_count",
    "last_error_code",
    "last_error_message",
    "created_at",
    "updated_at",
}

EXPECTED_INDEXES = {
    "ix_leads_phone",
    "ix_leads_registration_status",
    "ix_leads_created_at",
    "uq_leads_idempotency_key",
    "uq_leads_tracking_token",
    "uq_leads_registered_phone",
}


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
    assert script.get_current_head() == "0001_create_leads"
