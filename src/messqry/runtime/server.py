"""Isolated HTTP server for one validated MLflow pyfunc model."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import logging
import math
import os
import platform
import re
import tempfile
import zipfile
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

import yaml
from fastapi import FastAPI, HTTPException, Request, status
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from pydantic import ValidationError

from messqry.api.schemas import PredictionRequest, PredictionResponse

MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
MAX_COMPRESSION_RATIO = 200
MAX_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MAX_ENVIRONMENT_FILE_BYTES = 256 * 1024
MAX_REQUIREMENT_LINES = 512
ENVIRONMENT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
logger = logging.getLogger("messqry.runtime.server")


class RuntimeConfigurationError(ValueError):
    """Runtime task environment is incomplete or invalid."""


class ModelLoadError(RuntimeError):
    """Artifact retrieval or isolated model loading failed."""


@dataclass(frozen=True)
class RuntimeConfig:
    model_id: UUID
    deployment_id: str
    environment: str
    deployment_revision: int
    model_version_id: str
    model_version: int
    artifact_uri: str
    artifact_sha256: str
    artifact_version_id: str | None
    aws_region: str
    port: int = 8000

    @classmethod
    def from_environment(cls, environ: Mapping[str, str] | None = None) -> RuntimeConfig:
        source = os.environ if environ is None else environ
        required = (
            "MESSQRY_MODEL_ID",
            "MESSQRY_DEPLOYMENT_ID",
            "MESSQRY_DEPLOYMENT_ENVIRONMENT",
            "MESSQRY_DEPLOYMENT_REVISION",
            "MESSQRY_MODEL_VERSION_ID",
            "MESSQRY_MODEL_VERSION",
            "MESSQRY_ARTIFACT_URI",
            "MESSQRY_ARTIFACT_SHA256",
            "AWS_REGION",
        )
        missing = [key for key in required if not source.get(key)]
        if missing:
            raise RuntimeConfigurationError(
                "Runtime task is missing required configuration: " + ", ".join(missing)
            )
        try:
            model_id = UUID(source["MESSQRY_MODEL_ID"])
            deployment_revision = int(source["MESSQRY_DEPLOYMENT_REVISION"])
            model_version = int(source["MESSQRY_MODEL_VERSION"])
            port = int(source.get("MESSQRY_RUNTIME_PORT", "8000"))
        except (ValueError, TypeError) as exc:
            raise RuntimeConfigurationError("Runtime task configuration is invalid") from exc

        environment = source["MESSQRY_DEPLOYMENT_ENVIRONMENT"]
        artifact_uri = source["MESSQRY_ARTIFACT_URI"]
        digest = source["MESSQRY_ARTIFACT_SHA256"]
        parsed = urlparse(artifact_uri)
        if (
            not ENVIRONMENT_PATTERN.fullmatch(environment)
            or deployment_revision < 1
            or model_version < 1
            or not source["MESSQRY_MODEL_VERSION_ID"].strip()
            or not SHA256_PATTERN.fullmatch(digest)
            or parsed.scheme != "s3"
            or not parsed.netloc
            or not parsed.path.strip("/")
            or parsed.query
            or parsed.fragment
            or not 1 <= port <= 65535
        ):
            raise RuntimeConfigurationError("Runtime task configuration is invalid")

        return cls(
            model_id=model_id,
            deployment_id=source["MESSQRY_DEPLOYMENT_ID"],
            environment=environment,
            deployment_revision=deployment_revision,
            model_version_id=source["MESSQRY_MODEL_VERSION_ID"],
            model_version=model_version,
            artifact_uri=artifact_uri,
            artifact_sha256=digest,
            artifact_version_id=source.get("MESSQRY_ARTIFACT_VERSION_ID") or None,
            aws_region=source["AWS_REGION"],
            port=port,
        )


class RuntimeService:
    """Fetch, verify, load, and invoke a single model for an ECS task."""

    def __init__(
        self,
        config: RuntimeConfig,
        *,
        s3_client: Any | None = None,
        model_loader: Callable[[str], Any] | None = None,
        temp_directory: Callable[[], tempfile.TemporaryDirectory] = tempfile.TemporaryDirectory,
    ) -> None:
        self.config = config
        self.s3_client = s3_client
        self.model_loader = model_loader or _load_pyfunc
        self.temp_directory_factory = temp_directory
        self._work_dir: tempfile.TemporaryDirectory | None = None
        self._model: Any | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        if self.ready:
            return
        work_dir = self.temp_directory_factory(prefix="messqry-runtime-")
        root = Path(work_dir.name)
        archive_path = root / "model.zip"
        model_path = root / "model"
        try:
            self._download_artifact(archive_path)
            self._extract_package(archive_path, model_path)
            _validate_runtime_environment(model_path)
            model = self.model_loader(str(model_path))
        except Exception as exc:
            work_dir.cleanup()
            if isinstance(exc, ModelLoadError):
                raise
            logger.warning("Model runtime startup failed (%s)", type(exc).__name__)
            raise ModelLoadError("Model artifact could not be loaded") from exc
        self._work_dir = work_dir
        self._model = model

    def close(self) -> None:
        model, self._model = self._model, None
        work_dir, self._work_dir = self._work_dir, None
        try:
            model_close = getattr(model, "close", None)
            if callable(model_close):
                model_close()
        except Exception as exc:
            logger.warning("Model runtime shutdown failed (%s)", type(exc).__name__)
        finally:
            if work_dir is not None:
                work_dir.cleanup()

    def readiness(self) -> dict[str, str]:
        if not self.ready:
            raise RuntimeError("Model is not loaded")
        return {"status": "ready", "model_version_id": self.config.model_version_id}

    def predict(self, payload: PredictionRequest) -> PredictionResponse:
        if self._model is None:
            raise RuntimeError("Model is not loaded")
        try:
            import pandas as pd

            result = self._model.predict(pd.DataFrame(payload.inputs))
            predictions = _json_compatible(result)
            if not isinstance(predictions, list) or len(predictions) != len(payload.inputs):
                raise ValueError("Model returned an unexpected prediction count")
            return PredictionResponse(
                model_id=self.config.model_id,
                environment=self.config.environment,
                model_version=self.config.model_version,
                predictions=predictions,
            )
        except (ValidationError, ValueError, TypeError, OverflowError) as exc:
            logger.warning("Model inference returned invalid output (%s)", type(exc).__name__)
            raise RuntimeError("Model inference failed") from exc
        except Exception as exc:
            logger.warning("Model inference failed (%s)", type(exc).__name__)
            raise RuntimeError("Model inference failed") from exc

    def _download_artifact(self, destination: Path) -> None:
        if self.s3_client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise ModelLoadError('Install the "runtime" extra to load model artifacts') from exc
            self.s3_client = boto3.client(
                "s3",
                region_name=self.config.aws_region,
                config=Config(
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"total_max_attempts": 2},
                ),
            )

        parsed = urlparse(self.config.artifact_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        request = {"Bucket": bucket, "Key": key}
        if self.config.artifact_version_id is not None:
            request["VersionId"] = self.config.artifact_version_id
        response = self.s3_client.get_object(**request)
        body = response["Body"]
        digest = hashlib.sha256()
        total = 0
        try:
            with destination.open("wb") as output:
                while True:
                    chunk = body.read(MAX_DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ModelLoadError("Model archive exceeds the runtime size limit")
                    digest.update(chunk)
                    output.write(chunk)
        finally:
            close = getattr(body, "close", None)
            if close is not None:
                close()
        if total == 0 or digest.hexdigest() != self.config.artifact_sha256:
            raise ModelLoadError("Model artifact checksum does not match its registry record")

    @staticmethod
    def _extract_package(archive_path: Path, destination: Path) -> None:
        destination.mkdir(mode=0o700, parents=True)
        root = destination.resolve()
        try:
            with zipfile.ZipFile(archive_path) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > MAX_ARCHIVE_FILES:
                    raise ModelLoadError("Model archive exceeds the runtime file-count limit")
                files: set[str] = set()
                folded_files: set[str] = set()
                normalized: set[str] = set()
                casefolded: set[str] = set()
                expanded = 0
                for info in infos:
                    name = info.filename
                    if not name or "\\" in name or "\x00" in name or name.startswith("/"):
                        raise ModelLoadError("Model archive contains an unsafe path")
                    path = name[:-1] if info.is_dir() and name.endswith("/") else name
                    parts = path.split("/")
                    if any(part in {"", ".", ".."} or ":" in part for part in parts):
                        raise ModelLoadError("Model archive contains an unsafe path")
                    clean = PurePosixPath(path).as_posix()
                    folded = clean.casefold()
                    if clean in normalized or folded in casefolded:
                        raise ModelLoadError("Model archive contains duplicate paths")
                    normalized.add(clean)
                    casefolded.add(folded)
                    mode = info.external_attr >> 16
                    if mode and (mode & 0o170000) not in {0, 0o040000, 0o100000}:
                        raise ModelLoadError("Model archive contains a special file")
                    if info.flag_bits & 0x1:
                        raise ModelLoadError("Encrypted model archive entries are unsupported")
                    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                        raise ModelLoadError("Model archive compression is unsupported")
                    if info.file_size < 0 or info.compress_size < 0:
                        raise ModelLoadError("Model archive entry sizes are invalid")
                    if (
                        info.compress_size
                        and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                    ):
                        raise ModelLoadError("Model archive compression ratio is too large")
                    expanded += info.file_size
                    if expanded > MAX_EXPANDED_BYTES:
                        raise ModelLoadError("Model archive exceeds the expanded size limit")
                    if not info.is_dir():
                        files.add(clean)
                        folded_files.add(folded)

                for path in folded_files:
                    parts = path.split("/")
                    if any(
                        "/".join(parts[:index]) in folded_files for index in range(1, len(parts))
                    ):
                        raise ModelLoadError("Model archive contains a file/directory conflict")

                if "MLmodel" not in files:
                    raise ModelLoadError("Model archive does not contain a root MLmodel file")
                for info in infos:
                    relative_name = info.filename[:-1] if info.is_dir() else info.filename
                    target = (destination / PurePosixPath(relative_name)).resolve()
                    if not target.is_relative_to(root):
                        raise ModelLoadError("Model archive contains an unsafe path")
                    if info.is_dir():
                        target.mkdir(mode=0o700, parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    actual = 0
                    with archive.open(info) as source, target.open("xb") as output:
                        while chunk := source.read(MAX_DOWNLOAD_CHUNK_BYTES):
                            actual += len(chunk)
                            if actual > info.file_size:
                                raise ModelLoadError(
                                    "Model archive entry exceeds its declared size"
                                )
                            output.write(chunk)
                    if actual != info.file_size:
                        raise ModelLoadError("Model archive entry size does not match its metadata")
        except ModelLoadError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, ValueError) as exc:
            raise ModelLoadError("Model artifact is not a supported ZIP package") from exc


def _load_pyfunc(model_path: str) -> Any:
    try:
        mlflow = importlib.import_module("mlflow")
    except ImportError as exc:
        raise ModelLoadError('Install the "runtime" extra to load MLflow models') from exc
    return mlflow.pyfunc.load_model(model_path)


def _read_environment_yaml(path: Path) -> dict[str, Any]:
    try:
        if path.stat().st_size > MAX_ENVIRONMENT_FILE_BYTES:
            raise ModelLoadError("Model environment metadata exceeds the runtime limit")
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError, RecursionError) as exc:
        raise ModelLoadError("Model environment metadata is invalid") from exc
    if not isinstance(value, dict):
        raise ModelLoadError("Model environment metadata must be a mapping")
    return value


def _validate_python_version(value: Any) -> None:
    if not isinstance(value, str):
        raise ModelLoadError("Model Python version declaration is invalid")
    match = re.fullmatch(r"\s*(\d+)\.(\d+)(?:\.\d+)?\s*", value)
    current = platform.python_version_tuple()
    if match is None or (match.group(1), match.group(2)) != current[:2]:
        raise ModelLoadError("Model Python version is incompatible with the runtime image")


def _validate_requirements(requirements: list[Any]) -> None:
    if len(requirements) > MAX_REQUIREMENT_LINES:
        raise ModelLoadError("Model declares too many runtime dependencies")
    for raw in requirements:
        if not isinstance(raw, str) or len(raw) > 4096:
            raise ModelLoadError("Model dependency declaration is invalid")
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("-", ".", "/")):
            raise ModelLoadError("Model uses an unsupported dependency declaration")
        # MLflow's locked requirements may include pip hash options.
        line = re.sub(r"\s+--hash=[^\s]+", "", line)
        try:
            requirement = Requirement(line)
        except InvalidRequirement as exc:
            raise ModelLoadError("Model dependency declaration is invalid") from exc
        if requirement.url is not None:
            raise ModelLoadError("Model direct-URL dependencies are unsupported")
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = importlib.metadata.version(canonicalize_name(requirement.name))
        except importlib.metadata.PackageNotFoundError as exc:
            raise ModelLoadError(
                f"Model dependency is missing from the runtime image: {requirement.name}"
            ) from exc
        if requirement.specifier and not requirement.specifier.contains(
            installed, prereleases=True
        ):
            raise ModelLoadError(
                f"Model dependency is incompatible with the runtime image: {requirement.name}"
            )


def _validate_runtime_environment(model_path: Path) -> None:
    """Reject declared Python/package requirements the baked image cannot satisfy."""
    metadata = _read_environment_yaml(model_path / "MLmodel")
    python_function = metadata.get("flavors", {}).get("python_function", {})
    if not isinstance(python_function, dict):
        return

    def environment_file(value: str) -> Path:
        candidate = (model_path / value).resolve()
        if not candidate.is_relative_to(model_path.resolve()):
            raise ModelLoadError("Model environment path is unsafe")
        return candidate

    def validate_requirements_file(path: Path, *, unavailable_message: str) -> None:
        try:
            if path.stat().st_size > MAX_ENVIRONMENT_FILE_BYTES:
                raise ModelLoadError("Model requirements exceed the runtime limit")
            requirements = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as exc:
            raise ModelLoadError(unavailable_message) from exc
        _validate_requirements(requirements)

    python_version = python_function.get("python_version")
    if python_version is not None:
        _validate_python_version(python_version)

    # MLflow packages conventionally include root requirements.txt even when the
    # flavor metadata does not reference a separate environment file.
    root_requirements = model_path / "requirements.txt"
    if root_requirements.exists():
        validate_requirements_file(
            root_requirements, unavailable_message="Model requirements file is unavailable"
        )

    python_env_path = python_function.get("python_env")
    if python_env_path is None:
        environment_paths = python_function.get("env", {})
        if isinstance(environment_paths, dict):
            python_env_path = environment_paths.get("virtualenv")
    if python_env_path is not None:
        if not isinstance(python_env_path, str):
            raise ModelLoadError("Model Python environment path is invalid")
        environment = _read_environment_yaml(environment_file(python_env_path))
        _validate_python_version(environment.get("python"))
        dependencies = environment.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ModelLoadError("Model dependency metadata is invalid")
        for dependency in dependencies:
            if not isinstance(dependency, str) or not dependency.startswith("-r "):
                raise ModelLoadError("Model environment contains unsupported dependencies")
            requirements_path = environment_file(dependency[3:].strip())
            validate_requirements_file(
                requirements_path, unavailable_message="Model requirements file is unavailable"
            )

    conda_path = python_function.get("conda_env")
    if conda_path is None:
        environment_paths = python_function.get("env", {})
        if isinstance(environment_paths, dict):
            conda_path = environment_paths.get("conda")
    if conda_path is not None:
        if not isinstance(conda_path, str):
            raise ModelLoadError("Model Conda environment path is invalid")
        conda_environment = _read_environment_yaml(environment_file(conda_path))
        dependencies = conda_environment.get("dependencies", [])
        if not isinstance(dependencies, list):
            raise ModelLoadError("Model Conda dependencies are invalid")
        pip_requirements: list[Any] = []
        for dependency in dependencies:
            if isinstance(dependency, str):
                match = re.fullmatch(
                    r"(python|pip|setuptools|wheel)(<=|>=|==|=|<|>|!=)?([\d.]+)?", dependency
                )
                if match is not None:
                    package, operator, version = match.groups()
                    if package == "python":
                        if operator not in {"=", "==", ">=", "<="} or version is None:
                            raise ModelLoadError("Model Python version declaration is invalid")
                        _validate_python_version(version)
                    elif operator is not None:
                        if version is None:
                            raise ModelLoadError("Model build dependency declaration is invalid")
                        normalized_operator = "==" if operator == "=" else operator
                        pip_requirements.append(f"{package}{normalized_operator}{version}")
                    continue
                raise ModelLoadError("Conda-only model dependencies are unsupported")
            if isinstance(dependency, dict) and set(dependency) == {"pip"}:
                if not isinstance(dependency["pip"], list):
                    raise ModelLoadError("Model pip dependencies are invalid")
                pip_requirements.extend(dependency["pip"])
                continue
            raise ModelLoadError("Model Conda dependencies are unsupported")
        _validate_requirements(pip_requirements)


def _json_compatible(value: Any) -> Any:
    if value.__class__.__module__.startswith("pandas") and value.__class__.__name__ == "DataFrame":
        value = value.to_dict(orient="records")
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Prediction object keys must be strings")
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Model prediction contains a non-finite number")
        return value
    raise ValueError("Model prediction contains a non-JSON value")


def create_app(
    *,
    service_factory: Callable[[], RuntimeService] | None = None,
) -> FastAPI:
    """Create the runtime app; model loading completes before readiness is served."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        service = (
            service_factory()
            if service_factory is not None
            else RuntimeService(RuntimeConfig.from_environment())
        )
        application.state.runtime_service = service
        try:
            service.load()
            yield
        finally:
            service.close()

    application = FastAPI(
        title="MessQry isolated model runtime",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.get("/health/ready")
    def health_ready(request: Request) -> dict[str, str]:
        service: RuntimeService | None = getattr(request.app.state, "runtime_service", None)
        if service is None or not service.ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model runtime is not ready",
            )
        return service.readiness()

    @application.post("/predict", response_model=PredictionResponse)
    def predict(payload: PredictionRequest, request: Request) -> PredictionResponse:
        service: RuntimeService | None = getattr(request.app.state, "runtime_service", None)
        if service is None or not service.ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Model runtime is not ready",
            )
        try:
            return service.predict(payload)
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Model inference failed",
            ) from exc

    return application


app = create_app()
