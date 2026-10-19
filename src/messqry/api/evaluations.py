"""Endpoints for recording model-version metric evaluations."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from messqry.api.schemas import EvaluationCreate, EvaluationRead, MetricOperator
from messqry.db import get_session
from messqry.models import ModelEvaluation, ModelVersion

router = APIRouter(
    prefix="/api/v1/models/{model_id}/versions/{version_number}/evaluations",
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


@router.post("", response_model=EvaluationRead, status_code=status.HTTP_201_CREATED)
def record_evaluation(
    model_id: UUID,
    version_number: int,
    payload: EvaluationCreate,
    session: Session = Depends(get_session),
) -> ModelEvaluation:
    """Evaluate supplied numeric metrics against caller-provided thresholds."""

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


@router.get("", response_model=list[EvaluationRead])
def list_evaluations(
    model_id: UUID,
    version_number: int,
    session: Session = Depends(get_session),
) -> list[ModelEvaluation]:
    """List evaluation records for a model version, newest first."""

    version = _get_version(session, model_id, version_number)
    return list(
        session.scalars(
            select(ModelEvaluation)
            .where(ModelEvaluation.model_version_id == version.id)
            .order_by(ModelEvaluation.created_at.desc())
        )
    )
