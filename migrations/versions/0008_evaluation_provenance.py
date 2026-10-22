"""Add dataset and evaluator provenance to evaluations.

Revision ID: 0008_evaluation_provenance
Revises: 0007_deployment_idempotency
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_evaluation_provenance"
down_revision = "0007_deployment_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_evaluations", sa.Column("dataset_name", sa.String(128), nullable=True))
    op.add_column("model_evaluations", sa.Column("dataset_version", sa.String(128), nullable=True))
    op.add_column("model_evaluations", sa.Column("dataset_sha256", sa.String(64), nullable=True))
    op.add_column("model_evaluations", sa.Column("evaluator_name", sa.String(128), nullable=True))
    op.add_column(
        "model_evaluations", sa.Column("evaluator_version", sa.String(128), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("model_evaluations", "evaluator_version")
    op.drop_column("model_evaluations", "evaluator_name")
    op.drop_column("model_evaluations", "dataset_sha256")
    op.drop_column("model_evaluations", "dataset_version")
    op.drop_column("model_evaluations", "dataset_name")
