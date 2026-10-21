import json
from http import HTTPStatus
from io import BytesIO
from unittest.mock import patch
from uuid import UUID
from zipfile import ZipFile

import pytest
from fastapi import HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.requests import Request

from messqry.api import deployments, evaluations, routes
from messqry.api.schemas import (
    DeploymentChange,
    EvaluationCreate,
    ModelCreate,
    ModelVersionCreate,
    PredictionRequest,
    PredictionResponse,
)
from messqry.api.security import Principal, ScopeRequirement
from messqry.artifact_storage import LocalArtifactStore
from messqry.main import app
from messqry.models import (
    Deployment,
    DeploymentEvent,
    ModelEvaluation,
    ModelVersion,
    RegisteredModel,
)


def valid_pyfunc_package() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "MLmodel",
            "flavors:\n  python_function:\n    loader_module: mlflow.sklearn\n    data: data/model.pkl\n",
        )
        archive.writestr("data/model.pkl", b"model bytes")
    return output.getvalue()


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
    registry_security = app.openapi()["paths"]["/api/v1/models"]["get"]["security"]
    assert registry_security == [{"HTTPBearer": []}]
    deployment_list_path = "/api/v1/models/{model_id}/deployments"
    assert app.openapi()["paths"][deployment_list_path]["get"]["security"] == [{"HTTPBearer": []}]
    deployment_security = app.openapi()["paths"][
        "/api/v1/models/{model_id}/deployments/{environment}"
    ]
    assert deployment_security["put"]["security"] == [{"HTTPBearer": []}]
    assert any(
        parameter["name"] == "Idempotency-Key" and parameter["in"] == "header"
        for parameter in deployment_security["put"]["parameters"]
    )
    prediction_path = "/api/v1/models/{model_id}/deployments/{environment}/predict"
    assert app.openapi()["paths"][prediction_path]["post"]["security"] == [{"HTTPBearer": []}]

    def required_scopes(path, method):
        route = next(
            route
            for route in deployments.router.routes
            if getattr(route, "path", None) == path and method in getattr(route, "methods", set())
        )
        return {
            dependency.call.scope
            for dependency in route.dependant.dependencies
            if isinstance(dependency.call, ScopeRequirement)
        }

    deployment_path = "/api/v1/models/{model_id}/deployments/{environment}"
    assert required_scopes(deployment_list_path, "GET") == {"messqry/deployment.read"}
    assert required_scopes(deployment_path, "PUT") == {"messqry/deployment.write"}
    assert required_scopes(f"{deployment_path}/rollback", "POST") == {"messqry/deployment.write"}
    assert required_scopes(f"{deployment_path}/history", "GET") == {"messqry/deployment.read"}
    assert required_scopes(f"{deployment_path}/predict", "POST") == {"messqry/prediction.invoke"}


def test_prediction_route_uses_active_validated_version_and_runtime(session_factory):
    with session_factory() as session:
        model = routes.create_model(ModelCreate(name="servable-model"), session)
        model_id = model.id
        version = ModelVersion(
            model_id=model_id,
            version=1,
            artifact_uri="s3://private-bucket/model.zip",
            package_format="mlflow_pyfunc",
            artifact_sha256="a" * 64,
            validation_status="validated",
        )
        session.add(version)
        session.commit()
        version_number = version.version
        deployments.deploy_version(
            model_id, "production", DeploymentChange(version=version_number), session
        )

        class FakeRuntime:
            def predict(self, **kwargs):
                assert not session.in_transaction()
                self.request = kwargs
                return PredictionResponse(
                    model_id=model_id,
                    environment="production",
                    model_version=version_number,
                    predictions=[0.91],
                )

        runtime = FakeRuntime()
        app.state.runtime_manager = runtime
        request = Request({"type": "http", "app": app, "headers": [], "path": "/"})
        response = deployments.predict(
            model_id,
            "production",
            PredictionRequest(inputs=[{"feature": 1}]),
            request,
            session,
            Principal("test-user", "user", frozenset({"messqry/prediction.invoke"})),
        )
        assert not session.in_transaction()

    assert response.model_version == version_number
    assert response.predictions == [0.91]
    assert runtime.request["model_id"] == str(model_id)
    assert runtime.request["model_version"] == version_number
    assert runtime.request["payload"] == {"inputs": [{"feature": 1}]}
    del app.state.runtime_manager


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
        assert versions[0].validation_status == "legacy_unverified"
        assert versions[0].package_format is None
        assert versions[0].artifact_sha256 is None
        assert versions[0].artifact_version_id is None
        assert [item.version for item in routes.get_model(model.id, session).versions] == [1, 2]

        with pytest.raises(HTTPException) as missing:
            routes.get_model(UUID("00000000-0000-0000-0000-000000000000"), session)
        assert missing.value.status_code == status.HTTP_404_NOT_FOUND


