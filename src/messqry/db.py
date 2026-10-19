"""Database engine and session dependency."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from messqry.config import get_settings


class Base(DeclarativeBase):
    """Base class for persisted platform entities."""


def _build_engine():
    database_url = get_settings().database_url.get_secret_value()
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, pool_pre_ping=True)


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def dispose_engine() -> None:
    """Close pooled database connections during application shutdown."""

    engine.dispose()


def get_session() -> Generator[Session, None, None]:
    """Provide a short-lived session for one request."""

    with SessionLocal() as session:
        yield session
