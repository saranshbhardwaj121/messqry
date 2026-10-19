"""Request and response schemas for the model registry API."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator, model_validator


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Model name cannot be blank")
        return value


class ModelVersionCreate(BaseModel):
    artifact_uri: str = Field(min_length=1, max_length=2048)
    framework: str | None = Field(default=None, max_length=80)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_uri")
    @classmethod
    def strip_artifact_uri(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Artifact URI cannot be blank")
        return value


class ModelVersionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_id: UUID
    version: int
    artifact_uri: str
    framework: str | None
    metrics: dict[str, Any]
    created_at: datetime


class ModelRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    created_at: datetime


class ModelDetail(ModelRead):
    versions: list[ModelVersionRead]


class DeploymentChange(BaseModel):
    version: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=1000)


class DeploymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_id: UUID
    environment: str
    model_version: ModelVersionRead
    updated_at: datetime


class DeploymentEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deployment_id: UUID
    from_model_version_id: UUID | None
    to_model_version_id: UUID
    action: str
    reason: str | None
    created_at: datetime


class MetricOperator(str, Enum):
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"


class MetricThreshold(BaseModel):
    operator: MetricOperator
    value: FiniteFloat


class EvaluationCreate(BaseModel):
    metrics: dict[str, FiniteFloat] = Field(min_length=1)
    thresholds: dict[str, MetricThreshold] = Field(min_length=1)

    @field_validator("metrics", "thresholds")
    @classmethod
    def validate_metric_names(cls, value):
        cleaned = {}
        for key, metric_value in value.items():
            name = key.strip()
            if not name or len(name) > 100:
                raise ValueError("Metric names must contain 1 to 100 non-whitespace characters")
            if name in cleaned:
                raise ValueError("Metric names must be unique after trimming whitespace")
            cleaned[name] = metric_value
        return cleaned

    @model_validator(mode="after")
    def validate_threshold_metrics(self):
        missing_metrics = set(self.thresholds) - set(self.metrics)
        if missing_metrics:
            missing = ", ".join(sorted(missing_metrics))
            raise ValueError(f"Thresholds reference metrics that were not supplied: {missing}")
        return self


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_version_id: UUID
    metrics: dict[str, FiniteFloat]
    thresholds: dict[str, MetricThreshold]
    passed: bool
    created_at: datetime