def test_registered_model_version_metadata_is_immutable(session_factory):
    model_id, _ = create_model_and_versions(session_factory)

    with session_factory() as session:
        version = session.scalar(select(ModelVersion).where(ModelVersion.model_id == model_id))
        version.artifact_uri = "s3://other-bucket/changed-after-registration"
        with pytest.raises(ValueError, match="Registered model versions are immutable"):
            session.commit()
        session.rollback()

    with session_factory() as session:
        version = session.scalar(select(ModelVersion).where(ModelVersion.model_id == model_id))
        session.delete(version)
        with pytest.raises(ValueError, match="Registered model versions cannot be deleted"):
            session.commit()
        session.rollback()


def test_artifact_upload_registers_immutable_validated_version_and_downloads(
    session_factory, tmp_path
):
    model_id, _ = create_model_and_versions(session_factory)
    store = LocalArtifactStore(tmp_path / "artifacts")
    payload = valid_pyfunc_package()
    upload = UploadFile(file=BytesIO(payload), filename="model.zip")
    principal = Principal("operator", "user", frozenset({"messqry/registry.write"}))

    with session_factory() as session:
        version = routes.upload_model_artifact(
            model_id,
            file=upload,
            framework="sklearn",
            session=session,
            store=store,
            principal=principal,
        )

    assert version.version == 3
    assert version.validation_status == "validated"
    assert version.package_format == "mlflow_pyfunc"
    assert version.artifact_sha256 is not None
    key = routes._artifact_key(model_id, version.id)
    with store.open(key) as stored:
        assert stored.read() == payload

    class MemoryDownloadStore(LocalArtifactStore):
        def open(self, artifact_key, *, version_id=None):
            with super().open(artifact_key, version_id=version_id) as artifact:
                return BytesIO(artifact.read())

    with session_factory() as session:
        response = routes.download_model_artifact(
            model_id,
            3,
            session=session,
            store=MemoryDownloadStore(tmp_path / "artifacts"),
            principal=Principal("operator", "user", frozenset({"messqry/registry.read"})),
        )
    assert response.media_type == "application/zip"
    assert response.headers["etag"] == f'"{version.artifact_sha256}"'
    assert response.headers["content-disposition"].endswith('-v3.zip"')


def test_artifact_upload_rejects_invalid_package_without_registering_version(
    session_factory, tmp_path
):
    model_id, _ = create_model_and_versions(session_factory)
    store = LocalArtifactStore(tmp_path / "artifacts")
    upload = UploadFile(file=BytesIO(b"not a ZIP"), filename="bad.zip")

    with session_factory() as session:
        with pytest.raises(HTTPException) as rejected:
            routes.upload_model_artifact(
                model_id,
                file=upload,
                session=session,
                store=store,
                principal=Principal("operator", "user", frozenset({"messqry/registry.write"})),
            )
        assert rejected.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        assert len(routes.list_model_versions(model_id, session)) == 2
    assert not list((tmp_path / "artifacts").rglob("*.zip"))


def test_artifact_upload_cleans_up_object_when_registry_commit_fails(session_factory, tmp_path):
    model_id, _ = create_model_and_versions(session_factory)
    store = LocalArtifactStore(tmp_path / "artifacts")
    upload = UploadFile(file=BytesIO(valid_pyfunc_package()), filename="model.zip")

    with session_factory() as session:
        conflict = IntegrityError("insert model version", {}, RuntimeError("unique conflict"))
        with patch.object(session, "commit", side_effect=conflict):
            with pytest.raises(HTTPException) as rejected:
                routes.upload_model_artifact(
                    model_id,
                    file=upload,
                    session=session,
                    store=store,
                    principal=Principal("operator", "user", frozenset({"messqry/registry.write"})),
                )
        assert rejected.value.status_code == status.HTTP_409_CONFLICT
    assert not list((tmp_path / "artifacts").rglob("*.zip"))


