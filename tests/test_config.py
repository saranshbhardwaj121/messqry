import pytest
from pydantic import ValidationError

from messqry import db
from messqry.config import Settings

PRODUCTION_OIDC = {
    "oidc_issuer": "https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
    "oidc_audience": "messqry-web-client",
    "oidc_service_client_ids": "messqry-worker-client",
    "auth_dev_bypass": False,
}


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
            artifact_storage="s3",
            artifact_s3_bucket="messqry-production",
        )

    with pytest.raises(ValidationError, match="require private S3 artifact storage"):
        Settings(
            environment="staging",
            database_url="postgresql+psycopg://messqry:secret@db/messqry",
        )

    with pytest.raises(ValidationError, match="MESSQRY_ECS_SUBNET_IDS"):
        Settings(
            runtime_backend="ecs_fargate",
            artifact_storage="s3",
            artifact_s3_bucket="messqry-artifacts",
            ecs_cluster="cluster",
            ecs_task_definition="runtime:1",
            ecs_container_name="model-runtime",
            ecs_subnet_ids=" , ",
            ecs_security_group_ids="sg-runtime",
        )

    with pytest.raises(ValidationError, match="MESSQRY_ARTIFACT_S3_BUCKET is required"):
        Settings(
            environment="production",
            docs_enabled=False,
            database_url="postgresql+psycopg://messqry:secret@db/messqry",
            artifact_storage="s3",
        )

    with pytest.raises(ValidationError, match="must be false in production"):
        Settings(
            environment="production",
            docs_enabled=True,
            database_url="postgresql+psycopg://messqry:secret@db/messqry",
            artifact_storage="s3",
            artifact_s3_bucket="messqry-production",
            **PRODUCTION_OIDC,
        )

    settings = Settings(
        environment="production",
        docs_enabled=False,
        database_url="postgresql+psycopg://messqry:secret@db/messqry",
        artifact_storage="s3",
        artifact_s3_bucket="messqry-production",
        **PRODUCTION_OIDC,
    )
    assert settings.environment == "production"
    assert settings.docs_enabled is False


def test_production_requires_cognito_and_rejects_development_bypass():
    with pytest.raises(ValidationError, match="MESSQRY_OIDC_ISSUER"):
        Settings(
            environment="production",
            docs_enabled=False,
            database_url="postgresql+psycopg://messqry:secret@db/messqry",
            artifact_storage="s3",
            artifact_s3_bucket="messqry-production",
        )

    with pytest.raises(ValidationError, match="only in development"):
        Settings(
            environment="production",
            docs_enabled=False,
            database_url="postgresql+psycopg://messqry:secret@db/messqry",
            artifact_storage="s3",
            artifact_s3_bucket="messqry-production",
            **(PRODUCTION_OIDC | {"auth_dev_bypass": True}),
        )


def test_ecs_runtime_requires_private_network_and_s3_configuration():
    with pytest.raises(ValidationError, match="ECS Fargate runtime requires"):
        Settings(runtime_backend="ecs_fargate")

    with pytest.raises(ValidationError, match="require private S3 artifact storage"):
        Settings(
            runtime_backend="ecs_fargate",
            aws_region="us-east-1",
            ecs_cluster="cluster",
            ecs_task_definition="runtime:1",
            ecs_container_name="model-runtime",
            ecs_subnet_ids="subnet-private-a",
            ecs_security_group_ids="sg-runtime",
        )

    settings = Settings(
        runtime_backend="ecs_fargate",
        aws_region="us-east-1",
        artifact_storage="s3",
        artifact_s3_bucket="messqry-artifacts",
        ecs_cluster="cluster",
        ecs_task_definition="runtime:1",
        ecs_container_name="model-runtime",
        ecs_subnet_ids="subnet-private-a, subnet-private-b",
        ecs_security_group_ids="sg-runtime, sg-endpoints",
    )
    assert settings.ecs_subnets == ("subnet-private-a", "subnet-private-b")
    assert settings.ecs_security_groups == ("sg-runtime", "sg-endpoints")

    with pytest.raises(ValidationError, match="runtime_startup_timeout_seconds"):
        Settings(runtime_startup_timeout_seconds=61)


def test_database_dispose_closes_the_engine_pool(monkeypatch):
    disposed = False

    def mark_disposed():
        nonlocal disposed
        disposed = True

    monkeypatch.setattr(db.engine, "dispose", mark_disposed)
    db.dispose_engine()
    assert disposed is True
