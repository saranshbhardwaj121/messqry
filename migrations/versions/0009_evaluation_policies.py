"""Store immutable, versioned evaluation policies.

Revision ID: 0009_evaluation_policies
Revises: 0008_evaluation_provenance
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_evaluation_policies"
down_revision = "0008_evaluation_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evaluation_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("model_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "version >= 1 AND version <= 2147483647",
            name="ck_evaluation_policy_version_range",
        ),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "model_id", "environment", "name", "version", name="uq_evaluation_policy_version"
        ),
    )
    op.create_index("ix_evaluation_policies_model_id", "evaluation_policies", ["model_id"])

    with op.batch_alter_table("model_evaluations") as batch_op:
        batch_op.add_column(sa.Column("policy_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("policy_name", sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column("policy_version", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("policy_environment", sa.String(length=32), nullable=True))
        batch_op.create_foreign_key(
            "fk_model_evaluations_policy_id_evaluation_policies",
            "evaluation_policies",
            ["policy_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index("ix_model_evaluations_policy_id", "model_evaluations", ["policy_id"])


def downgrade() -> None:
    op.drop_index("ix_model_evaluations_policy_id", table_name="model_evaluations")
    with op.batch_alter_table("model_evaluations") as batch_op:
        batch_op.drop_constraint(
            "fk_model_evaluations_policy_id_evaluation_policies", type_="foreignkey"
        )
        batch_op.drop_column("policy_environment")
        batch_op.drop_column("policy_version")
        batch_op.drop_column("policy_name")
        batch_op.drop_column("policy_id")
    op.drop_index("ix_evaluation_policies_model_id", table_name="evaluation_policies")
    op.drop_table("evaluation_policies")
