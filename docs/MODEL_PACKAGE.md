# Supported model package and artifact contract

## Approved first-release choices

- **Model interface:** MLflow `pyfunc`.
- **Artifact storage:** private Amazon S3, accessed by the service through its AWS role. User-supplied artifact URIs will not select arbitrary storage locations.
- **Serving boundary:** the control API validates package structure and metadata. It does not import model code, unpickle model objects, or run inference. Model execution belongs in the isolated runtime defined in Phase 3.

MLflow's `pyfunc` format is a model directory whose root `MLmodel` file describes the `python_function` flavor. MLflow also documents that model loading can deserialize Python objects and may execute user code; package inspection must therefore remain separate from loading and inference. See the [MLflow pyfunc documentation](https://mlflow.org/docs/latest/api_reference/python_api/mlflow.pyfunc.html) and its [model format overview](https://mlflow.org/docs/latest/ml/model/index.html).

## Package shape

The first-release upload is a ZIP transport for one MLflow model directory. After extracting into a private temporary directory, the package must have:

- One `MLmodel` regular file at the package root.
- A YAML mapping with a `flavors.python_function` mapping.
- Any model data, code, and environment files referenced by that metadata, using relative paths contained within the package root.
- No absolute paths, parent-directory traversal, symbolic links, duplicate normalized paths, or special filesystem entries.

Other MLflow flavors may be present. At least the `python_function` flavor is required. The control API will validate the YAML and paths without loading the model or importing referenced modules.

The non-executable validator currently enforces a 512 MiB compressed upload limit, 2 GiB expanded-size limit, 10,000-entry limit, and 200:1 per-entry compression-ratio limit. It accepts stored and deflated ZIP entries, rejects encrypted/special entries, duplicate case-insensitive paths, and file-as-parent path collisions, and streams entries to verify CRCs. It validates `python_function.artifacts.*.path` and other declared paths against the package contents. These are initial service defaults, not yet owner-approved operating limits. Dependency allowlisting and policy for nested archives remain open; the validator does not extract or execute nested content.

## Runtime dependency boundary

The prototype runtime image pins MLflow to 3.17.0 and uses the task's IAM role to retrieve the exact server-selected S3 object. It does not install model requirements, restore a Conda environment, or contact a package index at task startup. Before model loading and readiness, the runtime checks `python_function.python_version`, Python versions in referenced environment files, root `requirements.txt`, and requirements referenced by `python_env.yaml` against the baked image. Supported Conda `python`, `pip`, `setuptools`, and `wheel` entries are handled; versioned build-tool constraints are checked against installed package versions. Patch-level Python differences are accepted; package specifiers must match the image. Direct-URL, local-path, and other Conda-only dependencies are rejected because this image does not restore arbitrary environments. Missing or incompatible declared dependencies fail startup and the ECS launcher treats the task as unhealthy. This preserves the selected private-endpoints-only egress policy and avoids executing package installation hooks in a running task. The first-release dependency allowlist and process for building images with additional approved libraries still need owner approval. MLflow supports loading a local pyfunc model with `mlflow.pyfunc.load_model`; model dependencies are recorded in model environment files and need a compatible execution environment ([MLflow model dependency documentation](https://mlflow.org/docs/latest/ml/model/dependencies/)).

## Version metadata and immutability

Each registered version is an append-only record containing:

- The parent model ID and monotonically assigned version number.
- Package format (`mlflow_pyfunc`).
- Server-generated S3 object key and, when bucket versioning is enabled, the S3 object version ID.
- SHA-256 of the original uploaded archive and its byte size.
- A server-generated validation manifest containing parsed, non-executable package metadata.
- Registration and validation timestamps/status.

Artifact key, object version, digest, size, package format, parsed manifest, and version number are immutable after registration. Corrections require registering a new version. Model display metadata can be edited separately. Existing records that contain only caller-provided `artifact_uri` values must be migrated or labeled as legacy and unverified; they must not be represented as validated MLflow packages without inspection.

`POST /api/v1/models/{model_id}/versions/artifact` accepts a multipart ZIP upload, validates it, assigns the next immutable version and a server-generated object key, and stores the package through the private storage interface. An ASGI request guard enforces a 513 MiB total multipart request cap (512 MiB package limit plus at most 1 MiB multipart overhead) before multipart parsing, using `Content-Length` when available and counting streamed bytes otherwise. If the database commit fails after storage succeeds, the API attempts to remove the unregistered object. `GET /api/v1/models/{model_id}/versions/{version_number}/artifact` streams only validated packages and never exposes storage credentials or a presigned URL. The legacy JSON version-registration endpoint still accepts a URI, but marks that record `legacy_unverified`; those records cannot be downloaded through the artifact endpoint.

## Storage and access rules

- The S3 bucket is private and encrypted at rest; public access is blocked.
- The API uses workload IAM credentials and generates object keys from server-controlled identifiers, never from user-provided paths.
- Registry read/write endpoints require `messqry/registry.read` or `messqry/registry.write` scopes. Staging/production validate Cognito access tokens against the configured user-pool issuer, JWKS, access-token type, and allowed web/service client IDs.
- `MESSQRY_AUTH_DEV_BYPASS` grants both registry scopes only in development for the local console; configuration rejects it in staging/production.
- Download access is authorized by the API. Short-lived presigned URLs may be used only after the caller is authorized and the response contract is established.
- Secrets and signed URLs must not be stored in model metadata, logs, or the validation manifest.
- Cognito user pool, app clients, custom resource-server scopes, callback/domain configuration, S3 provisioning, bucket policy, lifecycle/retention, and KMS choices remain infrastructure work; no cloud resources are provisioned by this contract. Deployment and evaluation endpoints require separate scopes, but their Cognito resources still need provisioning and live verification.
