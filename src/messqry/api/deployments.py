"""Control-plane endpoints for selecting and rolling back model versions."""

import hashlib
import json
import re
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from messqry.api.schemas import (
    DeploymentChange,
    DeploymentEventRead,
    DeploymentRead,
    PredictionRequest,
    PredictionResponse,
)
from messqry.api.security import Principal, require_scope
from messqry.db import get_session
from messqry.models import (
    Deployment,
    DeploymentEvent,
    DeploymentRequest,
    ModelVersion,
    RegisteredModel,
)
from messqry.runtime.ecs import RuntimeLaunchError
from messqry.runtime.transport import RuntimeTransportError, encode_prediction_payload

router = APIRouter(prefix="/api/v1/models/{model_id}/deployments", tags=["deployments"])
ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _validate_environment(environment: str) -> None:
    if not ENVIRONMENT_PATTERN.fullmatch(environment):
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="Environment must be a lowercase slug of up to 32 characters",
        )


@router.post("/{environment}/predict", response_model=PredictionResponse)
def predict(
    model_id: UUID,
    environment: str,
    payload: PredictionRequest,
    request: Request,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/prediction.invoke")),
) -> PredictionResponse:
    """Run bounded inference against the currently selected validated version."""

    del principal
    _validate_environment(environment)
    deployment = session.scalar(
        select(Deployment)
        .options(selectinload(Deployment.model_version))
        .where(Deployment.model_id == model_id, Deployment.environment == environment)
    )
    if deployment is None:
        if session.get(RegisteredModel, model_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active deployment for this environment",
        )

    version = deployment.model_version
    if (
        version.validation_status != "validated"
        or version.package_format != "mlflow_pyfunc"
        or not version.artifact_sha256
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The active model version is not a validated serving package",
        )
    request_payload = payload.model_dump(mode="json")
    try:
        encode_prediction_payload(request_payload)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Prediction request exceeds the maximum runtime payload size",
        ) from exc
    runtime = getattr(request.app.state, "runtime_manager", None)
    if runtime is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model runtime is unavailable",
        )
    # Keep the database transaction limited to resolving the deployment. Startup
    # and inference may take up to 90 seconds, so carry only immutable metadata
    # across the remote call and return the pooled connection first.
    runtime_params = {
        "deployment_id": str(deployment.id),
        "deployment_revision": deployment.revision,
        "environment": environment,
        "model_id": str(model_id),
        "model_version_id": str(version.id),
        "model_version": version.version,
        "artifact_uri": version.artifact_uri,
        "artifact_sha256": version.artifact_sha256,
        "payload": request_payload,
    }
    session.rollback()
    try:
        return runtime.predict(**runtime_params)
    except RuntimeLaunchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Model runtime could not be started",
        ) from exc
    except RuntimeTransportError as exc:
        error_status = (
            status.HTTP_504_GATEWAY_TIMEOUT
            if exc.timed_out
            else status.HTTP_503_SERVICE_UNAVAILABLE
            if "unavailable" in str(exc)
            else status.HTTP_502_BAD_GATEWAY
        )
        raise HTTPException(status_code=error_status, detail=str(exc)) from exc


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


def _request_fingerprint(action: str, payload: DeploymentChange) -> str:
    canonical = json.dumps(
        {"action": action, "version": payload.version, "reason": payload.reason},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _replay_request(
    session: Session,
    model_id: UUID,
    environment: str,
    key: str | None,
    fingerprint: str,
) -> DeploymentRead | None:
    if key is None:
        return None
    record = session.scalar(
        select(DeploymentRequest).where(
            DeploymentRequest.model_id == model_id,
            DeploymentRequest.environment == environment,
            DeploymentRequest.idempotency_key == key,
        )
    )
    if record is None:
        return None
    if record.request_hash != fingerprint:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used for a different deployment request",
        )
    return DeploymentRead.model_validate(record.response_snapshot)


