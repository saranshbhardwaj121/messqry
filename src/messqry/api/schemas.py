"""Request and response schemas for the model registry API."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    JsonValue,
    field_validator,
    model_validator,
)


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
    package_format: str | None
    artifact_sha256: str | None
    artifact_size_bytes: int | None
    artifact_version_id: str | None
    artifact_manifest: dict[str, Any] | None
    validation_status: str
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
    override_evaluation: bool = Field(
        default=False,
        description="Bypass production evaluation gating; requires a privileged scope and reason.",
    )


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


class PredictionRequest(BaseModel):
    """A bounded batch of JSON records converted to the pyfunc tabular input."""

    model_config = ConfigDict(extra="forbid")

    inputs: list[dict[str, JsonValue]] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_records(self) -> "PredictionRequest":
        columns = set(self.inputs[0])
        if not columns or len(columns) > 256:
            raise ValueError("Prediction records must contain 1 to 256 fields")
        if any(not key.strip() or len(key) > 128 for key in columns):
            raise ValueError("Prediction field names must contain 1 to 128 characters")
        if any(set(record) != columns for record in self.inputs[1:]):
            raise ValueError("All prediction records must contain the same fields")
        return self


class PredictionResponse(BaseModel):
    """Predictions paired with the exact model version selected for the request."""

    model_id: UUID
    environment: str
    model_version: int = Field(gt=0)
    predictions: list[JsonValue] = Field(min_length=1)


class MetricOperator(str, Enum):
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"


class MetricThreshold(BaseModel):
    operator: MetricOperator
    value: FiniteFloat


class EvaluationPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    environment: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,31}$")
    version: int = Field(ge=1, le=2_147_483_647)
    thresholds: dict[str, MetricThreshold] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def validate_policy_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("Policy name must not be blank")
        return name

    @field_validator("thresholds")
    @classmethod
    def validate_policy_metric_names(cls, value):
        cleaned = {}
        for key, threshold in value.items():
            name = key.strip()
            if not name or len(name) > 100:
                raise ValueError("Metric names must contain 1 to 100 non-whitespace characters")
            if name in cleaned:
                raise ValueError("Metric names must be unique after trimming whitespace")
            cleaned[name] = threshold
        return cleaned


class EvaluationPolicyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_id: UUID
    name: str
    environment: str
    version: int
    thresholds: dict[str, MetricThreshold]
    created_at: datetime


class EvaluationCreate(BaseModel):
    """Original v1 evaluation request; kept stable for existing clients."""

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


class EvaluationCreateV2(BaseModel):
    """Provenance and policy-backed evaluation request introduced in v2."""

    dataset_name: str = Field(min_length=1, max_length=128)
    dataset_version: str = Field(min_length=1, max_length=128)
    dataset_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    evaluator_name: str = Field(min_length=1, max_length=128)
    evaluator_version: str = Field(min_length=1, max_length=128)
    policy_id: UUID
    metrics: dict[str, FiniteFloat] = Field(min_length=1)

    @field_validator("dataset_name", "dataset_version", "evaluator_name", "evaluator_version")
    @classmethod
    def validate_provenance_label(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Evaluation provenance labels must not be blank")
        return cleaned

    @field_validator("dataset_sha256")
    @classmethod
    def normalize_dataset_checksum(cls, value: str) -> str:
        return value.lower()

    @field_validator("metrics")
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


class EvaluationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_version_id: UUID
    dataset_name: str | None
    dataset_version: str | None
    dataset_sha256: str | None
    evaluator_name: str | None
    evaluator_version: str | None
    policy_id: UUID | None
    policy_name: str | None
    policy_version: int | None
    policy_environment: str | None
    metrics: dict[str, FiniteFloat]
    thresholds: dict[str, MetricThreshold]
    passed: bool
    created_at: datetime


class EvaluationReadV1(BaseModel):
    """Stable response schema for legacy v1 evaluation clients."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    model_version_id: UUID
    metrics: dict[str, FiniteFloat]
    thresholds: dict[str, MetricThreshold]
    passed: bool
    created_at: datetime
