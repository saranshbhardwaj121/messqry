import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

ROOT = Path(__file__).resolve().parents[1]


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
            "model_evaluations",
        } <= set(inspector.get_table_names())
        deployment_columns = {column["name"] for column in inspector.get_columns("deployments")}
        assert "revision" in deployment_columns
    finally:
        engine.dispose()

    run_alembic(database_path, "downgrade", "base")
    run_alembic(database_path, "upgrade", "head")
