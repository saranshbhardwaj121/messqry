"""ECS Fargate task lifecycle adapter for isolated model runtimes."""

from __future__ import annotations

import re
import json
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

from messqry.config import Settings

_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


class RuntimeLaunchError(RuntimeError):
    """A model runtime could not be started or reached privately."""


@dataclass(frozen=True)
class RuntimeTask:
    """Running model task and its private inference address."""

    task_arn: str
    private_ip: str


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

    def start(
        self,
        *,
        deployment_id: str,
        deployment_revision: int,
        environment: str,
        model_id: str,
        model_version_id: str,
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
                            ],
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
        except Exception:
            # Preserve the startup failure; the task may already have stopped.
            pass

    @staticmethod
    def _probe_model_health(private_ip: str, port: int, model_version_id: str, timeout: float) -> bool:
        """Return true only after the runtime confirms its loaded model version."""

        try:
            with urlopen(f"http://{private_ip}:{port}/health/ready", timeout=timeout) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read(4096))
        except (OSError, URLError, ValueError, json.JSONDecodeError):
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
