"""SQLAlchemy entities for model registration and version history."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from messqry.db import Base


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class RegisteredModel(Base):
    """A logical model tracked by MessQry."""

    __tablename__ = "models"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    versions: Mapped[list["ModelVersion"]] = relationship(
        back_populates="model", cascade="all, delete-orphan", order_by="ModelVersion.version"
    )


class ModelVersion(Base):
    """Immutable registration metadata for one model artifact version."""

    __tablename__ = "model_versions"
    __table_args__ = (
        UniqueConstraint("model_id", "version", name="uq_model_version_number"),
        CheckConstraint(
            "artifact_size_bytes IS NULL OR artifact_size_bytes > 0",
            name="ck_model_version_artifact_size_positive",
        ),
        CheckConstraint(
            "validation_status IN ('legacy_unverified', 'validated')",
            name="ck_model_version_validation_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    artifact_uri: Mapped[str] = mapped_column(String(2048))
    package_format: Mapped[str | None] = mapped_column(String(40), nullable=True)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    artifact_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    artifact_version_id: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    artifact_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    validation_status: Mapped[str] = mapped_column(
        String(24),
        default="legacy_unverified",
        server_default=text("'legacy_unverified'"),
    )
    framework: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    model: Mapped[RegisteredModel] = relationship(back_populates="versions")


_IMMUTABLE_VERSION_FIELDS = (
    "model_id",
    "version",
    "artifact_uri",
    "package_format",
    "artifact_sha256",
    "artifact_size_bytes",
    "artifact_version_id",
    "artifact_manifest",
    "validation_status",
    "framework",
    "metrics",
    "created_at",
)


@event.listens_for(ModelVersion, "before_update")
def prevent_model_version_update(mapper, connection, target: ModelVersion) -> None:
    """Keep registered version metadata append-only through ORM operations."""

    state = inspect(target)
    if any(state.attrs[name].history.has_changes() for name in _IMMUTABLE_VERSION_FIELDS):
        raise ValueError("Registered model versions are immutable; register a new version instead")


@event.listens_for(ModelVersion, "before_delete")
def prevent_model_version_delete(mapper, connection, target: ModelVersion) -> None:
    """Keep registered version records available for audit and deployment history."""

    raise ValueError("Registered model versions cannot be deleted")


class Deployment(Base):
    """The version currently selected for a model in one named environment."""

    __tablename__ = "deployments"
    __table_args__ = (
        UniqueConstraint("model_id", "environment", name="uq_deployment_model_environment"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    environment: Mapped[str] = mapped_column(String(32))
    model_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    model_version: Mapped[ModelVersion] = relationship()

    __mapper_args__ = {"version_id_col": revision}


class DeploymentEvent(Base):
    """Append-only history of deployment selection changes."""

    __tablename__ = "deployment_events"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    deployment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    from_model_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=True
    )
    to_model_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("model_versions.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DeploymentRequest(Base):
    """Persist idempotency results for deployment mutations when callers supply a key."""

    __tablename__ = "deployment_requests"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "environment", "idempotency_key", name="uq_deployment_request_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    environment: Mapped[str] = mapped_column(String(32))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)


@event.listens_for(DeploymentEvent, "before_update")
def prevent_deployment_event_update(mapper, connection, target: DeploymentEvent) -> None:
    """Keep deployment audit entries append-only through ORM operations."""

    raise ValueError("Deployment events are immutable; record a new event instead")


@event.listens_for(DeploymentEvent, "before_delete")
def prevent_deployment_event_delete(mapper, connection, target: DeploymentEvent) -> None:
    """Prevent deleting an individual deployment audit entry through the ORM."""

    raise ValueError("Deployment events cannot be deleted")


class ModelEvaluation(Base):
    """Recorded metric checks for one registered model version."""

    __tablename__ = "model_evaluations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("model_versions.id", ondelete="CASCADE"), index=True
    )
    # Nullable for records created before provenance became part of the evaluation contract.
    dataset_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dataset_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evaluator_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    evaluator_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("evaluation_policies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    policy_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_environment: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metrics: Mapped[dict[str, float]] = mapped_column(JSON)
    thresholds: Mapped[dict[str, dict[str, Any]]] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvaluationPolicy(Base):
    """Immutable, versioned threshold rules for one model/environment."""

    __tablename__ = "evaluation_policies"
    __table_args__ = (
        UniqueConstraint(
            "model_id", "environment", "name", "version", name="uq_evaluation_policy_version"
        ),
        CheckConstraint(
            "version >= 1 AND version <= 2147483647",
            name="ck_evaluation_policy_version_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    environment: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    thresholds: Mapped[dict[str, dict[str, Any]]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@event.listens_for(EvaluationPolicy, "before_update")
def prevent_evaluation_policy_update(mapper, connection, target: EvaluationPolicy) -> None:
    """Keep a published evaluation policy version immutable."""

    raise ValueError("Evaluation policies are immutable; create a new version instead")


@event.listens_for(EvaluationPolicy, "before_delete")
def prevent_evaluation_policy_delete(mapper, connection, target: EvaluationPolicy) -> None:
    """Keep policy versions available for historical evaluation records."""

    raise ValueError("Evaluation policies cannot be deleted")
