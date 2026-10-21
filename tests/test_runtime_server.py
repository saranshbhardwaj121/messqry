import hashlib
import importlib.metadata
import io
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from messqry.api.schemas import PredictionRequest
from messqry.runtime.server import (
    ModelLoadError,
    RuntimeConfig,
    RuntimeConfigurationError,
    RuntimeService,
    _json_compatible,
    create_app,
)


def model_archive(*, extra: dict[str, bytes] | None = None) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("MLmodel", (extra or {}).get("MLmodel", b"flavors: {}"))
        archive.writestr("model/data.pkl", b"test-model")
        for name, value in (extra or {}).items():
            if name == "MLmodel":
                continue
            archive.writestr(name, value)
    return output.getvalue()


def runtime_config(artifact: bytes) -> RuntimeConfig:
    return RuntimeConfig(
        model_id=uuid4(),
        deployment_id="deployment-1",
        environment="production",
        deployment_revision=3,
        model_version_id="version-4",
        model_version=4,
        artifact_uri="s3://private-models/model-1/version-4.zip",
        artifact_sha256=hashlib.sha256(artifact).hexdigest(),
        artifact_version_id=None,
        aws_region="us-east-1",
    )


class FakeS3:
    def __init__(self, artifact: bytes):
        self.artifact = artifact
        self.requests = []

    def get_object(self, **kwargs):
        self.requests.append(kwargs)
        return {"Body": io.BytesIO(self.artifact)}


class FakeModel:
    def predict(self, dataframe):
        return [row["value"] * 2 for row in dataframe]


class ClosableModel(FakeModel):
    def __init__(self, *, fail_close=False):
        self.closed = False
        self.fail_close = fail_close

    def close(self):
        self.closed = True
        if self.fail_close:
            raise RuntimeError("model cleanup failed")


class DataFrame:
    __module__ = "pandas.core.frame"

    def __init__(self, records):
        self.records = records

    def to_dict(self, *, orient):
        assert orient == "records"
        return self.records


class DataFrameModel:
    def predict(self, dataframe):
        return DataFrame([{"score": 0.9} for _ in dataframe])


def test_runtime_config_requires_complete_valid_task_metadata():
    with pytest.raises(RuntimeConfigurationError, match="missing required configuration"):
        RuntimeConfig.from_environment({})

    env = {
        "MESSQRY_MODEL_ID": str(uuid4()),
        "MESSQRY_DEPLOYMENT_ID": "deployment-1",
        "MESSQRY_DEPLOYMENT_ENVIRONMENT": "production",
        "MESSQRY_DEPLOYMENT_REVISION": "3",
        "MESSQRY_MODEL_VERSION_ID": "version-4",
        "MESSQRY_MODEL_VERSION": "4",
        "MESSQRY_ARTIFACT_URI": "s3://private-models/model-1/version-4.zip",
        "MESSQRY_ARTIFACT_SHA256": "a" * 64,
        "AWS_REGION": "us-east-1",
    }
    config = RuntimeConfig.from_environment(env)

    assert config.deployment_revision == 3
    assert config.model_version == 4
    assert config.port == 8000


def test_runtime_downloads_checksums_extracts_and_loads_model():
    artifact = model_archive()
    loaded_paths = []
    s3 = FakeS3(artifact)
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=s3,
        model_loader=lambda path: loaded_paths.append(path) or FakeModel(),
    )
    try:
        service.load()

        assert service.ready
        assert s3.requests == [{"Bucket": "private-models", "Key": "model-1/version-4.zip"}]
        assert len(loaded_paths) == 1
        assert Path(loaded_paths[0], "MLmodel").is_file()
        assert service.readiness() == {
            "status": "ready",
            "model_version_id": "version-4",
        }
    finally:
        service.close()

    assert not service.ready
    assert service._work_dir is None


def test_runtime_shutdown_closes_model_before_removing_artifacts():
    artifact = model_archive()
    model = ClosableModel()
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda _path: model,
    )
    service.load()
    work_dir = Path(service._work_dir.name)

    service.close()

    assert model.closed
    assert not work_dir.exists()
    assert not service.ready
    assert service._work_dir is None


def test_runtime_shutdown_cleans_files_even_if_model_close_fails(caplog):
    artifact = model_archive()
    model = ClosableModel(fail_close=True)
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda _path: model,
    )
    service.load()
    work_dir = Path(service._work_dir.name)

    service.close()

    assert model.closed
    assert not work_dir.exists()
    assert not service.ready
    assert "Model runtime shutdown failed (RuntimeError)" in caplog.text


