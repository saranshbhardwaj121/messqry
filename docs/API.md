# API contract policy

## Versioning and compatibility

The first API surface is rooted at `/api/v1`. Within a major version, existing
request fields, response fields, status codes, and error meanings are stable.
Adding an optional request field or response field is compatible. Removing or
renaming a field, changing its meaning or type, or changing an established
response shape requires a new major route prefix such as `/api/v2`.

The v1 collection endpoints currently return JSON arrays. Do not change them to
pagination envelopes in place. Before registry sizes require pagination, add a
versioned paginated endpoint. Its collection response should use
`{ "items": [], "next_cursor": null }`, accept an opaque `cursor` and a bounded
`limit` (default 50, maximum 100), and use stable ordering with a unique tie
breaker. Clients must treat cursors as opaque and stop when `next_cursor` is
`null`.

## Errors

Expected API errors use the shared envelope:

```json
{
  "error": {
    "code": "not_found",
    "message": "Model not found"
  }
}
```

Validation errors use `error.code = "validation_error"` and may include a
`fields` array containing safe field locations and validation codes. They must
not echo submitted values. Unexpected server errors return a generic message.
HTTP status codes and protocol headers remain meaningful; clients should use
the status code and machine-readable error code rather than parsing message
text. The server preserves headers such as `Allow`, `WWW-Authenticate`, and
`Retry-After`.

## Current routes

The route inventory and initial local run instructions are maintained in the
repository [README](../README.md#current-api). The OpenAPI document at
`/openapi.json` is the machine-readable contract when API docs are enabled.

Current v1 list responses are unpaginated. Registry routes require registry
scopes, deployment list/history/change routes require deployment read/write
scopes, evaluation list/create routes require evaluation read/write scopes,
and prediction requires `messqry/prediction.invoke`. The prediction
route is `POST /api/v1/models/{model_id}/deployments/{environment}/predict`.
It accepts 1–1,000 homogeneous JSON records within a 1 MiB request body and
returns one prediction per record, tagged with the active model version. Only
validated MLflow `pyfunc` versions can be served. Runtime calls use the selected
private ECS task and a 30-second timeout; the control plane never loads model
code. See [SERVING_CONTRACT.md](SERVING_CONTRACT.md) for the full payload and
runtime response contract. The frontend includes Cognito authorization-code
sign-in with PKCE and attaches the access token to API calls when configured;
live Cognito setup is unverified. Evaluation endpoints use separate read/write
scopes, with their Cognito resource-server configuration still pending.

Production deployment and rollback requests are gated by every latest
production policy version for the model. Each policy must have a passing
evaluation for the exact target model version, with matching policy identity
and threshold snapshot. Missing, failing, stale, incompatible, or timestamp-tied
latest evaluations return `409 Conflict`. An operator may set `override_evaluation: true` in the
deployment request only when the token also has `messqry/evaluation.override`;
the request must include a non-empty `reason`. The immutable history records
the action as `override` and stores the trimmed reason. Keep this scope limited
to release administrators. Cognito scope creation and grants remain Phase 6
work.

The existing `/api/v1/models/{model_id}/versions/{version_number}/evaluations`
contract remains stable: POST accepts caller-supplied `metrics` and
`thresholds`, and v1 responses retain their original fields. The
provenance- and policy-backed POST contract is available at
`POST /api/v2/models/{model_id}/versions/{version_number}/evaluations`.
`POST /api/v1/models/{model_id}/evaluation-policies` creates an immutable policy
version with a name, target environment, explicit metric operators, and
threshold values. `GET` on that path lists versions, optionally filtered by
`environment`. Reusing the same model/environment/name/version returns 409.
Policy `version` must be between 1 and 2,147,483,647 inclusive.
The v2 evaluation request requires `dataset_name`, `dataset_version`, a 64-character
SHA-256 `dataset_sha256`, `evaluator_name`, `evaluator_version`, the `policy_id`,
and caller-supplied `metrics`. The API applies the stored policy thresholds and
copies them into the evaluation record with the policy identity/version. The
checksum is caller-declared and identifies the dataset bytes the evaluator
claims to have used; the API cannot verify it because dataset ingestion is not
implemented. It also does not execute the dataset or independently reproduce
metric computation. Historical records created before provenance and policy
linkage were introduced return null for those fields.

For an existing model with no deployment selected in an environment, deployment
history returns an empty array. A nonexistent model returns 404.

Deployment `PUT` and rollback `POST` requests may include an `Idempotency-Key`
header containing 1–128 ASCII letters, digits, `.`, `_`, `~`, `:`, or `-`. The
key is scoped to a model and environment. A retry with the same key and the same
action/version/reason returns the original response snapshot, even if the
deployment has changed since then. Reusing the key for a different request
returns 409. Requests without a key keep the ordinary desired-state behavior.
If concurrent requests race to store the same key and request, the losing
request rechecks the idempotency record after its transaction conflict and
returns the winner's response snapshot. A concurrent different request using
the same key receives 409.
