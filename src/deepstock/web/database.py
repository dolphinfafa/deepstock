from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from deepstock.web.config import settings


class Base(DeclarativeBase):
    pass


def _create_database_engine(database_url: str) -> Engine:
    connect_args = (
        {"check_same_thread": False, "timeout": 30}
        if database_url.startswith("sqlite")
        else {}
    )
    value = create_engine(database_url, future=True, connect_args=connect_args)
    if database_url.startswith("sqlite"):

        @event.listens_for(value, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()
    return value


engine = _create_database_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def configure_database(database_url: str) -> Engine:
    """Rebind the process database explicitly (primarily for isolated tests/tools)."""
    global engine
    engine.dispose()
    engine = _create_database_engine(database_url)
    SessionLocal.configure(bind=engine)
    return engine


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
