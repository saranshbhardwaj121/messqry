"""Prevent rewriting deployment history outside application ORM operations.

Revision ID: 0006_event_immutability
Revises: 0005_artifact_metadata
Create Date: 2026-10-20
"""

from alembic import op

revision = "0006_event_immutability"
down_revision = "0005_artifact_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute(
            "CREATE TRIGGER trg_deployment_events_no_update "
            "BEFORE UPDATE ON deployment_events "
            "BEGIN SELECT RAISE(ABORT, 'deployment events are immutable'); END"
        )
        op.execute(
            "CREATE TRIGGER trg_deployment_events_no_delete "
            "BEFORE DELETE ON deployment_events "
            "BEGIN SELECT RAISE(ABORT, 'deployment events cannot be deleted'); END"
        )
    elif dialect == "postgresql":
        op.execute(
            "CREATE FUNCTION reject_deployment_event_mutation() RETURNS trigger AS $$ "
            "BEGIN RAISE EXCEPTION 'deployment events are immutable'; END; "
            "$$ LANGUAGE plpgsql"
        )
        op.execute(
            "CREATE TRIGGER trg_deployment_events_immutable "
            "BEFORE UPDATE OR DELETE ON deployment_events "
            "FOR EACH ROW EXECUTE FUNCTION reject_deployment_event_mutation()"
        )
    else:
        raise RuntimeError(f"Unsupported database dialect for audit immutability: {dialect}")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        op.execute("DROP TRIGGER trg_deployment_events_no_delete")
        op.execute("DROP TRIGGER trg_deployment_events_no_update")
    elif dialect == "postgresql":
        op.execute("DROP TRIGGER trg_deployment_events_immutable ON deployment_events")
        op.execute("DROP FUNCTION reject_deployment_event_mutation()")
