# Model serving contract (Phase 3)

## Runtime boundary

Model packages use MLflow `pyfunc`. Each model/environment deployment will run in its own ECS Fargate task. The API control plane selects a registered deployment and sends bounded input data to its private runtime endpoint. Model loading, deserialization, and inference must happen in the Fargate task, never in the API process. Tasks receive only the selected validated artifact and narrowly scoped access to required resources.

The owner selected one ECS Fargate task per model/environment deployment, initially sized at 1 vCPU / 2 GiB with the default 20 GiB ephemeral storage, and stopping tasks five minutes after their last prediction to conserve the initial AWS credit while supporting short bursts. AWS includes the default storage allocation without an additional storage charge; compute and other service charges still apply. The control plane will discover a launched task by its private IP after ECS reports it running. The prototype may tolerate a cold start of up to 60 seconds, followed by a 30-second inference timeout; these are targets to validate, not availability guarantees. This profile is not a claim of production suitability; workload measurements must validate it. Arbitrary internet egress is blocked; tasks may access only required private AWS endpoints, such as S3 for artifacts and the configured log path.

The optional `ecs_fargate` launcher starts tasks with public IP assignment disabled, 1,024 CPU units / 2,048 MiB memory, and no ephemeral-storage override. Before launching, it describes the configured task definition and rejects it unless it uses Fargate with `awsvpc`, the named runtime container is non-privileged, has a read-only root filesystem, explicitly sets a non-root numeric user, and mounts a writable ephemeral task volume at `/tmp/messqry`. It then launches the exact immutable task-definition revision ARN that was inspected, avoiding a mutable-family-alias check/use race. The image sets `/tmp/messqry` ownership to UID/GID 10001 and declares that path with Docker `VOLUME`; ECS copies the image directory into the empty bind mount when the container path matches, preserving those permissions. The container definition must set `user` to `10001:10001`. The control-plane task role needs `ecs:DescribeTaskDefinition` along with scoped `ecs:RunTask`, `ecs:DescribeTasks`, and `ecs:StopTask` permissions. The cluster, task definition, container, private subnet IDs, and security-group IDs are supplied through `MESSQRY_ECS_*` settings. The task definition must use an execution role for image/log operations and a least-privilege task role for private artifact and required AWS endpoint access. Per-model IAM role selection is not implemented. VPC route tables, endpoint policies, security-group egress, and task-definition roles must be configured and verified separately; the launcher cannot prove those network policies.

After ECS reports `RUNNING` and provides a private IP, the launcher probes `http://<private-ip>:<MESSQRY_RUNTIME_PORT>/health/ready` until the shared 60-second cold-start deadline. Readiness requests use a single direct HTTP request with redirects disabled and an absolute per-request deadline bounded by the remaining startup time. A ready response must be HTTP 200 JSON with `status: "ready"` and the exact selected `model_version_id`. The default port is 8000. An unhealthy, unreachable, malformed, oversized, redirected, or mismatched response keeps the task in startup; timeout attempts to stop the task. The runtime server now implements this wire contract, and both sides have unit/mock coverage. The Linux image has been built and locally exercised with restrictive Docker settings; actual ECS task launch and model-backed inference are not yet verified.

The API process caches ready task handles by deployment ID and revision. It cancels the idle timer while requests are in flight, reuses the task for the selected five-minute warm period, stops stale or failed tasks, and stops cached tasks during API shutdown. Concurrent cold starts for the same deployment share a reference-counted startup gate retained until all current callers finish, including when startup fails. The prediction route copies the selected deployment metadata and ends its database read transaction before starting or calling the runtime. Runtime HTTP uses one request with no redirect support, caps parsed headers and trailers at 64 KiB, and enforces one total 30-second deadline across connect, send, and response read. The serialized UTF-8 request is size-checked before any cold start. This cache is process-local; running multiple API replicas can create one runtime task per replica for the same deployment. A shared task lease/registry is required before horizontal API scaling.

## Prediction payload

The initial v1 payload shape is:

```json
{
  "inputs": [
    {"feature_a": 1.2, "feature_b": "north"},
    {"feature_a": 0.7, "feature_b": "south"}
  ]
}
```

`inputs` contains 1–1,000 JSON records in a request body capped at 1 MiB. The ASGI guard enforces the byte cap while streaming, before JSON parsing, including when `Content-Length` is absent. Each record must have the same 1–256 field names; field names must be 1–128 non-whitespace characters. Values may be JSON scalars, arrays, or objects. The runtime converts the records to the tabular input expected by `pyfunc` and applies any declared MLflow model signature. The first record's key order defines column order.

The response shape is:

```json
{
  "model_id": "<uuid>",
  "environment": "production",
  "model_version": 4,
  "predictions": [0.91, 0.12]
}
```

There is one JSON-compatible prediction value per input record. The selected model version is returned so callers can identify which deployed package handled the request. A runtime response with a different prediction count is an upstream failure, not a partial success.

The planned route is `POST /api/v1/models/{model_id}/deployments/{environment}/predict`. It will resolve the active deployment version on every request or use a bounded cache invalidated by deployment revision. It requires a dedicated `messqry/prediction.invoke` scope; registry scopes do not grant inference access. The API-to-runtime adapter posts to the private task's `/predict` endpoint with a 30-second timeout, caps its JSON response at 1 MiB, and validates model ID, environment, version, and prediction count before returning data. The owner selected a 30-second inference execution timeout after the runtime is ready; the separate prototype cold-start allowance is up to 60 seconds. Expected errors use the shared API error envelope: invalid input (422), unauthenticated/unauthorized (401/403), no active deployment (404/409), runtime unavailable or timed out (503/504), and malformed runtime output (502).

This document records the contract and bounds as implementation defaults, not load-tested production limits. The configurable ECS launcher, task-definition isolation preflight, health-probe client, bounded HTTP transport, authenticated prediction endpoint, process-local idle-task timer, model artifact downloader, checksum verifier, safe extractor, and MLflow runtime server are implemented with local tests and mocks. `runtime/Dockerfile` has been built and locally exercised under bounded Docker restrictions. The image does not install model-specific requirements at task startup; models must use dependencies already included in the image. A real S3 model load, shared task coordination across API replicas, and AWS network/task authorization remain unverified. Real inference must not be considered available until dependency compatibility and AWS environment are configured and verified.