def test_deploy_promote_rollback_and_history(session_factory):
    model_id, version_ids = create_model_and_versions(session_factory)
    with session_factory() as session:
        first = deployments.deploy_version(
            model_id, "staging", DeploymentChange(version=1, reason="initial"), session
        )
        assert first.model_version.version == 1
        repeated_initial = deployments.deploy_version(
            model_id, "staging", DeploymentChange(version=1, reason="initial"), session
        )
        assert repeated_initial.model_version.version == 1

        promoted = deployments.deploy_version(
            model_id, "staging", DeploymentChange(version=2, reason="approved"), session
        )
        assert promoted.model_version.version == 2

        rolled_back = deployments.rollback_deployment(
            model_id, "staging", DeploymentChange(version=1, reason="regression"), session
        )
        assert rolled_back.model_version.version == 1
        repeated_rollback = deployments.rollback_deployment(
            model_id, "staging", DeploymentChange(version=1, reason="regression"), session
        )
        assert repeated_rollback.model_version.version == 1

        history = deployments.deployment_history(model_id, "staging", session)
        assert [event.action for event in reversed(history)] == ["deploy", "promote", "rollback"]
        assert history[0].from_model_version_id == version_ids[1]
        assert history[0].to_model_version_id == version_ids[0]


def test_deployment_idempotency_replays_result_and_rejects_key_reuse(session_factory):
    model_id, _ = create_model_and_versions(session_factory)
    with session_factory() as session:
        original = deployments.deploy_version(
            model_id,
            "staging",
            DeploymentChange(version=1, reason="start"),
            session,
            idempotency_key="deploy-start-1",
        )
        original_version = original.model_version.version
        deployments.deploy_version(
            model_id,
            "staging",
            DeploymentChange(version=2, reason="promote"),
            session,
        )

        replay = deployments.deploy_version(
            model_id,
            "staging",
            DeploymentChange(version=1, reason="start"),
            session,
            idempotency_key="deploy-start-1",
        )
        assert replay.model_version.version == original_version == 1

        with pytest.raises(HTTPException) as reused:
            deployments.deploy_version(
                model_id,
                "staging",
                DeploymentChange(version=2, reason="different request"),
                session,
                idempotency_key="deploy-start-1",
            )
        assert reused.value.status_code == status.HTTP_409_CONFLICT
        assert len(deployments.deployment_history(model_id, "staging", session)) == 2


def test_idempotent_deploy_replays_winner_after_initial_lookup_misses(session_factory, monkeypatch):
    model_id, _ = create_model_and_versions(session_factory)
    key = "deployment-race-1"
    with session_factory() as session:
        deployments.deploy_version(model_id, "staging", DeploymentChange(version=1), session)
    with session_factory() as winner_session:
        winner = deployments.deploy_version(
            model_id,
            "staging",
            DeploymentChange(version=2, reason="same request"),
            winner_session,
            idempotency_key=key,
        )

    real_replay = deployments._replay_request
    replay_calls = 0

    def miss_once_then_replay(session, requested_model_id, environment, request_key, fingerprint):
        nonlocal replay_calls
        replay_calls += 1
        if replay_calls == 1:
            # Model the initial lookup racing ahead of the winner's commit.
            return None
        return real_replay(session, requested_model_id, environment, request_key, fingerprint)

    monkeypatch.setattr(deployments, "_replay_request", miss_once_then_replay)
    with session_factory() as retry_session:
        retry = deployments.deploy_version(
            model_id,
            "staging",
            DeploymentChange(version=2, reason="same request"),
            retry_session,
            idempotency_key=key,
        )

    assert retry.model_version.id == winner.model_version.id
    assert retry.model_version.version == winner.model_version.version == 2
    assert replay_calls == 2
    with session_factory() as session:
        assert len(deployments.deployment_history(model_id, "staging", session)) == 2


def test_deployment_events_are_immutable(session_factory):
    model_id, _ = create_model_and_versions(session_factory)
    with session_factory() as session:
        deployments.deploy_version(model_id, "staging", DeploymentChange(version=1), session)
        event_record = session.scalar(select(DeploymentEvent))
        event_record.reason = "rewritten history"
        with pytest.raises(ValueError, match="Deployment events are immutable"):
            session.commit()
        session.rollback()

    with session_factory() as session:
        event_record = session.scalar(select(DeploymentEvent))
        session.delete(event_record)
        with pytest.raises(ValueError, match="Deployment events cannot be deleted"):
            session.commit()
        session.rollback()


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


def test_deployment_history_is_empty_before_first_deployment(session_factory):
    model_id, _ = create_model_and_versions(session_factory)
    with session_factory() as session:
        assert deployments.deployment_history(model_id, "staging", session) == []


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
