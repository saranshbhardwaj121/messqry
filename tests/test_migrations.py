import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from messqry.models import Deployment, DeploymentEvent, ModelVersion, RegisteredModel

ROOT = Path(__file__).resolve().parents[1]


def test_migration_revision_ids_fit_alembic_version_column():
    for migration_file in (ROOT / "migrations" / "versions").glob("*.py"):
        migration = runpy.run_path(str(migration_file))
        assert len(migration["revision"]) <= 32, migration_file.name
        if migration["down_revision"] is not None:
            revisions = migration["down_revision"]
            if isinstance(revisions, str):
                revisions = (revisions,)
            assert all(len(revision) <= 32 for revision in revisions), migration_file.name


def run_alembic(database_path: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = os.environ.copy()
    environment["MESSQRY_DATABASE_URL"] = f"sqlite:///{database_path.as_posix()}"
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )


def test_migrations_upgrade_downgrade_and_upgrade_again(tmp_path):
    database_path = tmp_path / "migration-test.db"
    current = run_alembic(database_path, "upgrade", "head")
    assert current.returncode == 0
    run_alembic(database_path, "check")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        inspector = inspect(engine)
        assert {
            "models",
            "model_versions",
            "deployments",
            "deployment_events",
            "deployment_requests",
            "model_evaluations",
        } <= set(inspector.get_table_names())
        deployment_columns = {column["name"] for column in inspector.get_columns("deployments")}
        assert "revision" in deployment_columns
    finally:
        engine.dispose()

    run_alembic(database_path, "downgrade", "base")
    run_alembic(database_path, "upgrade", "head")


def test_existing_model_versions_are_marked_unverified_by_artifact_migration(tmp_path):
    database_path = tmp_path / "legacy-artifacts.db"
    run_alembic(database_path, "upgrade", "0004_deployment_optimistic_lock")

    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    model_id = "00000000-0000-0000-0000-000000000001"
    version_id = "00000000-0000-0000-0000-000000000002"
    try:
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO models (id, name, created_at) VALUES (:id, :name, :created_at)"),
                {"id": model_id, "name": "legacy-model", "created_at": "2026-10-18"},
            )
            connection.execute(
                text(
                    "INSERT INTO model_versions "
                    "(id, model_id, version, artifact_uri, metrics, created_at) "
                    "VALUES (:id, :model_id, 1, :artifact_uri, '{}', :created_at)"
                ),
                {
                    "id": version_id,
                    "model_id": model_id,
                    "artifact_uri": "s3://unverified-bucket/model",
                    "created_at": "2026-10-18",
                },
            )
    finally:
        engine.dispose()

    run_alembic(database_path, "upgrade", "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with engine.connect() as connection:
            version = connection.execute(
                text(
                    "SELECT package_format, artifact_sha256, artifact_size_bytes, "
                    "artifact_version_id, artifact_manifest, validation_status "
                    "FROM model_versions WHERE id=:id"
                ),
                {"id": version_id},
            ).one()
        assert version == (None, None, None, None, None, "legacy_unverified")
    finally:
        engine.dispose()


def test_deployment_events_are_immutable_in_database(tmp_path):
    database_path = tmp_path / "immutable-deployment-events.db"
    run_alembic(database_path, "upgrade", "head")
    engine = create_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        with Session(engine) as session:
            model = RegisteredModel(name="audit-model")
            version = ModelVersion(
                model=model,
                version=1,
                artifact_uri="s3://bucket/audit-model/v1",
                metrics={},
            )
            session.add(version)
            session.flush()
            deployment = Deployment(
                model_id=model.id,
                environment="staging",
                model_version=version,
            )
            session.add(deployment)
            session.flush()
            event_record = DeploymentEvent(
                deployment_id=deployment.id,
                from_model_version_id=None,
                to_model_version_id=version.id,
                action="deploy",
            )
            session.add(event_record)
            session.commit()
            event_id = event_record.id

        with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="deployment events are immutable"):
                connection.execute(
                    text("UPDATE deployment_events SET reason='rewritten' WHERE id=:id"),
                    {"id": event_id.hex},
                )
        with engine.begin() as connection:
            with pytest.raises(DBAPIError, match="deployment events cannot be deleted"):
                connection.execute(
                    text("DELETE FROM deployment_events WHERE id=:id"),
                    {"id": event_id.hex},
                )
    finally:
        engine.dispose()
