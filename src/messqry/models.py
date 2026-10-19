"""SQLAlchemy entities for model registration and version history."""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, text
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
    __table_args__ = (UniqueConstraint("model_id", "version", name="uq_model_version_number"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("models.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    artifact_uri: Mapped[str] = mapped_column(String(2048))
    framework: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    model: Mapped[RegisteredModel] = relationship(back_populates="versions")


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


class ModelEvaluation(Base):
    """Recorded metric checks for one registered model version."""

    __tablename__ = "model_evaluations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    model_version_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("model_versions.id", ondelete="CASCADE"), index=True
    )
    metrics: Mapped[dict[str, float]] = mapped_column(JSON)
    thresholds: Mapped[dict[str, dict[str, Any]]] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
