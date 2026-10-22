"""Endpoints for recording model-version metric evaluations."""

from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from messqry.api.schemas import (
    EvaluationCreate,
    EvaluationCreateV2,
    EvaluationPolicyCreate,
    EvaluationPolicyRead,
    EvaluationRead,
    EvaluationReadV1,
    MetricOperator,
)
from messqry.api.security import Principal, require_scope
from messqry.db import get_session
from messqry.models import EvaluationPolicy, ModelEvaluation, ModelVersion, RegisteredModel

router = APIRouter(
    prefix="/api/v1/models/{model_id}/versions/{version_number}/evaluations",
    tags=["evaluations"],
)
policy_router = APIRouter(
    prefix="/api/v1/models/{model_id}/evaluation-policies",
    tags=["evaluation policies"],
)
v2_router = APIRouter(
    prefix="/api/v2/models/{model_id}/versions/{version_number}/evaluations",
    tags=["evaluations"],
)


def _get_version(session: Session, model_id: UUID, version_number: int) -> ModelVersion:
    version = session.scalar(
        select(ModelVersion).where(
            ModelVersion.model_id == model_id,
            ModelVersion.version == version_number,
        )
    )
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model version not found")
    return version


def _meets_threshold(metric_value: float, operator: MetricOperator, threshold: float) -> bool:
    if operator == MetricOperator.GREATER_THAN:
        return metric_value > threshold
    if operator == MetricOperator.GREATER_THAN_OR_EQUAL:
        return metric_value >= threshold
    if operator == MetricOperator.LESS_THAN:
        return metric_value < threshold
    return metric_value <= threshold


@policy_router.post("", response_model=EvaluationPolicyRead, status_code=status.HTTP_201_CREATED)
def create_evaluation_policy(
    model_id: UUID,
    payload: EvaluationPolicyCreate,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/evaluation.write")),
) -> EvaluationPolicy:
    """Create an immutable version of a model/environment evaluation policy."""

    del principal
    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    policy = EvaluationPolicy(
        model_id=model_id,
        name=payload.name,
        environment=payload.environment,
        version=payload.version,
        thresholds={
            name: threshold.model_dump(mode="json")
            for name, threshold in payload.thresholds.items()
        },
    )
    session.add(policy)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(
            status_code=HTTPStatus.CONFLICT,
            detail="That evaluation policy version already exists",
        ) from exc
    session.refresh(policy)
    return policy


@policy_router.get("", response_model=list[EvaluationPolicyRead])
def list_evaluation_policies(
    model_id: UUID,
    environment: str | None = None,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/evaluation.read")),
) -> list[EvaluationPolicy]:
    """List immutable policy versions for a model, optionally by environment."""

    del principal
    if session.get(RegisteredModel, model_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model not found")
    query = select(EvaluationPolicy).where(EvaluationPolicy.model_id == model_id)
    if environment is not None:
        query = query.where(EvaluationPolicy.environment == environment)
    return list(
        session.scalars(
            query.order_by(
                EvaluationPolicy.environment,
                EvaluationPolicy.name,
                EvaluationPolicy.version.desc(),
            )
        )
    )


@router.post("", response_model=EvaluationReadV1, status_code=status.HTTP_201_CREATED)
def record_evaluation(
    model_id: UUID,
    version_number: int,
    payload: EvaluationCreate,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/evaluation.write")),
) -> ModelEvaluation:
    """Record metrics against caller-supplied thresholds using the stable v1 contract."""

    del principal
    version = _get_version(session, model_id, version_number)
    passed = all(
        _meets_threshold(payload.metrics[name], threshold.operator, threshold.value)
        for name, threshold in payload.thresholds.items()
    )
    evaluation = ModelEvaluation(
        model_version_id=version.id,
        metrics=payload.metrics,
        thresholds={
            name: threshold.model_dump(mode="json")
            for name, threshold in payload.thresholds.items()
        },
        passed=passed,
    )
    session.add(evaluation)
    session.commit()
    session.refresh(evaluation)
    return evaluation


@v2_router.post("", response_model=EvaluationRead, status_code=status.HTTP_201_CREATED)
def record_policy_evaluation(
    model_id: UUID,
    version_number: int,
    payload: EvaluationCreateV2,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/evaluation.write")),
) -> ModelEvaluation:
    """Evaluate supplied metrics against a selected immutable v2 policy version."""

    del principal
    version = _get_version(session, model_id, version_number)
    policy = session.scalar(
        select(EvaluationPolicy).where(
            EvaluationPolicy.id == payload.policy_id,
            EvaluationPolicy.model_id == model_id,
        )
    )
    if policy is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation policy not found"
        )
    missing_metrics = set(policy.thresholds) - set(payload.metrics)
    if missing_metrics:
        missing = ", ".join(sorted(missing_metrics))
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY,
            detail=f"Metrics required by the policy were not supplied: {missing}",
        )
    passed = all(
        _meets_threshold(
            payload.metrics[name],
            MetricOperator(threshold["operator"]),
            threshold["value"],
        )
        for name, threshold in policy.thresholds.items()
    )
    evaluation = ModelEvaluation(
        model_version_id=version.id,
        dataset_name=payload.dataset_name.strip(),
        dataset_version=payload.dataset_version.strip(),
        dataset_sha256=payload.dataset_sha256.lower(),
        evaluator_name=payload.evaluator_name.strip(),
        evaluator_version=payload.evaluator_version.strip(),
        policy_id=policy.id,
        policy_name=policy.name,
        policy_version=policy.version,
        policy_environment=policy.environment,
        metrics=payload.metrics,
        thresholds=policy.thresholds,
        passed=passed,
    )
    session.add(evaluation)
    session.commit()
    session.refresh(evaluation)
    return evaluation


@router.get("", response_model=list[EvaluationReadV1])
def list_evaluations(
    model_id: UUID,
    version_number: int,
    session: Session = Depends(get_session),
    principal: Principal = Depends(require_scope("messqry/evaluation.read")),
) -> list[ModelEvaluation]:
    """List evaluation records for a model version, newest first."""

    del principal
    version = _get_version(session, model_id, version_number)
    return list(
        session.scalars(
            select(ModelEvaluation)
            .where(ModelEvaluation.model_version_id == version.id)
            .order_by(ModelEvaluation.created_at.desc())
        )
    )
