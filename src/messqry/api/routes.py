"""HTTP routes for the initial API surface."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse

from messqry.api.schemas import (
    ModelCreate,
    ModelDetail,
    ModelRead,
    ModelVersionCreate,
    ModelVersionRead,
)
from messqry.db import get_session
from messqry.models import ModelVersion, RegisteredModel

router = APIRouter()


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


@models_router.post("", response_model=ModelRead, status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelCreate, session: Session = Depends(get_session)) -> RegisteredModel:
    """Create a logical model record in the registry."""

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
def list_models(session: Session = Depends(get_session)) -> list[RegisteredModel]:
    """List registered models, newest first."""

    return list(
        session.scalars(select(RegisteredModel).order_by(RegisteredModel.created_at.desc()))
    )


@models_router.get("/{model_id}", response_model=ModelDetail)
def get_model(model_id: UUID, session: Session = Depends(get_session)) -> RegisteredModel:
    """Return one model and its registered versions."""

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
) -> ModelVersion:
    """Register metadata for the next version of an existing model."""

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
    model_id: UUID, session: Session = Depends(get_session)
) -> list[ModelVersion]:
    """List all registered versions of one model."""

    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return list(
        session.scalars(
            select(ModelVersion)
            .where(ModelVersion.model_id == model_id)
            .order_by(ModelVersion.version.desc())
        )
    )
