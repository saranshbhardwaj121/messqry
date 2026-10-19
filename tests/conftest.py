from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from messqry import models  # noqa: F401
from messqry.db import Base


@pytest.fixture
def session_factory(tmp_path) -> Generator[sessionmaker[Session], None, None]:
    database_path = tmp_path / "api-test.db"
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        yield test_sessions
    finally:
        engine.dispose()
