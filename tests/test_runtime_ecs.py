import threading
import time

import pytest

from messqry.config import Settings
from messqry.runtime.ecs import EcsFargateRuntime, RuntimeLaunchError, RuntimeTask
from messqry.runtime.transport import RuntimeTransportError


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
        self.run_calls = 0
        self.task_definition = {
            "taskDefinitionArn": "arn:aws:ecs:us-east-1:123456789012:task-definition/messqry-runtime:1",
            "requiresCompatibilities": ["FARGATE"],
            "networkMode": "awsvpc",
            "volumes": [{"name": "runtime-tmp"}],
            "containerDefinitions": [
                {
                    "name": "model-runtime",
                    "user": "10001:10001",
                    "privileged": False,
                    "readonlyRootFilesystem": True,
                    "mountPoints": [
                        {
                            "sourceVolume": "runtime-tmp",
                            "containerPath": "/tmp/messqry",
                            "readOnly": False,
                        }
                    ],
                }
            ],
        }
        self.task_definition_request = None

    def describe_task_definition(self, **request):
        self.task_definition_request = request
        return {"taskDefinition": self.task_definition}

    def run_task(self, **request):
        self.run_calls += 1
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
    probes = []

    def health_probe(ip, port, version, timeout):
        probes.append((ip, port, version, timeout))
        return True

    runtime = EcsFargateRuntime(ecs_settings(), client=client, health_probe=health_probe)

    handle = runtime.start(
        deployment_id="deployment-1",
        deployment_revision=3,
        environment="staging",
        model_id="model-1",
        model_version_id="version-2",
        artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
        artifact_sha256="a" * 64,
        artifact_version_id="s3-version-1",
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
    assert client.task_definition_request == {"taskDefinition": "messqry-runtime:1"}
    assert request["taskDefinition"] == client.task_definition["taskDefinitionArn"]
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
        "MESSQRY_ARTIFACT_VERSION_ID": "s3-version-1",
        "MESSQRY_RUNTIME_PORT": "8000",
        "AWS_REGION": "us-east-1",
    }
    assert probes[0][:3] == ("10.0.1.17", 8000, "version-2")
    assert 0 < probes[0][3] <= 2


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
        health_probe=lambda *_: True,
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
    runtime = EcsFargateRuntime(ecs_settings(), client=client, health_probe=lambda *_: True)

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


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda definition: definition.update(networkMode="bridge"), "Fargate awsvpc"),
        (
            lambda definition: definition["containerDefinitions"][0].update(privileged=True),
            "must not be privileged",
        ),
        (
            lambda definition: definition["containerDefinitions"][0].update(
                readonlyRootFilesystem=False
            ),
            "read-only root filesystem",
        ),
        (
            lambda definition: definition["containerDefinitions"][0].update(user="0:0"),
            "non-root numeric user",
        ),
        (
            lambda definition: definition["containerDefinitions"][0].pop("user"),
            "explicitly use a non-root numeric user",
        ),
        (
            lambda definition: definition["containerDefinitions"][0].update(user="messqry"),
            "explicitly use a non-root numeric user",
        ),
        (
            lambda definition: definition["containerDefinitions"][0].update(mountPoints=[]),
            "writable volume at /tmp/messqry",
        ),
        (
            lambda definition: definition["containerDefinitions"][0]["mountPoints"][0].update(
                readOnly=True
            ),
            "writable volume at /tmp/messqry",
        ),
        (
            lambda definition: definition["volumes"][0].update(
                efsVolumeConfiguration={"fileSystemId": "fs-example"}
            ),
            "writable volume at /tmp/messqry",
        ),
    ],
)
def test_ecs_rejects_task_definition_without_runtime_isolation(change, message):
    client = FakeEcsClient([running_task()])
    change(client.task_definition)
    runtime = EcsFargateRuntime(ecs_settings(), client=client, health_probe=lambda *_: True)

    with pytest.raises(RuntimeLaunchError, match=message):
        runtime.start(
            deployment_id="deployment-1",
            deployment_revision=3,
            environment="staging",
            model_id="model-1",
            model_version_id="version-2",
            artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
            artifact_sha256="a" * 64,
        )

    assert client.run_calls == 0


