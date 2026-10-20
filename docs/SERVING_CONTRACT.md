# Model serving contract (Phase 3)

## Runtime boundary

Model packages use MLflow `pyfunc`. Each model/environment deployment will run in its own ECS Fargate task. The API control plane selects a registered deployment and sends bounded input data to its private runtime endpoint. Model loading, deserialization, and inference must happen in the Fargate task, never in the API process. Tasks receive only the selected validated artifact and narrowly scoped access to required resources.

The owner selected one ECS Fargate task per model/environment deployment, initially sized at 1 vCPU / 2 GiB with the default 20 GiB ephemeral storage, and stopping tasks five minutes after their last prediction to conserve the initial AWS credit while supporting short bursts. AWS includes the default storage allocation without an additional storage charge; compute and other service charges still apply. The control plane will discover a launched task by its private IP after ECS reports it running. The prototype may tolerate a cold start of up to 60 seconds, followed by a 30-second inference timeout; these are targets to validate, not availability guarantees. This profile is not a claim of production suitability; workload measurements must validate it. Arbitrary internet egress is blocked; tasks may access only required private AWS endpoints, such as S3 for artifacts and the configured log path.

The optional `ecs_fargate` launcher starts tasks with public IP assignment disabled, 1,024 CPU units / 2,048 MiB memory, and no ephemeral-storage override. The cluster, task definition, container, private subnet IDs, and security-group IDs are supplied through `MESSQRY_ECS_*` settings. The task definition must use an execution role for image/log operations and a least-privilege task role for private artifact and required AWS endpoint access. Per-model IAM role selection is not implemented. VPC route tables, endpoint policies, security-group egress, task-definition roles, container health checks, and the inference listener must be configured and verified separately; the launcher cannot prove those network policies. It waits for ECS `RUNNING` plus a private IP, which is not yet a model health check.

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

The planned route is `POST /api/v1/models/{model_id}/deployments/{environment}/predict`. It will resolve the active deployment version on every request or use a bounded cache invalidated by deployment revision. It must require a dedicated prediction scope; registry scopes do not grant inference access. The owner selected a 30-second inference execution timeout after the runtime is ready; the separate prototype cold-start allowance is up to 60 seconds. Expected errors use the shared API error envelope: invalid input (422), unauthenticated/unauthorized (401/403), no active deployment (404/409), runtime unavailable or timed out (503/504), and malformed runtime output (502).

This document records the contract and bounds as implementation defaults, not load-tested production limits. The configurable ECS task launcher is implemented, but the prediction endpoint, runtime image, health probe, idle-task registry/timer, task authorization boundaries, and inference timeout enforcement remain incomplete. The serving path must enforce these controls before the prediction endpoint is exposed.
