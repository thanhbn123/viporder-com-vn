"""Database wiring.

``DATABASE_URL`` selects the backend. The dev default is a SQLite file under
``var/`` — created on demand and git-ignored — while production uses
``postgresql+psycopg://...``. The same SQLAlchemy models and the same Alembic
migration serve both.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import Settings

_SELECT_1 = text("SELECT 1")


def _prepare_sqlite_path(database_url: str) -> None:
    """Create the directory for a file-backed SQLite URL if it is missing."""
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return
    raw_path = database_url[len(prefix) :]
    if not raw_path or raw_path == ":memory:" or raw_path.startswith("file:"):
        return
    Path(raw_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)


def make_engine(database_url: str, *, echo: bool = False) -> Engine:
    _prepare_sqlite_path(database_url)
    kwargs: dict[str, object] = {"echo": echo, "future": True}
    if database_url.startswith("sqlite"):
        # TestClient / FastAPI run the app across threads.
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            # Foreign keys are not enforced by default in SQLite.
            cursor.execute("PRAGMA foreign_keys=ON")
            # Write-ahead logging keeps readers from blocking on a writer.
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


class Database:
    """Owns the engine and hands out sessions."""

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        self.database_url = database_url
        self.engine = make_engine(database_url, echo=echo)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    @classmethod
    def from_settings(cls, settings: Settings, *, echo: bool = False) -> Database:
        return cls(settings.database_url, echo=echo)

    def create_all(self) -> None:
        """Create tables directly. Alembic owns schema in real environments."""
        from .models import Base

        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        from .models import Base

        Base.metadata.drop_all(self.engine)

    def session(self) -> Session:
        return self.session_factory()

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def is_healthy(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(_SELECT_1)
            return True
        except Exception:
            return False

    def dispose(self) -> None:
        self.engine.dispose()
