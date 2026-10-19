"""Application settings loaded from environment variables."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    """Runtime configuration for the MessQry API."""

    app_name: str = "MessQry AI"
    environment: Literal["development", "test", "staging", "production"] = "development"
    docs_enabled: bool = True
    database_url: SecretStr = SecretStr("sqlite:///./messqry.db")

    model_config = SettingsConfigDict(
        env_prefix="MESSQRY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    @model_validator(mode="after")
    def validate_runtime_configuration(self) -> "Settings":
        database_url = self.database_url.get_secret_value()
        try:
            parsed_database_url = make_url(database_url)
        except (ArgumentError, TypeError, ValueError) as exc:
            raise ValueError("MESSQRY_DATABASE_URL must be a valid SQLAlchemy URL") from exc

        if self.environment in {"staging", "production"}:
            if parsed_database_url.get_backend_name() != "postgresql":
                raise ValueError("Staging and production require a PostgreSQL database")
            if parsed_database_url.drivername != "postgresql+psycopg":
                raise ValueError("Staging and production require the psycopg PostgreSQL driver")
        if self.environment == "production" and self.docs_enabled:
            raise ValueError("MESSQRY_DOCS_ENABLED must be false in production")
        return self


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the running process."""

    return Settings()
