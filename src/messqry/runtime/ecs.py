"""ECS Fargate task lifecycle adapter for isolated model runtimes."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from messqry.config import Settings
from messqry.runtime.transport import (
    RuntimeTransportError,
    encode_prediction_payload,
    http_get_private,
    predict_from_runtime,
)

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
logger = logging.getLogger("messqry.runtime.ecs")


class RuntimeLaunchError(RuntimeError):
    """A model runtime could not be started or reached privately."""


@dataclass(frozen=True)
class RuntimeTask:
    """Running model task and its private inference address."""

    task_arn: str
    private_ip: str


@dataclass
class _WarmTask:
    task: RuntimeTask
    active_requests: int = 0
    timer: threading.Timer | None = None
    timer_generation: int = 0
    invalidated: bool = False


@dataclass
class _StartupGate:
    lock: threading.Lock = field(default_factory=threading.Lock)
    users: int = 0


class EcsFargateRuntime:
    """Launch one private 1-vCPU/2-GiB Fargate task and resolve its private IP."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        poll_interval_seconds: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        health_probe: Callable[[str, int, str, float], bool] | None = None,
        prediction_transport: Callable[..., Any] = predict_from_runtime,
        timer_factory: Callable[..., threading.Timer] = threading.Timer,
    ) -> None:
        if settings.runtime_backend != "ecs_fargate":
            raise ValueError("ECS runtime settings are not enabled")
        self.settings = settings
        if client is None:
            try:
                import boto3
                from botocore.config import Config
            except ImportError as exc:
                raise RuntimeError('Install the "aws" extra to use ECS Fargate runtimes') from exc
            client = boto3.client(
                "ecs",
                region_name=settings.aws_region,
                config=Config(
                    connect_timeout=2,
                    read_timeout=5,
                    retries={"total_max_attempts": 2},
                ),
            )
        self.client = client
        self.poll_interval_seconds = poll_interval_seconds
        self.monotonic = monotonic
        self.sleep = sleep
        self.health_probe = health_probe or self._probe_model_health
        self.prediction_transport = prediction_transport
        self.timer_factory = timer_factory
        self._warm_tasks: dict[tuple[str, int], _WarmTask] = {}
        self._startup_locks: dict[tuple[str, int], _StartupGate] = {}
        self._warm_tasks_lock = threading.RLock()
        self._closed = False

    def start(
        self,
        *,
        deployment_id: str,
        deployment_revision: int,
        environment: str,
        model_id: str,
        model_version_id: str,
        model_version: int | None = None,
        artifact_uri: str,
        artifact_sha256: str,
    ) -> RuntimeTask:
        """Start a private task for one deployment revision and wait for its IP."""

        self._validate_artifact_uri(artifact_uri)
        if not _SHA256_PATTERN.fullmatch(artifact_sha256):
            raise ValueError("ECS model runtime requires a valid artifact SHA-256")
        if deployment_revision < 1 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", environment):
            raise ValueError(
                "ECS model runtime requires a valid deployment revision and environment"
            )
        deadline = self.monotonic() + self.settings.runtime_startup_timeout_seconds
        try:
            response = self.client.run_task(
                cluster=self.settings.ecs_cluster.strip(),
                taskDefinition=self.settings.ecs_task_definition.strip(),
                count=1,
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": list(self.settings.ecs_subnets),
                        "securityGroups": list(self.settings.ecs_security_groups),
                        "assignPublicIp": "DISABLED",
                    }
                },
                overrides={
                    "cpu": "1024",
                    "memory": "2048",
                    # Omit ephemeralStorage to use Fargate's included 20 GiB default.
                    "containerOverrides": [
                        {
                            "name": self.settings.ecs_container_name.strip(),
                            "environment": [
                                {"name": "MESSQRY_MODEL_ID", "value": model_id},
                                {"name": "MESSQRY_DEPLOYMENT_ID", "value": deployment_id},
                                {"name": "MESSQRY_DEPLOYMENT_ENVIRONMENT", "value": environment},
                                {
                                    "name": "MESSQRY_DEPLOYMENT_REVISION",
                                    "value": str(deployment_revision),
                                },
                                {
                                    "name": "MESSQRY_MODEL_VERSION_ID",
                                    "value": model_version_id,
                                },
                                {"name": "MESSQRY_ARTIFACT_URI", "value": artifact_uri},
                                {"name": "MESSQRY_ARTIFACT_SHA256", "value": artifact_sha256},
                            ]
                            + (
                                [{"name": "MESSQRY_MODEL_VERSION", "value": str(model_version)}]
                                if model_version is not None
                                else []
                            ),
                        }
                    ],
                },
                enableECSManagedTags=True,
                tags=[
                    {"key": "messqry:managed", "value": "true"},
                    {"key": "messqry:deployment-id", "value": deployment_id},
                    {"key": "messqry:deployment-revision", "value": str(deployment_revision)},
                    {"key": "messqry:environment", "value": environment},
                    {"key": "messqry:model-id", "value": model_id},
                    {"key": "messqry:model-version-id", "value": model_version_id},
                ],
            )
        except Exception as exc:
            raise RuntimeLaunchError("ECS could not start the model runtime") from exc

        tasks = response.get("tasks", [])
        if response.get("failures") or len(tasks) != 1:
            raise RuntimeLaunchError("ECS did not accept the model runtime task")
        task_arn = tasks[0].get("taskArn")
        if not task_arn:
            raise RuntimeLaunchError("ECS returned a task without an identifier")
        return self._wait_until_running(task_arn, deadline, model_version_id)

    def stop(self, task_arn: str) -> None:
        """Stop one task after its configured warm period expires."""

        self.client.stop_task(
            cluster=self.settings.ecs_cluster.strip(),
            task=task_arn,
            reason="MessQry runtime idle timeout",
        )

    def predict(
        self,
        *,
        deployment_id: str,
        deployment_revision: int,
        environment: str,
        model_id: str,
        model_version_id: str,
        model_version: int,
        artifact_uri: str,
        artifact_sha256: str,
        payload: dict[str, Any],
    ):
        """Reuse a revision-matched task and stop it after its idle grace period."""

        # Validate the actual UTF-8 wire representation before any potentially expensive launch.
        encode_prediction_payload(payload)
        cache_key = (deployment_id, deployment_revision)
        with self._warm_tasks_lock:
            if self._closed:
                raise RuntimeLaunchError("Model runtime manager is shutting down")
            warm_task = self._warm_tasks.get(cache_key)
            if warm_task is not None and warm_task.invalidated:
                raise RuntimeLaunchError("Model runtime is recovering from a failed request")
            startup_lock = (
                None
                if warm_task is not None
                else self._startup_locks.setdefault(cache_key, _StartupGate())
            )
            if warm_task is not None:
                self._reserve_warm_task(warm_task)
            else:
                startup_lock.users += 1

        if warm_task is None:
            assert startup_lock is not None
            startup_lock.lock.acquire()
            try:
                with self._warm_tasks_lock:
                    if self._closed:
                        raise RuntimeLaunchError("Model runtime manager is shutting down")
                    warm_task = self._warm_tasks.get(cache_key)
                    if warm_task is not None and warm_task.invalidated:
                        raise RuntimeLaunchError(
                            "Model runtime is recovering from a failed request"
                        )
                    if warm_task is not None:
                        self._reserve_warm_task(warm_task)

                if warm_task is None:
                    task = self.start(
                        deployment_id=deployment_id,
                        deployment_revision=deployment_revision,
                        environment=environment,
                        model_id=model_id,
                        model_version_id=model_version_id,
                        model_version=model_version,
                        artifact_uri=artifact_uri,
                        artifact_sha256=artifact_sha256,
                    )
                    with self._warm_tasks_lock:
                        closed = self._closed
                        if not closed:
                            warm_task = _WarmTask(task=task, active_requests=1)
                            self._warm_tasks[cache_key] = warm_task
                    if closed:
                        self._stop_failed_start(task.task_arn)
                        raise RuntimeLaunchError("Model runtime manager is shutting down")
            finally:
                startup_lock.lock.release()
                with self._warm_tasks_lock:
                    startup_lock.users -= 1
                    if (
                        startup_lock.users == 0
                        and self._startup_locks.get(cache_key) is startup_lock
                    ):
                        self._startup_locks.pop(cache_key, None)

        assert warm_task is not None

        succeeded = False
        try:
            result = self.prediction_transport(
                warm_task.task.private_ip,
                self.settings.runtime_port,
                payload,
                model_id=model_id,
                environment=environment,
                model_version=model_version,
                timeout_seconds=30,
            )
            succeeded = True
            return result
        finally:
            task_to_stop = None
            with self._warm_tasks_lock:
                warm_task.active_requests -= 1
                if not succeeded:
                    warm_task.invalidated = True
                if warm_task.invalidated and warm_task.active_requests == 0:
                    if self._warm_tasks.get(cache_key) is warm_task:
                        self._warm_tasks.pop(cache_key, None)
                    if warm_task.timer is not None:
                        warm_task.timer.cancel()
                    warm_task.timer_generation += 1
                    task_to_stop = warm_task.task.task_arn
                elif self._warm_tasks.get(cache_key) is warm_task:
                    if succeeded and warm_task.active_requests == 0:
                        warm_task.timer_generation += 1
                        generation = warm_task.timer_generation
                        timer = self.timer_factory(
                            self.settings.runtime_idle_timeout_seconds,
                            self._stop_if_idle,
                            args=(cache_key, warm_task, generation),
                        )
                        timer.daemon = True
                        warm_task.timer = timer
                        timer.start()
            if task_to_stop is not None:
                self._stop_failed_start(task_to_stop)

    @staticmethod
    def _reserve_warm_task(warm_task: _WarmTask) -> None:
        if warm_task.timer is not None:
            warm_task.timer.cancel()
            warm_task.timer = None
        warm_task.timer_generation += 1
        warm_task.active_requests += 1

    def _stop_if_idle(
        self, cache_key: tuple[str, int], warm_task: _WarmTask, generation: int
    ) -> None:
        with self._warm_tasks_lock:
            if (
                self._warm_tasks.get(cache_key) is not warm_task
                or warm_task.active_requests
                or warm_task.timer_generation != generation
            ):
                return
            self._warm_tasks.pop(cache_key, None)
        self._stop_failed_start(warm_task.task.task_arn)

    def close(self) -> None:
        """Stop cached tasks and release the ECS client during API shutdown."""

        with self._warm_tasks_lock:
            self._closed = True
            warm_tasks = list(self._warm_tasks.values())
            self._warm_tasks.clear()
        for warm_task in warm_tasks:
            if warm_task.timer is not None:
                warm_task.timer.cancel()
            self._stop_failed_start(warm_task.task.task_arn)
        close = getattr(self.client, "close", None)
        if close is not None:
            close()

    def _wait_until_running(
        self, task_arn: str, deadline: float, model_version_id: str
    ) -> RuntimeTask:
        while True:
            if self.monotonic() >= deadline:
                self._stop_failed_start(task_arn)
                raise RuntimeLaunchError(
                    "ECS runtime did not start within "
                    f"{self.settings.runtime_startup_timeout_seconds} seconds"
                )
            try:
                response = self.client.describe_tasks(
                    cluster=self.settings.ecs_cluster.strip(), tasks=[task_arn]
                )
            except Exception as exc:
                self._stop_failed_start(task_arn)
                raise RuntimeLaunchError("ECS runtime status could not be read") from exc

            if response.get("failures") or not response.get("tasks"):
                self._stop_failed_start(task_arn)
                raise RuntimeLaunchError("ECS runtime task disappeared during startup")

            task = response["tasks"][0]
            if task.get("lastStatus") == "STOPPED":
                self._stop_failed_start(task_arn)
                raise RuntimeLaunchError("ECS runtime task stopped before becoming ready")
            if task.get("lastStatus") == "RUNNING":
                private_ip = self._private_ip(task)
                remaining = deadline - self.monotonic()
                if private_ip and remaining > 0:
                    try:
                        healthy = self.health_probe(
                            private_ip,
                            self.settings.runtime_port,
                            model_version_id,
                            min(2.0, remaining),
                        )
                    except Exception:
                        healthy = False
                    if healthy:
                        return RuntimeTask(task_arn=task_arn, private_ip=private_ip)

            remaining = deadline - self.monotonic()
            if remaining <= 0:
                self._stop_failed_start(task_arn)
                raise RuntimeLaunchError(
                    "ECS runtime did not start within "
                    f"{self.settings.runtime_startup_timeout_seconds} seconds"
                )
            self.sleep(min(self.poll_interval_seconds, remaining))

    def _stop_failed_start(self, task_arn: str) -> None:
        try:
            self.stop(task_arn)
        except Exception as exc:
            # Preserve the startup failure; the task may already have stopped.
            logger.warning("ECS runtime task cleanup failed (%s)", type(exc).__name__)

    @staticmethod
    def _probe_model_health(
        private_ip: str, port: int, model_version_id: str, timeout: float
    ) -> bool:
        """Return true only after the runtime confirms its loaded model version."""

        try:
            status, body = http_get_private(private_ip, port, "/health/ready", timeout)
            if status != 200 or len(body) > 4096:
                return False
            payload = json.loads(body)
        except (RuntimeTransportError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(payload, dict)
            and payload.get("status") == "ready"
            and payload.get("model_version_id") == model_version_id
        )

    @staticmethod
    def _private_ip(task: dict[str, Any]) -> str | None:
        for attachment in task.get("attachments", []):
            if attachment.get("type") != "ElasticNetworkInterface":
                continue
            details = {
                item.get("name"): item.get("value") for item in attachment.get("details", [])
            }
            address = details.get("privateIPv4Address")
            if address:
                return address
        return None

    @staticmethod
    def _validate_artifact_uri(artifact_uri: str) -> None:
        parsed = urlparse(artifact_uri)
        if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/"):
            raise ValueError("ECS model runtimes require a private S3 artifact URI")


def create_runtime_manager(settings: Settings, *, ecs_client: Any | None = None):
    """Create the configured runtime manager, leaving local development disabled."""

    if settings.runtime_backend == "disabled":
        return None
    return EcsFargateRuntime(settings, client=ecs_client)
