"""HTTP routes for the initial API surface."""

import logging
from http import HTTPStatus
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, StreamingResponse

from messqry.api.schemas import (
    ModelCreate,
    ModelDetail,
    ModelRead,
    ModelVersionCreate,
    ModelVersionRead,
)
from messqry.api.security import Principal, require_scope
from messqry.artifact_storage import ArtifactStore
from messqry.db import get_session
from messqry.models import ModelVersion, RegisteredModel
from messqry.package_validation import (
    PackageLimits,
    PackageValidationError,
    validate_mlflow_pyfunc_package,
)

router = APIRouter()
logger = logging.getLogger("messqry.api.artifacts")


class HealthResponse(BaseModel):
    """Liveness response for the API process."""

    status: str


class ReadinessResponse(BaseModel):
    """Readiness response including required service dependencies."""

    status: str
    database: str


@router.get("/health", response_model=HealthResponse, tags=["operations"])
def health_check() -> HealthResponse:
    """Report that the API process is accepting requests."""

    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ReadinessResponse}},
    tags=["operations"],
)
def readiness_check(
    session: Session = Depends(get_session),
) -> ReadinessResponse | JSONResponse:
    """Check whether the API can reach its configured database."""

    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        session.rollback()
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=ReadinessResponse(status="not_ready", database="unavailable").model_dump(),
        )
    return ReadinessResponse(status="ready", database="ok")


models_router = APIRouter(prefix="/api/v1/models", tags=["models"])


def get_artifact_store(request: Request) -> ArtifactStore:
    """Return the storage adapter initialized by the application lifespan."""

    store = getattr(request.app.state, "artifact_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact storage is unavailable",
        )
    return store


def _artifact_key(model_id: UUID, version_id: UUID) -> str:
    return f"models/{model_id}/versions/{version_id}/package.zip"


def _artifact_chunks(artifact):
    try:
        while chunk := artifact.read(1024 * 1024):
            yield chunk
    finally:
        artifact.close()


def _cleanup_unregistered_artifact(store: ArtifactStore, key: str, version_id: str | None) -> None:
    try:
        store.delete(key, version_id=version_id)
    except Exception as exc:
        logger.warning("Unregistered artifact cleanup failed (%s)", type(exc).__name__)


@models_router.post(
    "/{model_id}/versions/artifact",
    response_model=ModelVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def upload_model_artifact(
    model_id: UUID,
    file: UploadFile = File(...),
    framework: Annotated[str | None, Form(max_length=80)] = None,
    session: Session = Depends(get_session),
    store: ArtifactStore = Depends(get_artifact_store),
    principal: Principal = Depends(require_scope("messqry/registry.write")),
) -> ModelVersion:
    """Validate and register one private MLflow pyfunc ZIP package."""

    del principal  # The scope dependency enforces access; identity is not stored in model metadata.
    model = session.get(RegisteredModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")

    try:
        validated = validate_mlflow_pyfunc_package(file.file, limits=PackageLimits())
    except PackageValidationError as exc:
        raise HTTPException(status_code=HTTPStatus.UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    current_version = session.scalar(
        select(func.max(ModelVersion.version)).where(ModelVersion.model_id == model_id)
    )
    version_id = uuid4()
    key = _artifact_key(model_id, version_id)
    stored_artifact = None
    try:
        stored_artifact = store.put(
            key,
            file.file,
            sha256=validated.sha256,
            size_bytes=validated.size_bytes,
        )
        version = ModelVersion(
            id=version_id,
            model_id=model_id,
            version=(current_version or 0) + 1,
            artifact_uri=stored_artifact.uri,
            package_format=validated.package_format,
            artifact_sha256=validated.sha256,
            artifact_size_bytes=validated.size_bytes,
            artifact_version_id=stored_artifact.version_id,
            artifact_manifest=validated.manifest,
            validation_status="validated",
            framework=framework,
            metrics={},
        )
        session.add(version)
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        _cleanup_unregistered_artifact(
            store, key, stored_artifact.version_id if stored_artifact is not None else None
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent version registration occurred; retry the request",
        ) from exc
    except Exception as exc:
        session.rollback()
        _cleanup_unregistered_artifact(
            store, key, stored_artifact.version_id if stored_artifact is not None else None
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact registration could not be completed",
        ) from exc
    return version


@models_router.get("/{model_id}/versions/{version_number}/artifact")
def download_model_artifact(
    model_id: UUID,
    version_number: int,
    session: Session = Depends(get_session),
    store: ArtifactStore = Depends(get_artifact_store),
    principal: Principal = Depends(require_scope("messqry/registry.read")),
) -> StreamingResponse:
    """Stream an authorized, previously validated package from private storage."""

    del principal
    version = session.scalar(
        select(ModelVersion).where(
            ModelVersion.model_id == model_id,
            ModelVersion.version == version_number,
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model version not found")
    if version.validation_status != "validated" or version.package_format != "mlflow_pyfunc":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This legacy artifact has not been validated for download",
        )
    try:
        artifact = store.open(
            _artifact_key(model_id, version.id), version_id=version.artifact_version_id
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Artifact storage is unavailable",
        ) from exc
    return StreamingResponse(
        _artifact_chunks(artifact),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="model-{model_id}-v{version_number}.zip"',
            "ETag": f'"{version.artifact_sha256}"',
        },
    )


@models_router.post("", response_model=ModelRead, status_code=status.HTTP_201_CREATED)
def create_model(
    payload: ModelCreate,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/registry.write")),
) -> RegisteredModel:
    """Create a logical model record in the registry."""

    del principal
    model = RegisteredModel(name=payload.name, description=payload.description)
    session.add(model)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="A model with this name already exists"
        ) from exc
    session.refresh(model)
    return model


@models_router.get("", response_model=list[ModelRead])
def list_models(
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/registry.read")),
) -> list[RegisteredModel]:
    """List registered models, newest first."""

    del principal
    return list(
        session.scalars(select(RegisteredModel).order_by(RegisteredModel.created_at.desc()))
    )


@models_router.get("/{model_id}", response_model=ModelDetail)
def get_model(
    model_id: UUID,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/registry.read")),
) -> RegisteredModel:
    """Return one model and its registered versions."""

    del principal
    model = session.get(RegisteredModel, model_id)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return model


@models_router.post(
    "/{model_id}/versions",
    response_model=ModelVersionRead,
    status_code=status.HTTP_201_CREATED,
)
def register_model_version(
    model_id: UUID,
    payload: ModelVersionCreate,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/registry.write")),
) -> ModelVersion:
    """Register metadata for the next version of an existing model."""

    del principal
    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")

    current_version = session.scalar(
        select(func.max(ModelVersion.version)).where(ModelVersion.model_id == model_id)
    )
    version = ModelVersion(
        model_id=model_id,
        version=(current_version or 0) + 1,
        artifact_uri=payload.artifact_uri,
        framework=payload.framework,
        metrics=payload.metrics,
    )
    session.add(version)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent version registration occurred; retry the request",
        ) from exc
    session.refresh(version)
    return version


@models_router.get("/{model_id}/versions", response_model=list[ModelVersionRead])
def list_model_versions(
    model_id: UUID,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/registry.read")),
) -> list[ModelVersion]:
    """List all registered versions of one model."""

    del principal
    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return list(
        session.scalars(
            select(ModelVersion)
            .where(ModelVersion.model_id == model_id)
            .order_by(ModelVersion.version.desc())
        )
    )
