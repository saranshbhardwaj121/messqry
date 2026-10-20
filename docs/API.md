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
scopes, and prediction requires `messqry/prediction.invoke`. The prediction
route is `POST /api/v1/models/{model_id}/deployments/{environment}/predict`.
It accepts 1–1,000 homogeneous JSON records within a 1 MiB request body and
returns one prediction per record, tagged with the active model version. Only
validated MLflow `pyfunc` versions can be served. Runtime calls use the selected
private ECS task and a 30-second timeout; the control plane never loads model
code. See [SERVING_CONTRACT.md](SERVING_CONTRACT.md) for the full payload and
runtime response contract. Browser sign-in and evaluation authorization remain
unfinished, so the API is not ready for production exposure.

For an existing model with no deployment selected in an environment, deployment
history returns an empty array. A nonexistent model returns 404.

Deployment `PUT` and rollback `POST` requests may include an `Idempotency-Key`
header containing 1–128 ASCII letters, digits, `.`, `_`, `~`, `:`, or `-`. The
key is scoped to a model and environment. A retry with the same key and the same
action/version/reason returns the original response snapshot, even if the
deployment has changed since then. Reusing the key for a different request
returns 409. Requests without a key keep the ordinary desired-state behavior.
If concurrent requests race to store the same new key, one may receive 409 and
can retry with that key to retrieve the recorded response.
