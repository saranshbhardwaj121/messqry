import pytest

from messqry.config import Settings
from messqry.runtime.ecs import EcsFargateRuntime, RuntimeLaunchError, RuntimeTask


def ecs_settings(**overrides):
    values = {
        "runtime_backend": "ecs_fargate",
        "aws_region": "us-east-1",
        "artifact_storage": "s3",
        "artifact_s3_bucket": "messqry-private-artifacts",
        "ecs_cluster": "messqry-cluster",
        "ecs_task_definition": "messqry-runtime:1",
        "ecs_container_name": "model-runtime",
        "ecs_subnet_ids": "subnet-private-a,subnet-private-b",
        "ecs_security_group_ids": "sg-model-runtime",
    }
    values.update(overrides)
    return Settings(**values)


class FakeEcsClient:
    def __init__(self, descriptions):
        self.descriptions = iter(descriptions)
        self.run_request = None
        self.stopped = []

    def run_task(self, **request):
        self.run_request = request
        return {"tasks": [{"taskArn": "arn:aws:ecs:region:account:task/runtime-1"}]}

    def describe_tasks(self, **request):
        return {"tasks": [next(self.descriptions)]}

    def stop_task(self, **request):
        self.stopped.append(request)


def running_task(private_ip="10.0.1.17"):
    return {
        "lastStatus": "RUNNING",
        "attachments": [
            {
                "type": "ElasticNetworkInterface",
                "details": [{"name": "privateIPv4Address", "value": private_ip}],
            }
        ],
    }


def test_ecs_task_uses_selected_profile_and_private_network():
    client = FakeEcsClient([running_task()])
    runtime = EcsFargateRuntime(ecs_settings(), client=client)

    handle = runtime.start(
        deployment_id="deployment-1",
        deployment_revision=3,
        environment="staging",
        model_id="model-1",
        model_version_id="version-2",
        artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
        artifact_sha256="a" * 64,
    )

    assert handle == RuntimeTask(
        task_arn="arn:aws:ecs:region:account:task/runtime-1", private_ip="10.0.1.17"
    )
    request = client.run_request
    assert request["count"] == 1
    assert request["launchType"] == "FARGATE"
    network = request["networkConfiguration"]["awsvpcConfiguration"]
    assert network == {
        "subnets": ["subnet-private-a", "subnet-private-b"],
        "securityGroups": ["sg-model-runtime"],
        "assignPublicIp": "DISABLED",
    }
    assert request["overrides"]["cpu"] == "1024"
    assert request["overrides"]["memory"] == "2048"
    assert "ephemeralStorage" not in request["overrides"]
    assert request["enableECSManagedTags"] is True
    assert {entry["key"]: entry["value"] for entry in request["tags"]} == {
        "messqry:managed": "true",
        "messqry:deployment-id": "deployment-1",
        "messqry:deployment-revision": "3",
        "messqry:environment": "staging",
        "messqry:model-id": "model-1",
        "messqry:model-version-id": "version-2",
    }
    environment = request["overrides"]["containerOverrides"][0]["environment"]
    assert {entry["name"]: entry["value"] for entry in environment} == {
        "MESSQRY_MODEL_ID": "model-1",
        "MESSQRY_DEPLOYMENT_ID": "deployment-1",
        "MESSQRY_DEPLOYMENT_ENVIRONMENT": "staging",
        "MESSQRY_DEPLOYMENT_REVISION": "3",
        "MESSQRY_MODEL_VERSION_ID": "version-2",
        "MESSQRY_ARTIFACT_URI": "s3://messqry-private-artifacts/models/model-1/version-2.zip",
        "MESSQRY_ARTIFACT_SHA256": "a" * 64,
    }


def test_ecs_startup_timeout_stops_task_and_returns_bounded_error():
    now = 0.0
    stops = []

    def sleep(duration):
        nonlocal now
        now += duration

    client = FakeEcsClient([{"lastStatus": "PENDING"}] * 3)
    runtime = EcsFargateRuntime(
        ecs_settings(runtime_startup_timeout_seconds=2),
        client=client,
        monotonic=lambda: now,
        sleep=sleep,
        poll_interval_seconds=1,
    )
    with pytest.raises(RuntimeLaunchError, match="within 2 seconds"):
        runtime.start(
            deployment_id="deployment-1",
            deployment_revision=3,
            environment="staging",
            model_id="model-1",
            model_version_id="version-2",
            artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
            artifact_sha256="a" * 64,
        )
    stops.extend(client.stopped)
    assert len(stops) == 1
    assert stops[0]["reason"] == "MessQry runtime idle timeout"


def test_ecs_start_rejects_non_s3_artifact_before_launch():
    client = FakeEcsClient([running_task()])
    runtime = EcsFargateRuntime(ecs_settings(), client=client)

    with pytest.raises(ValueError, match="private S3 artifact URI"):
        runtime.start(
            deployment_id="deployment-1",
            deployment_revision=3,
            environment="staging",
            model_id="model-1",
            model_version_id="version-2",
            artifact_uri="file:///tmp/model.zip",
            artifact_sha256="a" * 64,
        )
    assert client.run_request is None

    with pytest.raises(ValueError, match="valid artifact SHA-256"):
        runtime.start(
            deployment_id="deployment-1",
            deployment_revision=3,
            environment="staging",
            model_id="model-1",
            model_version_id="version-2",
            artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
            artifact_sha256="not-a-digest",
        )
    assert client.run_request is None


def test_ecs_stopped_task_fails_start_and_attempts_cleanup():
    client = FakeEcsClient([{"lastStatus": "STOPPED"}])
    runtime = EcsFargateRuntime(ecs_settings(), client=client)

    with pytest.raises(RuntimeLaunchError, match="stopped before becoming ready"):
        runtime.start(
            deployment_id="deployment-1",
            deployment_revision=3,
            environment="staging",
            model_id="model-1",
            model_version_id="version-2",
            artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
            artifact_sha256="a" * 64,
        )
    assert len(client.stopped) == 1