def test_runtime_downloads_the_registered_s3_object_version():
    artifact = model_archive()
    config = RuntimeConfig(
        **{**runtime_config(artifact).__dict__, "artifact_version_id": "object-version-7"}
    )
    s3 = FakeS3(artifact)
    service = RuntimeService(config, s3_client=s3, model_loader=lambda _path: FakeModel())
    try:
        service.load()
        assert s3.requests == [
            {
                "Bucket": "private-models",
                "Key": "model-1/version-4.zip",
                "VersionId": "object-version-7",
            }
        ]
    finally:
        service.close()


def test_runtime_rejects_checksum_mismatch_before_model_loading():
    artifact = model_archive()
    config = runtime_config(artifact)
    wrong_config = RuntimeConfig(**{**config.__dict__, "artifact_sha256": "b" * 64})
    loaded = []
    service = RuntimeService(
        wrong_config,
        s3_client=FakeS3(artifact),
        model_loader=lambda path: loaded.append(path),
    )

    with pytest.raises(ModelLoadError, match="checksum"):
        service.load()

    assert loaded == []
    assert not service.ready


def test_runtime_rejects_unsafe_archive_paths_before_loading():
    artifact = model_archive(extra={"../escape.txt": b"unsafe"})
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda _path: pytest.fail("unsafe model must not be loaded"),
    )

    with pytest.raises(ModelLoadError, match="unsafe path"):
        service.load()


@pytest.mark.parametrize(
    "extra",
    [
        {"DATA": b"file", "data/child.bin": b"child"},
        {"compressed.bin": b"x" * 300_000},
    ],
)
def test_runtime_rejects_archive_path_collisions_and_extreme_compression(extra):
    artifact = model_archive(extra=extra)
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda _path: pytest.fail("invalid package must not be loaded"),
    )

    with pytest.raises(ModelLoadError):
        service.load()


def test_runtime_prediction_returns_json_safe_identity_and_version(monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", SimpleNamespace(DataFrame=lambda rows: rows))
    artifact = model_archive()
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda _path: FakeModel(),
    )
    try:
        service.load()
        response = service.predict(PredictionRequest(inputs=[{"value": 2}, {"value": 3}]))
    finally:
        service.close()

    assert response.model_version == 4
    assert response.environment == "production"
    assert response.predictions == [4, 6]


def test_runtime_converts_dataframe_predictions_to_one_record_per_input(monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", SimpleNamespace(DataFrame=lambda rows: rows))
    artifact = model_archive()
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda _path: DataFrameModel(),
    )
    try:
        service.load()
        response = service.predict(PredictionRequest(inputs=[{"value": 2}, {"value": 3}]))
    finally:
        service.close()

    assert response.predictions == [{"score": 0.9}, {"score": 0.9}]


def test_runtime_rejects_incompatible_python_version_before_loading():
    import platform

    mismatch = "3.7.0" if platform.python_version_tuple()[0] != "3" else "2.7.18"
    artifact = model_archive(
        extra={
            "MLmodel": b"flavors:\n  python_function:\n    python_env: python_env.yaml\n",
            "python_env.yaml": f"python: {mismatch}\ndependencies: []\n".encode(),
        }
    )
    loaded = []
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda path: loaded.append(path),
    )

    with pytest.raises(ModelLoadError, match="Python version is incompatible"):
        service.load()

    assert loaded == []
    assert not service.ready


def test_runtime_checks_mlmodel_python_version_without_environment_file():
    artifact = model_archive(
        extra={"MLmodel": b"flavors:\n  python_function:\n    python_version: '2.7.18'\n"}
    )
    loaded = []
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda path: loaded.append(path),
    )

    with pytest.raises(ModelLoadError, match="Python version is incompatible"):
        service.load()

    assert loaded == []


def test_runtime_checks_root_requirements_without_environment_reference(monkeypatch):
    monkeypatch.setattr(
        "messqry.runtime.server.importlib.metadata.version",
        lambda _name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError),
    )
    artifact = model_archive(
        extra={
            "MLmodel": b"flavors:\n  python_function: {}\n",
            "requirements.txt": b"missing-messqry-root-requirement==1.0\n",
        }
    )
    loaded = []
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda path: loaded.append(path),
    )

    with pytest.raises(ModelLoadError, match="dependency is missing"):
        service.load()

    assert loaded == []


