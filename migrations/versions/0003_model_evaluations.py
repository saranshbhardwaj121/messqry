"""Add recorded metric evaluations for model versions.

Revision ID: 0003_model_evaluations
Revises: 0002_deployment_history
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_model_evaluations"
down_revision = "0002_deployment_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_version_id", sa.Uuid(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_evaluations_model_version_id",
        "model_evaluations",
        ["model_version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_model_evaluations_model_version_id", table_name="model_evaluations")
    op.drop_table("model_evaluations")