def _save_request_result(
    session: Session,
    deployment: Deployment,
    *,
    key: str | None,
    fingerprint: str,
) -> None:
    if key is None:
        return
    response = DeploymentRead.model_validate(deployment).model_dump(mode="json")
    session.add(
        DeploymentRequest(
            model_id=deployment.model_id,
            environment=deployment.environment,
            idempotency_key=key,
            request_hash=fingerprint,
            response_snapshot=response,
        )
    )


def _replay_after_conflict(
    session: Session,
    model_id: UUID,
    environment: str,
    key: str | None,
    fingerprint: str,
    cause: Exception,
) -> DeploymentRead:
    """Return a winner's snapshot when an idempotent request raced its commit."""
    session.rollback()
    replay = _replay_request(session, model_id, environment, key, fingerprint)
    if replay is not None:
        return replay
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A concurrent deployment change occurred; retry the request",
    ) from cause


@router.get("", response_model=list[DeploymentRead])
def list_deployments(
    model_id: UUID,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/deployment.read")),
) -> list[Deployment]:
    del principal
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
    principal: Principal = Depends(require_scope("messqry/deployment.write")),
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9._~:-]+$",
        ),
    ] = None,
) -> Deployment:
    """Select a registered version for an environment and record the change."""

    del principal
    _validate_environment(environment)
    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    fingerprint = _request_fingerprint("deploy", payload)
    replay = _replay_request(session, model_id, environment, idempotency_key, fingerprint)
    if replay is not None:
        return replay
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
        session.flush()
        _save_request_result(
            session,
            deployment,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        session.commit()
    except (IntegrityError, StaleDataError) as exc:
        return _replay_after_conflict(
            session, model_id, environment, idempotency_key, fingerprint, exc
        )
    session.refresh(deployment)
    return deployment


@router.post("/{environment}/rollback", response_model=DeploymentRead)
def rollback_deployment(
    model_id: UUID,
    environment: str,
    payload: DeploymentChange,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/deployment.write")),
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9._~:-]+$",
        ),
    ] = None,
) -> Deployment:
    """Move an environment back to a selected registered model version."""

    del principal
    _validate_environment(environment)
    fingerprint = _request_fingerprint("rollback", payload)
    replay = _replay_request(session, model_id, environment, idempotency_key, fingerprint)
    if replay is not None:
        return replay
    deployment = session.scalar(
        select(Deployment)
        .options(selectinload(Deployment.model_version))
        .where(Deployment.model_id == model_id, Deployment.environment == environment)
        .with_for_update()
    )
    if deployment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found")

    target = _get_target_version(session, model_id, payload.version)
    if deployment.model_version_id == target.id and idempotency_key is None:
        # Treat replayed desired-state requests as an idempotent no-op.
        return deployment

    if deployment.model_version_id != target.id:
        _record_change(session, deployment, target, "rollback", payload.reason)
    try:
        session.flush()
        _save_request_result(
            session,
            deployment,
            key=idempotency_key,
            fingerprint=fingerprint,
        )
        session.commit()
    except (IntegrityError, StaleDataError) as exc:
        return _replay_after_conflict(
            session, model_id, environment, idempotency_key, fingerprint, exc
        )
    session.refresh(deployment)
    return deployment


@router.get("/{environment}/history", response_model=list[DeploymentEventRead])
def deployment_history(
    model_id: UUID,
    environment: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/deployment.read")),
) -> list[DeploymentEvent]:
    del principal
    _validate_environment(environment)
    deployment = session.scalar(
        select(Deployment).where(
            Deployment.model_id == model_id,
            Deployment.environment == environment,
        )
    )
    if deployment is None:
        if session.get(RegisteredModel, model_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
        return []
    return list(
        session.scalars(
            select(DeploymentEvent)
            .where(DeploymentEvent.deployment_id == deployment.id)
            .order_by(DeploymentEvent.created_at.desc())
        )
    )
