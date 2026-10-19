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

Current v1 list responses are unpaginated. The current API is a development
foundation and is not yet an authenticated production contract.
