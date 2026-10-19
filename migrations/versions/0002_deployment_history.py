"""Add deployment selection and change history.

Revision ID: 0002_deployment_history
Revises: 0001_model_registry
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_deployment_history"
down_revision = "0001_model_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("model_id", "environment", name="uq_deployment_model_environment"),
    )
    op.create_index("ix_deployments_model_id", "deployments", ["model_id"], unique=False)
    op.create_table(
        "deployment_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("deployment_id", sa.Uuid(), nullable=False),
        sa.Column("from_model_version_id", sa.Uuid(), nullable=True),
        sa.Column("to_model_version_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["from_model_version_id"], ["model_versions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_model_version_id"], ["model_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_deployment_events_deployment_id", "deployment_events", ["deployment_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_deployment_events_deployment_id", table_name="deployment_events")
    op.drop_table("deployment_events")
    op.drop_index("ix_deployments_model_id", table_name="deployments")
    op.drop_table("deployments")
