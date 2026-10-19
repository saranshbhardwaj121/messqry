"""Control-plane endpoints for selecting and rolling back model versions."""

import re
from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from messqry.api.schemas import DeploymentChange, DeploymentEventRead, DeploymentRead
from messqry.db import get_session
from messqry.models import Deployment, DeploymentEvent, ModelVersion, RegisteredModel

router = APIRouter(prefix="/api/v1/models/{model_id}/deployments", tags=["deployments"])
ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _validate_environment(environment: str) -> None:
    if not ENVIRONMENT_PATTERN.fullmatch(environment):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Environment must be a lowercase slug of up to 32 characters",
        )


def _get_target_version(session: Session, model_id: UUID, version_number: int) -> ModelVersion:
    target = session.scalar(
        select(ModelVersion).where(
            ModelVersion.model_id == model_id,
            ModelVersion.version == version_number,
        )
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model version not found",
        )
    return target


def _record_change(
    session: Session,
    deployment: Deployment,
    target: ModelVersion,
    action: str,
    reason: str | None,
) -> None:
    previous_version_id = deployment.model_version_id if deployment.model_version_id else None
    deployment.model_version = target
    session.add(
        DeploymentEvent(
            deployment_id=deployment.id,
            from_model_version_id=previous_version_id,
            to_model_version_id=target.id,
            action=action,
            reason=reason,
        )
    )


@router.get("", response_model=list[DeploymentRead])
def list_deployments(model_id: UUID, session: Session = Depends(get_session)) -> list[Deployment]:
    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    return list(
        session.scalars(
            select(Deployment)
            .options(selectinload(Deployment.model_version))
            .where(Deployment.model_id == model_id)
            .order_by(Deployment.environment)
        )
    )


@router.put("/{environment}", response_model=DeploymentRead)
def deploy_version(
    model_id: UUID,
    environment: str,
    payload: DeploymentChange,
    session: Session = Depends(get_session),
) -> Deployment:
    """Select a registered version for an environment and record the change."""

    _validate_environment(environment)
    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    target = _get_target_version(session, model_id, payload.version)
    deployment = session.scalar(
        select(Deployment)
        .options(selectinload(Deployment.model_version))
        .where(Deployment.model_id == model_id, Deployment.environment == environment)
        .with_for_update()
    )

    try:
        if deployment is None:
            deployment = Deployment(
                model_id=model_id, environment=environment, model_version=target
            )
            session.add(deployment)
            session.flush()
            session.add(
                DeploymentEvent(
                    deployment_id=deployment.id,
                    from_model_version_id=None,
                    to_model_version_id=target.id,
                    action="deploy",
                    reason=payload.reason,
                )
            )
        elif deployment.model_version_id != target.id:
            _record_change(session, deployment, target, "promote", payload.reason)
        session.commit()
    except (IntegrityError, StaleDataError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent deployment change occurred; retry the request",
        ) from exc
    session.refresh(deployment)
    return deployment


@router.post("/{environment}/rollback", response_model=DeploymentRead)
def rollback_deployment(
    model_id: UUID,
    environment: str,
    payload: DeploymentChange,
    session: Session = Depends(get_session),
) -> Deployment:
    """Move an environment back to a selected registered model version."""

    _validate_environment(environment)
    deployment = session.scalar(
        select(Deployment)
        .options(selectinload(Deployment.model_version))
        .where(Deployment.model_id == model_id, Deployment.environment == environment)
        .with_for_update()
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    target = _get_target_version(session, model_id, payload.version)
    if deployment.model_version_id == target.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The requested version is already selected for this environment",
        )

    _record_change(session, deployment, target, "rollback", payload.reason)
    try:
        session.commit()
    except (IntegrityError, StaleDataError) as exc:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent deployment change occurred; retry the request",
        ) from exc
    session.refresh(deployment)
    return deployment


@router.get("/{environment}/history", response_model=list[DeploymentEventRead])
def deployment_history(
    model_id: UUID,
    environment: str,
    session: Session = Depends(get_session),
) -> list[DeploymentEvent]:
    _validate_environment(environment)
    deployment = session.scalar(
        select(Deployment).where(
            Deployment.model_id == model_id,
            Deployment.environment == environment,
        )
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")
    return list(
        session.scalars(
            select(DeploymentEvent)
            .where(DeploymentEvent.deployment_id == deployment.id)
            .order_by(DeploymentEvent.created_at.desc())
        )
    )
