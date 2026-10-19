"""Add optimistic concurrency control to deployments.

Revision ID: 0004_deployment_optimistic_lock
Revises: 0003_model_evaluations
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_deployment_optimistic_lock"
down_revision = "0003_model_evaluations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("deployments", "revision")
