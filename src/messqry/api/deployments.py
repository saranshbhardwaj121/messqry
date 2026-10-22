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
    EvaluationPolicy,
    ModelEvaluation,
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
            status_code=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
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
        "artifact_version_id": version.artifact_version_id,
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


def _validate_override(principal: Principal, payload: DeploymentChange) -> None:
    if not payload.override_evaluation:
        return
    if "messqry/evaluation.override" not in principal.scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The credential does not grant evaluation override permission",
        )
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail="An evaluation override requires a non-empty audit reason",
        )


def _production_policy_blocker(
    session: Session, model_id: UUID, target: ModelVersion
) -> str | None:
    """Return why production is blocked unless each latest policy has a current pass."""
    policies = list(
        session.scalars(
            select(EvaluationPolicy)
            .where(
                EvaluationPolicy.model_id == model_id,
                EvaluationPolicy.environment == "production",
            )
            .order_by(EvaluationPolicy.name, EvaluationPolicy.version.desc())
        )
    )
    current_by_name: dict[str, EvaluationPolicy] = {}
    for policy in policies:
        current_by_name.setdefault(policy.name, policy)
    if not current_by_name:
        return "No production evaluation policy is configured"

    policy_ids = [policy.id for policy in current_by_name.values()]
    evaluations = list(
        session.scalars(
            select(ModelEvaluation)
            .where(
                ModelEvaluation.model_version_id == target.id,
                ModelEvaluation.policy_id.in_(policy_ids),
            )
            .order_by(ModelEvaluation.created_at.desc())
        )
    )
    latest_by_policy: dict[UUID, list[ModelEvaluation]] = {}
    for evaluation in evaluations:
        assert evaluation.policy_id is not None
        tied_latest = latest_by_policy.setdefault(evaluation.policy_id, [])
        if not tied_latest or evaluation.created_at == tied_latest[0].created_at:
            tied_latest.append(evaluation)

    for name, policy in current_by_name.items():
        tied_latest = latest_by_policy.get(policy.id, [])
        if not tied_latest:
            return (
                f"Production policy '{name}' has no evaluation for model version {target.version}"
            )
        if len(tied_latest) > 1:
            return f"Production policy '{name}' has ambiguous latest evaluations"
        evaluation = tied_latest[0]
        if (
            evaluation.policy_name != policy.name
            or evaluation.policy_version != policy.version
            or evaluation.policy_environment != policy.environment
            or evaluation.thresholds != policy.thresholds
        ):
            return f"Production policy '{name}' has an incompatible evaluation snapshot"
        if not evaluation.passed:
            return f"The latest evaluation for production policy '{name}' did not pass"
    return None


def _request_fingerprint(action: str, payload: DeploymentChange) -> str:
    request_data = {"action": action, "version": payload.version, "reason": payload.reason}
    if payload.override_evaluation:
        request_data["override_evaluation"] = True
    canonical = json.dumps(request_data, sort_keys=True, separators=(",", ":"))
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

    _validate_override(principal, payload)
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

    changing_version = deployment is None or deployment.model_version_id != target.id
    override = payload.override_evaluation
    if environment == "production" and changing_version and not override:
        blocker = _production_policy_blocker(session, model_id, target)
        if blocker is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocker)

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
                    action="override" if override else "deploy",
                    reason=payload.reason.strip() if override else payload.reason,
                )
            )
        elif deployment.model_version_id != target.id:
            _record_change(
                session,
                deployment,
                target,
                "override" if override else "promote",
                payload.reason.strip() if override else payload.reason,
            )
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

    _validate_override(principal, payload)
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
    changing_version = deployment.model_version_id != target.id
    override = payload.override_evaluation
    if environment == "production" and changing_version and not override:
        blocker = _production_policy_blocker(session, model_id, target)
        if blocker is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=blocker)
    if deployment.model_version_id == target.id and idempotency_key is None:
        # Treat replayed desired-state requests as an idempotent no-op.
        return deployment

    if deployment.model_version_id != target.id:
        _record_change(
            session,
            deployment,
            target,
            "override" if override else "rollback",
            payload.reason.strip() if override else payload.reason,
        )
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
