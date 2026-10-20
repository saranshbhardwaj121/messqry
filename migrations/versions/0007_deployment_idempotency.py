"""Persist deployment mutation results for idempotent retries.

Revision ID: 0007_deployment_idempotency
Revises: 0006_event_immutability
Create Date: 2026-10-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_deployment_idempotency"
down_revision = "0006_event_immutability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deployment_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response_snapshot", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_id", "environment", "idempotency_key", name="uq_deployment_request_key"
        ),
    )
    op.create_index(op.f("ix_deployment_requests_model_id"), "deployment_requests", ["model_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_deployment_requests_model_id"), table_name="deployment_requests")
    op.drop_table("deployment_requests")