@pytest.mark.parametrize("task_definition_arn", [None, "messqry-runtime"])
def test_ecs_rejects_task_definition_without_pinnable_revision(task_definition_arn):
    client = FakeEcsClient([running_task()])
    client.task_definition["taskDefinitionArn"] = task_definition_arn
    runtime = EcsFargateRuntime(ecs_settings(), client=client, health_probe=lambda *_: True)

    with pytest.raises(RuntimeLaunchError, match="revision could not be pinned"):
        runtime.start(
            deployment_id="deployment-1",
            deployment_revision=3,
            environment="staging",
            model_id="model-1",
            model_version_id="version-2",
            artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
            artifact_sha256="a" * 64,
        )

    assert client.run_calls == 0


def test_ecs_stopped_task_fails_start_and_attempts_cleanup():
    client = FakeEcsClient([{"lastStatus": "STOPPED"}])
    runtime = EcsFargateRuntime(ecs_settings(), client=client, health_probe=lambda *_: True)

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


def test_ecs_retries_health_until_expected_model_is_ready():
    now = 0.0
    results = iter([False, True])

    def sleep(duration):
        nonlocal now
        now += duration

    client = FakeEcsClient([running_task(), running_task()])
    runtime = EcsFargateRuntime(
        ecs_settings(),
        client=client,
        monotonic=lambda: now,
        sleep=sleep,
        health_probe=lambda *_: next(results),
    )
    handle = runtime.start(
        deployment_id="deployment-1",
        deployment_revision=3,
        environment="staging",
        model_id="model-1",
        model_version_id="version-2",
        artifact_uri="s3://messqry-private-artifacts/models/model-1/version-2.zip",
        artifact_sha256="a" * 64,
    )
    assert handle.private_ip == "10.0.1.17"
    assert client.stopped == []


