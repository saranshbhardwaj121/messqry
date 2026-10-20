"""Add artifact identity and validation metadata to model versions.

Revision ID: 0005_artifact_metadata
Revises: 0004_deployment_optimistic_lock
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_artifact_metadata"
down_revision = "0004_deployment_optimistic_lock"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("model_versions", sa.Column("package_format", sa.String(length=40)))
    op.add_column("model_versions", sa.Column("artifact_sha256", sa.String(length=64)))
    op.add_column("model_versions", sa.Column("artifact_size_bytes", sa.Integer()))
    op.add_column("model_versions", sa.Column("artifact_version_id", sa.String(length=1024)))
    op.add_column("model_versions", sa.Column("artifact_manifest", sa.JSON()))
    op.add_column(
        "model_versions",
        sa.Column(
            "validation_status",
            sa.String(length=24),
            server_default=sa.text("'legacy_unverified'"),
            nullable=False,
        ),
    )
    with op.batch_alter_table("model_versions") as batch_op:
        batch_op.create_check_constraint(
            "ck_model_version_artifact_size_positive",
            "artifact_size_bytes IS NULL OR artifact_size_bytes > 0",
        )
        batch_op.create_check_constraint(
            "ck_model_version_validation_status",
            "validation_status IN ('legacy_unverified', 'validated')",
        )


def downgrade() -> None:
    with op.batch_alter_table("model_versions") as batch_op:
        batch_op.drop_constraint("ck_model_version_validation_status", type_="check")
        batch_op.drop_constraint("ck_model_version_artifact_size_positive", type_="check")
    op.drop_column("model_versions", "validation_status")
    op.drop_column("model_versions", "artifact_manifest")
    op.drop_column("model_versions", "artifact_version_id")
    op.drop_column("model_versions", "artifact_size_bytes")
    op.drop_column("model_versions", "artifact_sha256")
    op.drop_column("model_versions", "package_format")
