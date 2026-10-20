"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    """Runtime configuration for the MessQry API."""

    app_name: str = "MessQry AI"
    environment: Literal["development", "test", "staging", "production"] = "development"
    docs_enabled: bool = True
    database_url: SecretStr = SecretStr("sqlite:///./messqry.db")
    artifact_storage: Literal["local", "s3"] = "local"
    artifact_local_root: Path = Path("./artifacts")
    artifact_s3_bucket: str | None = None
    artifact_s3_prefix: str = "messqry"
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_service_client_ids: str = ""
    auth_dev_bypass: bool = True
    runtime_backend: Literal["disabled", "ecs_fargate"] = "disabled"
    aws_region: str | None = None
    ecs_cluster: str | None = None
    ecs_task_definition: str | None = None
    ecs_container_name: str | None = None
    ecs_subnet_ids: str = ""
    ecs_security_group_ids: str = ""
    runtime_port: int = Field(default=8000, ge=1, le=65535)
    runtime_startup_timeout_seconds: int = Field(default=60, ge=1, le=60)
    runtime_idle_timeout_seconds: int = Field(default=300, ge=1, le=3600)

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

        if self.artifact_storage == "s3" and not (self.artifact_s3_bucket or "").strip():
            raise ValueError("MESSQRY_ARTIFACT_S3_BUCKET is required when S3 storage is selected")

        if self.runtime_backend == "ecs_fargate":
            required_ecs_values = {
                "MESSQRY_AWS_REGION": self.aws_region,
                "MESSQRY_ECS_CLUSTER": self.ecs_cluster,
                "MESSQRY_ECS_TASK_DEFINITION": self.ecs_task_definition,
                "MESSQRY_ECS_CONTAINER_NAME": self.ecs_container_name,
                "MESSQRY_ECS_SUBNET_IDS": ",".join(self.ecs_subnets),
                "MESSQRY_ECS_SECURITY_GROUP_IDS": ",".join(self.ecs_security_groups),
            }
            missing_ecs_values = [
                name for name, value in required_ecs_values.items() if not (value or "").strip()
            ]
            if missing_ecs_values:
                raise ValueError("ECS Fargate runtime requires " + ", ".join(missing_ecs_values))
            if len(self.ecs_subnets) > 16:
                raise ValueError("MESSQRY_ECS_SUBNET_IDS supports at most 16 subnets")
            if len(self.ecs_security_groups) > 5:
                raise ValueError("MESSQRY_ECS_SECURITY_GROUP_IDS supports at most 5 groups")
            if self.artifact_storage != "s3":
                raise ValueError("ECS Fargate model runtimes require private S3 artifact storage")

        if self.environment in {"staging", "production"}:
            if parsed_database_url.get_backend_name() != "postgresql":
                raise ValueError("Staging and production require a PostgreSQL database")
            if parsed_database_url.drivername != "postgresql+psycopg":
                raise ValueError("Staging and production require the psycopg PostgreSQL driver")
            if self.artifact_storage != "s3":
                raise ValueError("Staging and production require private S3 artifact storage")
            if not self.oidc_issuer or not self.oidc_issuer.startswith("https://"):
                raise ValueError("Staging and production require an HTTPS MESSQRY_OIDC_ISSUER")
            if not (self.oidc_audience or "").strip():
                raise ValueError("Staging and production require MESSQRY_OIDC_AUDIENCE")
            if not self.service_client_ids:
                raise ValueError("Staging and production require MESSQRY_OIDC_SERVICE_CLIENT_IDS")
        if self.environment == "production" and self.docs_enabled:
            raise ValueError("MESSQRY_DOCS_ENABLED must be false in production")
        if self.auth_dev_bypass and self.environment != "development":
            raise ValueError("MESSQRY_AUTH_DEV_BYPASS is allowed only in development")
        return self

    @property
    def service_client_ids(self) -> frozenset[str]:
        """Return configured comma-separated Cognito machine-client IDs."""

        return frozenset(
            client_id.strip()
            for client_id in self.oidc_service_client_ids.split(",")
            if client_id.strip()
        )

    @property
    def ecs_subnets(self) -> tuple[str, ...]:
        """Return configured private subnet IDs."""

        return tuple(item.strip() for item in self.ecs_subnet_ids.split(",") if item.strip())

    @property
    def ecs_security_groups(self) -> tuple[str, ...]:
        """Return configured task security-group IDs."""

        return tuple(
            item.strip() for item in self.ecs_security_group_ids.split(",") if item.strip()
        )


@lru_cache
def get_settings() -> Settings:
    """Return one cached settings instance for the running process."""

    return Settings()
