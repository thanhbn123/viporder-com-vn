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

from tests import conftest
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


# ---------------------------------------------------------------------------
# G14 — the ENGINE BANNER is part of the evidence, so it is tested too
# ---------------------------------------------------------------------------


def _banner() -> str:
    """The banner exactly as a run emits it. `config` is unused by the hook."""
    return conftest.pytest_report_collectionfinish(None, [])  # type: ignore[arg-type]


def test_the_banner_reports_sqlite_when_the_harness_builds_sqlite(monkeypatch) -> None:
    """The banner is WRONG in both directions if it reads DATABASE_URL.

    MEASURED before the fix: with ``DATABASE_URL=<postgres>`` and
    ``TEST_DATABASE_URL`` unset, the banner said "default suite engine =
    postgresql" while every `harness` test in that run built SQLite — and
    ``-q``, which is how every documented and CI run is invoked, suppressed the
    banner entirely. This test fails if the banner goes back to reading the
    ambient URL, and it fails if the banner stops being emitted at all.
    """
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u@127.0.0.1:5432/db")

    assert conftest._suite_engine() == "sqlite"
    banner = _banner()
    assert "default suite engine = sqlite" in banner, banner
    assert "PostgreSQL-only tests = SKIPPED" in banner, banner
    # The ambient URL is still REPORTED — it is real, and it is what the
    # module-level app was built from; it just must not be called the suite engine.
    assert "DATABASE_URL         = postgresql+psycopg://u@127.0.0.1:5432/db" in banner, banner
    assert "WARNING" in banner, banner


def test_the_banner_reports_postgresql_when_the_harness_builds_postgresql(monkeypatch) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://u@127.0.0.1:5432/viporder_test")
    assert conftest._suite_engine() == "postgresql"
    banner = _banner()
    assert "default suite engine = postgresql" in banner, banner
    assert "PostgreSQL-only tests = RUN" in banner, banner


def test_the_banner_never_prints_a_password() -> None:
    """A DSN is a credential, and the banner is printed into CI logs."""
    url = "postgresql+psycopg://viporder:hunter2-not-a-real-secret@127.0.0.1:5432/db"
    assert conftest._redact_url(url) == ("postgresql+psycopg://viporder:***@127.0.0.1:5432/db")
    # Also through the real banner path.
    import os as _os

    original = _os.environ.get("TEST_DATABASE_URL")
    _os.environ["TEST_DATABASE_URL"] = url
    try:
        assert "hunter2-not-a-real-secret" not in _banner()
    finally:
        if original is None:
            _os.environ.pop("TEST_DATABASE_URL", None)
        else:
            _os.environ["TEST_DATABASE_URL"] = original
