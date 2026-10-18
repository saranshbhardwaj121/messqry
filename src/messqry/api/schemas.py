"""Request and response schemas for the model registry API."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
