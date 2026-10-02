"""G14 — prove the engine, do not infer it from the command name.

A previous verification reported a green "SQLite" run that was in fact
PostgreSQL, because an exported ``TEST_DATABASE_URL``/``DATABASE_URL`` outlived
the command that set it. The suite said SQLite in the filename and PostgreSQL in
the engine.

These tests make the engine a **claimed fact** that fails loudly when wrong,
instead of an assumption a reader has to trust.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from tests.conftest import _is_postgres, _scheme


def test_the_default_suite_is_not_silently_postgres() -> None:
    """If this is meant to be the SQLite run, the engine must actually be SQLite.

    Skipped (not failed) when ``TEST_DATABASE_URL`` is set, because then this IS
    the PostgreSQL run and the next test covers it.
    """
    if _is_postgres(os.environ.get("TEST_DATABASE_URL", "")):
        pytest.skip("this is the PostgreSQL run — see the next test")
    ambient = os.environ.get("DATABASE_URL", "")
    assert not _is_postgres(ambient), (
        f"a run with no TEST_DATABASE_URL used DATABASE_URL={ambient!r}, which is "
        f"PostgreSQL. This result must NOT be reported as a SQLite result."
    )


def test_the_live_engine_matches_the_declared_one(harness) -> None:
    """Ask the CONNECTION what it is, rather than trusting the configured URL."""
    engine = harness.database.engine
    dialect = engine.dialect.name

    if _is_postgres(os.environ.get("TEST_DATABASE_URL", "")):
        assert dialect == "postgresql", f"declared PostgreSQL, engine says {dialect!r}"
        version = engine.connect().execute(text("SELECT version()")).scalar_one()
        assert "PostgreSQL" in version, version
    else:
        assert dialect == "sqlite", f"declared SQLite, engine says {dialect!r}"
        # The file form, not `:memory:` — an in-memory database would make the
        # concurrency and partial-index assertions meaningless across connections.
        with engine.connect() as conn:
            assert conn.execute(text("PRAGMA journal_mode")).scalar_one() is not None
            assert conn.execute(text("SELECT sqlite_version()")).scalar_one()


def test_the_url_scheme_is_one_the_application_supports() -> None:
    """Catch a typo'd scheme before it becomes `create_engine`'s problem."""
    for var in ("DATABASE_URL", "TEST_DATABASE_URL"):
        url = os.environ.get(var, "")
        if url:
            assert _scheme(url) in (
                "sqlite",
                "postgresql",
                "postgresql+psycopg",
            ), f"{var}={url!r} has an unsupported scheme"