def test_runtime_accepts_versioned_mlflow_conda_build_tools(monkeypatch):
    monkeypatch.setattr(
        "messqry.runtime.server.importlib.metadata.version",
        lambda package: {"pip": "26.2.1", "setuptools": "84.0.0", "wheel": "0.45.0"}[package],
    )
    import platform

    artifact = model_archive(
        extra={
            "MLmodel": b"flavors:\n  python_function:\n    conda_env: conda.yaml\n",
            "conda.yaml": (
                f"dependencies:\n  - python={platform.python_version()}\n"
                "  - pip<=26.2.1\n  - setuptools<=84.0.0\n  - wheel\n  - pip: []\n"
            ).encode(),
        }
    )
    service = RuntimeService(
        runtime_config(artifact), s3_client=FakeS3(artifact), model_loader=lambda _path: FakeModel()
    )
    try:
        service.load()
        assert service.ready
    finally:
        service.close()


def test_runtime_rejects_missing_declared_package_before_loading(monkeypatch):
    import platform

    def missing_package(_name):
        raise importlib.metadata.PackageNotFoundError

    monkeypatch.setattr("messqry.runtime.server.importlib.metadata.version", missing_package)
    artifact = model_archive(
        extra={
            "MLmodel": b"flavors:\n  python_function:\n    python_env: python_env.yaml\n",
            "python_env.yaml": (
                f"python: {platform.python_version()}\ndependencies:\n  - -r requirements.txt\n"
            ).encode(),
            "requirements.txt": b"missing-messqry-test-dependency==1.0\n",
        }
    )
    loaded = []
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda path: loaded.append(path),
    )

    with pytest.raises(ModelLoadError, match="dependency is missing"):
        service.load()

    assert loaded == []
    assert not service.ready


def test_runtime_rejects_declared_package_version_mismatch_before_loading(monkeypatch):
    import platform

    monkeypatch.setattr("messqry.runtime.server.importlib.metadata.version", lambda _name: "1.0")
    artifact = model_archive(
        extra={
            "MLmodel": b"flavors:\n  python_function:\n    python_env: python_env.yaml\n",
            "python_env.yaml": (
                f"python: {platform.python_version()}\ndependencies:\n  - -r requirements.txt\n"
            ).encode(),
            "requirements.txt": b"sample-runtime-lib>=2.0\n",
        }
    )
    loaded = []
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda path: loaded.append(path),
    )

    with pytest.raises(ModelLoadError, match="dependency is incompatible"):
        service.load()

    assert loaded == []
    assert not service.ready


@pytest.mark.parametrize("value", [[float("nan")], {1: "not-json"}, [object()]])
def test_runtime_rejects_non_json_prediction_values(value):
    with pytest.raises(ValueError):
        _json_compatible(value)


def test_runtime_app_exposes_private_health_and_prediction_routes():
    app = create_app(service_factory=lambda: pytest.fail("startup is not run in this test"))
    endpoints = {route.path: route.endpoint for route in app.routes}

    assert "/health/ready" in endpoints
    assert "/predict" in endpoints
    with pytest.raises(HTTPException) as not_ready:
        endpoints["/health/ready"](SimpleNamespace(app=app))
    assert not_ready.value.status_code == 503


def test_runtime_app_health_and_prediction_responses_match_control_contract(monkeypatch):
    monkeypatch.setitem(sys.modules, "pandas", SimpleNamespace(DataFrame=lambda rows: rows))
    artifact = model_archive()
    service = RuntimeService(
        runtime_config(artifact),
        s3_client=FakeS3(artifact),
        model_loader=lambda _path: FakeModel(),
    )
    app = create_app(service_factory=lambda: service)
    service.load()
    app.state.runtime_service = service
    endpoints = {route.path: route.endpoint for route in app.routes}
    request = SimpleNamespace(app=app)
    try:
        assert endpoints["/health/ready"](request) == {
            "status": "ready",
            "model_version_id": "version-4",
        }
        response = endpoints["/predict"](
            PredictionRequest(inputs=[{"value": 5}]),
            request,
        )
    finally:
        service.close()

    assert response.model_id == service.config.model_id
    assert response.model_version == 4
    assert response.predictions == [10]