def test_ecs_unhealthy_task_times_out_and_is_stopped():
    now = 0.0

    def sleep(duration):
        nonlocal now
        now += duration

    client = FakeEcsClient([running_task()] * 3)
    runtime = EcsFargateRuntime(
        ecs_settings(runtime_startup_timeout_seconds=2),
        client=client,
        monotonic=lambda: now,
        sleep=sleep,
        poll_interval_seconds=1,
        health_probe=lambda *_: False,
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
    assert len(client.stopped) == 1


def test_health_probe_rejects_redirect_and_bounds_response(monkeypatch):
    calls = []

    def fake_get(ip, port, path, timeout):
        calls.append((ip, port, path, timeout))
        return 302, b""

    monkeypatch.setattr("messqry.runtime.ecs.http_get_private", fake_get)
    assert not EcsFargateRuntime._probe_model_health("10.0.1.17", 8000, "version-2", 1.25)
    assert calls == [("10.0.1.17", 8000, "/health/ready", 1.25)]

    monkeypatch.setattr(
        "messqry.runtime.ecs.http_get_private",
        lambda *_args: (200, b" " * 4097),
    )
    assert not EcsFargateRuntime._probe_model_health("10.0.1.17", 8000, "version-2", 1)


class FakeTimer:
    def __init__(self, interval, function, *, args):
        self.interval = interval
        self.function = function
        self.args = args
        self.daemon = False
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        self.function(*self.args)


def test_runtime_reuses_task_and_stops_after_idle_grace_period():
    client = FakeEcsClient([running_task()])
    timers = []

    def timer_factory(interval, function, *, args):
        timer = FakeTimer(interval, function, args=args)
        timers.append(timer)
        return timer

    runtime = EcsFargateRuntime(
        ecs_settings(),
        client=client,
        health_probe=lambda *_: True,
        timer_factory=timer_factory,
        prediction_transport=lambda *_args, **_kwargs: "ok",
    )
    params = {
        "deployment_id": "deployment-1",
        "deployment_revision": 3,
        "environment": "production",
        "model_id": "model-1",
        "model_version_id": "version-2",
        "model_version": 2,
        "artifact_uri": "s3://messqry-private-artifacts/models/model-1/version-2.zip",
        "artifact_sha256": "a" * 64,
        "payload": {"inputs": [{"feature": 1}]},
    }
    assert runtime.predict(**params) == "ok"
    first_timer = timers[-1]
    assert first_timer.interval == 300
    assert first_timer.started
    assert runtime.predict(**params) == "ok"
    assert first_timer.cancelled
    assert client.run_calls == 1
    first_timer.fire()  # A cancelled callback already waiting for the lock must be stale.
    assert client.stopped == []
    timers[-1].fire()
    assert len(client.stopped) == 1


def test_runtime_starts_new_task_for_new_deployment_revision():
    client = FakeEcsClient([running_task("10.0.1.17"), running_task("10.0.1.18")])
    runtime = EcsFargateRuntime(
        ecs_settings(),
        client=client,
        health_probe=lambda *_: True,
        timer_factory=lambda *_args, **_kwargs: FakeTimer(1, lambda: None, args=()),
        prediction_transport=lambda ip, *_args, **_kwargs: ip,
    )
    common = {
        "deployment_id": "deployment-1",
        "environment": "production",
        "model_id": "model-1",
        "model_version_id": "version-2",
        "model_version": 2,
        "artifact_uri": "s3://messqry-private-artifacts/models/model-1/version-2.zip",
        "artifact_sha256": "a" * 64,
        "payload": {"inputs": [{"feature": 1}]},
    }
    assert runtime.predict(deployment_revision=3, **common) == "10.0.1.17"
    assert runtime.predict(deployment_revision=4, **common) == "10.0.1.18"
    assert client.run_calls == 2


def test_prediction_transport_failure_stops_task_and_next_request_restarts():
    client = FakeEcsClient([])
    runtime = EcsFargateRuntime(
        ecs_settings(),
        client=client,
        timer_factory=lambda *_args, **_kwargs: FakeTimer(1, lambda: None, args=()),
    )
    starts = []
    outcomes = iter([RuntimeTransportError("injected response failure"), "recovered"])

    def controlled_start(**_kwargs):
        task = RuntimeTask(task_arn=f"task-{len(starts) + 1}", private_ip="10.0.1.17")
        starts.append(task)
        return task

    def controlled_transport(*_args, **_kwargs):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    runtime.start = controlled_start
    runtime.prediction_transport = controlled_transport
    params = {
        "deployment_id": "deployment-1",
        "deployment_revision": 3,
        "environment": "production",
        "model_id": "model-1",
        "model_version_id": "version-2",
        "model_version": 2,
        "artifact_uri": "s3://messqry-private-artifacts/models/model-1/version-2.zip",
        "artifact_sha256": "a" * 64,
        "payload": {"inputs": [{"feature": 1}]},
    }

    with pytest.raises(RuntimeTransportError, match="injected response failure"):
        runtime.predict(**params)

    assert len(starts) == 1
    assert client.stopped == [
        {
            "cluster": "messqry-cluster",
            "task": "task-1",
            "reason": "MessQry runtime idle timeout",
        }
    ]
    assert runtime.predict(**params) == "recovered"
    assert [task.task_arn for task in starts] == ["task-1", "task-2"]


def test_cold_start_for_one_deployment_does_not_block_another_warm_task():
    client = FakeEcsClient([running_task("10.0.1.17"), running_task("10.0.1.18")])
    runtime = EcsFargateRuntime(
        ecs_settings(),
        client=client,
        health_probe=lambda *_: True,
        timer_factory=lambda *_args, **_kwargs: FakeTimer(1, lambda: None, args=()),
        prediction_transport=lambda ip, *_args, **_kwargs: ip,
    )
    common = {
        "deployment_revision": 1,
        "environment": "production",
        "model_id": "model-1",
        "model_version_id": "version-1",
        "model_version": 1,
        "artifact_uri": "s3://messqry-private-artifacts/models/model-1/version-1.zip",
        "artifact_sha256": "a" * 64,
        "payload": {"inputs": [{"feature": 1}]},
    }
    runtime.predict(deployment_id="warm-deployment", **common)
    startup_entered = threading.Event()
    release_startup = threading.Event()
    original_start = runtime.start

    def delayed_start(**kwargs):
        if kwargs["deployment_id"] == "cold-deployment":
            startup_entered.set()
            assert release_startup.wait(2)
        return original_start(**kwargs)

    runtime.start = delayed_start
    cold_result = []
    cold_thread = threading.Thread(
        target=lambda: cold_result.append(
            runtime.predict(deployment_id="cold-deployment", **common)
        )
    )
    cold_thread.start()
    assert startup_entered.wait(1)
    assert runtime.predict(deployment_id="warm-deployment", **common) == "10.0.1.17"
    release_startup.set()
    cold_thread.join(timeout=2)
    assert not cold_thread.is_alive()
    assert cold_result == ["10.0.1.18"]


def test_oversized_forwarded_payload_is_rejected_before_ecs_launch():
    client = FakeEcsClient([])
    runtime = EcsFargateRuntime(ecs_settings(), client=client)
    with pytest.raises(ValueError, match="1 MiB runtime limit"):
        runtime.predict(
            deployment_id="deployment-1",
            deployment_revision=1,
            environment="production",
            model_id="model-1",
            model_version_id="version-1",
            model_version=1,
            artifact_uri="s3://messqry-private-artifacts/models/model-1/version-1.zip",
            artifact_sha256="a" * 64,
            payload={"inputs": [{"text": "漢" * 400_000}]},
        )
    assert client.run_calls == 0


def test_startup_gate_is_shared_by_waiters_after_a_startup_failure():
    runtime = EcsFargateRuntime(
        ecs_settings(),
        client=FakeEcsClient([]),
        timer_factory=lambda *_args, **_kwargs: FakeTimer(1, lambda: None, args=()),
        prediction_transport=lambda ip, *_args, **_kwargs: ip,
    )
    first_start_entered = threading.Event()
    fail_first_start = threading.Event()
    second_start_entered = threading.Event()
    release_second_start = threading.Event()
    starts = 0
    starts_lock = threading.Lock()

    def controlled_start(**_kwargs):
        nonlocal starts
        with starts_lock:
            starts += 1
            start_number = starts
        if start_number == 1:
            first_start_entered.set()
            assert fail_first_start.wait(2)
            raise RuntimeLaunchError("injected startup failure")
        if start_number == 2:
            second_start_entered.set()
            assert release_second_start.wait(2)
        return RuntimeTask(task_arn=f"task-{start_number}", private_ip="10.0.1.18")

    runtime.start = controlled_start
    common = {
        "deployment_id": "same-deployment",
        "deployment_revision": 1,
        "environment": "production",
        "model_id": "model-1",
        "model_version_id": "version-1",
        "model_version": 1,
        "artifact_uri": "s3://messqry-private-artifacts/models/model-1/version-1.zip",
        "artifact_sha256": "a" * 64,
        "payload": {"inputs": [{"feature": 1}]},
    }
    outcomes = []

    def call_predict():
        try:
            outcomes.append(runtime.predict(**common))
        except RuntimeLaunchError as exc:
            outcomes.append(str(exc))

    first = threading.Thread(target=call_predict)
    first.start()
    assert first_start_entered.wait(1)

    waiter = threading.Thread(target=call_predict)
    waiter.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with runtime._warm_tasks_lock:
            gate = runtime._startup_locks.get(("same-deployment", 1))
            if gate is not None and gate.users == 2:
                break
        time.sleep(0.005)
    else:
        pytest.fail("second caller did not join the existing startup gate")

    fail_first_start.set()
    first.join(timeout=1)
    assert not first.is_alive()
    assert second_start_entered.wait(1)

    newcomer = threading.Thread(target=call_predict)
    newcomer.start()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        with runtime._warm_tasks_lock:
            gate = runtime._startup_locks.get(("same-deployment", 1))
            if gate is not None and gate.users == 2:
                break
        time.sleep(0.005)
    else:
        pytest.fail("new caller did not join the retained startup gate")

    release_second_start.set()
    waiter.join(timeout=1)
    newcomer.join(timeout=1)
    assert not waiter.is_alive()
    assert not newcomer.is_alive()
    assert starts == 2
    assert outcomes.count("injected startup failure") == 1
    assert outcomes.count("10.0.1.18") == 2
