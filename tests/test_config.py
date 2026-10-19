import pytest
from pydantic import ValidationError

from messqry import db
from messqry.config import Settings


def test_database_url_is_masked_from_settings_repr_and_errors():
    secret = "top-secret-password"
    settings = Settings(database_url=f"postgresql+psycopg://messqry:{secret}@db/messqry")
    assert secret not in repr(settings)
    assert secret not in str(settings)

    with pytest.raises(ValidationError) as invalid:
        Settings(
            environment="production",
            docs_enabled=False,
            database_url="sqlite:///private-path.db",
        )
    assert secret not in str(invalid.value)
    assert "private-path.db" not in str(invalid.value)


def test_production_configuration_requires_postgresql_and_disables_docs():
    with pytest.raises(ValidationError, match="require a PostgreSQL database"):
        Settings(
            environment="production",
            docs_enabled=False,
            database_url="sqlite:///./messqry.db",
        )

    with pytest.raises(ValidationError, match="require the psycopg PostgreSQL driver"):
        Settings(
            environment="production",
            docs_enabled=False,
            database_url="postgresql://messqry:secret@db/messqry",
        )

    with pytest.raises(ValidationError, match="must be false in production"):
        Settings(
            environment="production",
            docs_enabled=True,
            database_url="postgresql+psycopg://messqry:secret@db/messqry",
        )

    settings = Settings(
        environment="production",
        docs_enabled=False,
        database_url="postgresql+psycopg://messqry:secret@db/messqry",
    )
    assert settings.environment == "production"
    assert settings.docs_enabled is False


def test_database_dispose_closes_the_engine_pool(monkeypatch):
    disposed = False

    def mark_disposed():
        nonlocal disposed
        disposed = True

    monkeypatch.setattr(db.engine, "dispose", mark_disposed)
    db.dispose_engine()
    assert disposed is True
