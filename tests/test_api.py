import json
from http import HTTPStatus
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from messqry.api import deployments, evaluations, routes
from messqry.api.schemas import DeploymentChange, EvaluationCreate, ModelCreate, ModelVersionCreate
from messqry.main import app
from messqry.models import (
    Deployment,
    DeploymentEvent,
    ModelEvaluation,
    ModelVersion,
    RegisteredModel,
)


def create_model_and_versions(session_factory, name="fraud-detector"):
    with session_factory() as session:
        model = routes.create_model(ModelCreate(name=name), session)
        versions = [
            routes.register_model_version(
                model.id,
                ModelVersionCreate(artifact_uri=uri, framework="sklearn", metrics={"auc": 0.9}),
                session,
            )
            for uri in ("s3://models/fraud/v1", "s3://models/fraud/v2")
        ]
        return model.id, [version.id for version in versions]


def test_health_readiness_and_openapi_contract(session_factory):
    assert routes.health_check().model_dump() == {"status": "ok"}
    with session_factory() as session:
        ready = routes.readiness_check(session)
        assert ready.model_dump() == {"status": "ready", "database": "ok"}

    class UnavailableSession:
        def execute(self, statement):
            raise SQLAlchemyError("database credentials must not leak")

        def rollback(self):
            pass

    unavailable = routes.readiness_check(UnavailableSession())
    assert unavailable.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert json.loads(unavailable.body) == {"status": "not_ready", "database": "unavailable"}
    assert b"credentials" not in unavailable.body

    readiness = app.openapi()["paths"]["/ready"]["get"]["responses"]
    assert readiness["503"]["content"]["application/json"]["schema"]["$ref"].endswith(
        "/ReadinessResponse"
    )


def test_model_registry_validates_and_versions_models(session_factory):
    with session_factory() as session:
        model = routes.create_model(ModelCreate(name="fraud-detector"), session)
        with pytest.raises(HTTPException) as duplicate:
            routes.create_model(ModelCreate(name=model.name), session)
        assert duplicate.value.status_code == status.HTTP_409_CONFLICT

        versions = [
            routes.register_model_version(
                model.id,
                ModelVersionCreate(artifact_uri=f"s3://models/fraud/v{index}"),
                session,
            )
            for index in (1, 2)
        ]
        assert [version.version for version in versions] == [1, 2]
        assert [item.version for item in routes.get_model(model.id, session).versions] == [1, 2]

        with pytest.raises(HTTPException) as missing:
            routes.get_model(UUID("00000000-0000-0000-0000-000000000000"), session)
        assert missing.value.status_code == status.HTTP_404_NOT_FOUND


def test_deploy_promote_rollback_and_history(session_factory):
    model_id, version_ids = create_model_and_versions(session_factory)
    with session_factory() as session:
        first = deployments.deploy_version(
            model_id, "staging", DeploymentChange(version=1, reason="initial"), session
        )
        assert first.model_version.version == 1

        promoted = deployments.deploy_version(
            model_id, "staging", DeploymentChange(version=2, reason="approved"), session
        )
        assert promoted.model_version.version == 2

        rolled_back = deployments.rollback_deployment(
            model_id, "staging", DeploymentChange(version=1, reason="regression"), session
        )
        assert rolled_back.model_version.version == 1

        history = deployments.deployment_history(model_id, "staging", session)
        assert [event.action for event in reversed(history)] == ["deploy", "promote", "rollback"]
        assert history[0].from_model_version_id == version_ids[1]
        assert history[0].to_model_version_id == version_ids[0]


def test_deployment_conflicts_and_missing_targets(session_factory):
    model_id, _ = create_model_and_versions(session_factory)
    with session_factory() as session:
        with pytest.raises(HTTPException) as invalid_environment:
            deployments.deploy_version(
                model_id, "BadEnvironment", DeploymentChange(version=1), session
            )
        assert invalid_environment.value.status_code == HTTPStatus.UNPROCESSABLE_ENTITY

        with pytest.raises(HTTPException) as missing_version:
            deployments.deploy_version(model_id, "staging", DeploymentChange(version=3), session)
        assert missing_version.value.status_code == status.HTTP_404_NOT_FOUND

        with pytest.raises(HTTPException) as missing_deployment:
            deployments.rollback_deployment(
                model_id, "production", DeploymentChange(version=1), session
            )
        assert missing_deployment.value.status_code == status.HTTP_404_NOT_FOUND


def test_initial_deployment_insert_conflict_maps_to_http_409(session_factory):
    model_id, _ = create_model_and_versions(session_factory)
    with session_factory() as session:
        conflict = IntegrityError("insert deployment", {}, RuntimeError("unique conflict"))
        with patch.object(session, "flush", side_effect=conflict):
            with pytest.raises(HTTPException) as response:
                deployments.deploy_version(
                    model_id, "staging", DeploymentChange(version=1), session
                )
        assert response.value.status_code == status.HTTP_409_CONFLICT


def test_stale_deployment_change_returns_conflict_without_corrupting_history(session_factory):
    model_id, version_ids = create_model_and_versions(session_factory)
    with session_factory() as initial_session:
        deployments.deploy_version(
            model_id, "staging", DeploymentChange(version=1), initial_session
        )

    stale_session = session_factory()
    try:
        stale_deployment = stale_session.scalar(
            select(Deployment).where(
                Deployment.model_id == model_id, Deployment.environment == "staging"
            )
        )
        assert stale_deployment.revision == 1
        with session_factory() as winning_session:
            deployments.deploy_version(
                model_id, "staging", DeploymentChange(version=2), winning_session
            )

        with pytest.raises(HTTPException) as conflict:
            deployments.deploy_version(
                model_id, "staging", DeploymentChange(version=2), stale_session
            )
        assert conflict.value.status_code == status.HTTP_409_CONFLICT
    finally:
        stale_session.close()

    with session_factory() as session:
        deployment = session.scalar(select(Deployment))
        history = list(
            session.scalars(select(DeploymentEvent).order_by(DeploymentEvent.created_at))
        )
        assert deployment.model_version_id == version_ids[1]
        assert [event.action for event in history] == ["deploy", "promote"]


def test_evaluation_thresholds_are_stored_and_listed(session_factory):
    model_id, version_ids = create_model_and_versions(session_factory)
    with session_factory() as session:
        passing = evaluations.record_evaluation(
            model_id,
            1,
            EvaluationCreate(
                metrics={"auc": 0.91, "loss": 0.12},
                thresholds={"auc": {"operator": "gte", "value": 0.9}},
            ),
            session,
        )
        failing = evaluations.record_evaluation(
            model_id,
            1,
            EvaluationCreate(
                metrics={"auc": 0.81},
                thresholds={"auc": {"operator": "gte", "value": 0.9}},
            ),
            session,
        )
        assert passing.passed is True
        assert failing.passed is False
        assert len(evaluations.list_evaluations(model_id, 1, session)) == 2
        assert passing.model_version_id == version_ids[0]

        with pytest.raises(ValueError):
            EvaluationCreate(
                metrics={"auc": 0.9},
                thresholds={"accuracy": {"operator": "gte", "value": 0.8}},
            )

        with pytest.raises(HTTPException) as missing_version:
            evaluations.list_evaluations(model_id, 99, session)
        assert missing_version.value.status_code == status.HTTP_404_NOT_FOUND

        assert session.scalar(select(ModelEvaluation).where(ModelEvaluation.id == passing.id))
        assert session.get(RegisteredModel, model_id)
        assert session.get(ModelVersion, version_ids[1])
