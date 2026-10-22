# MessQry AI

MessQry AI is an MLOps platform concept for helping teams register, deploy, monitor, evaluate, and safely update machine-learning models.

This repository contains a configurable FastAPI application, liveness/readiness endpoints, a persistent registry with validated artifact upload/download, Cognito token verification for registry, deployment, evaluation, and prediction routes, a deployment selection/history API, metric evaluation records, local/private S3 storage adapters, a non-executing MLflow `pyfunc` ZIP validator, a mock-tested private runtime dispatcher, and a single-model runtime server with a Docker build definition. The frontend now includes a Cognito authorization-code/PKCE sign-in flow and scoped API token handling, but live Cognito configuration is not verified. The runtime image has not been built or exercised in AWS; reproducible evaluations, request monitoring, and AWS deployment remain unfinished.

## Run locally

Requires Python 3.11 or newer.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
alembic upgrade head
uvicorn messqry.main:app --reload --app-dir src
```

For PostgreSQL, install its optional driver and set `MESSQRY_DATABASE_URL` to a SQLAlchemy psycopg URL, for example `postgresql+psycopg://messqry:password@localhost:5432/messqry`:

```powershell
python -m pip install -e ".[postgres]"
$env:MESSQRY_DATABASE_URL = "postgresql+psycopg://messqry:password@localhost:5432/messqry"
alembic upgrade head
```

The API is available at `http://127.0.0.1:8000`. When API docs are enabled, Swagger UI is at `/docs` and the OpenAPI schema is at `/openapi.json`.

## Frontend preview

The React/TypeScript console is in `frontend/`. Start the API in one terminal using the steps above, then start the console in another:

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev
```

Open `http://127.0.0.1:5173`. The development server proxies API and health requests to `http://127.0.0.1:8000`. The Overview displays model, deployment, and evaluation records returned by the API. Monitoring metrics remain unavailable until an inference telemetry source is implemented; no demo metrics are presented as live data. Set `VITE_DEPLOYMENT_ENV` to `local`, `development`, `staging`, or `production` in the frontend build environment so the badge names the API deployment, independent of Vite's build mode. For production sign-in, also configure the public Cognito domain/client ID/callback/logout URLs and granted API scopes shown in `frontend/.env.example`; use a public client with authorization-code PKCE and never include a client secret. The frontend keeps access tokens in memory and does not store service credentials. These values are public configuration and must not contain secrets.

## Configuration

The database does not come bundled with the AWS compute that runs the app. The
current production direction is PostgreSQL, and Amazon RDS for PostgreSQL is a
managed hosting option we recommend evaluating; it has not yet been selected
or provisioned. If selected, the API would connect to it over private AWS
networking using `MESSQRY_DATABASE_URL`. Read the beginner-friendly
[AWS glossary and architecture guide](docs/AWS_GLOSSARY.md) for how the
database, API, S3 artifacts, and isolated Fargate model tasks fit together.

Copy `.env.example` to `.env` to customize local settings. Variables use the `MESSQRY_` prefix:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MESSQRY_APP_NAME` | `MessQry AI` | API title shown in OpenAPI metadata |
| `MESSQRY_ENVIRONMENT` | `development` | One of `development`, `test`, `staging`, or `production` |
| `MESSQRY_DOCS_ENABLED` | `true` | Enables or disables interactive API documentation |
| `MESSQRY_DATABASE_URL` | `sqlite:///./messqry.db` | Secret SQLAlchemy database URL; staging and production require PostgreSQL and the `postgres` extra |
| `MESSQRY_ARTIFACT_STORAGE` | `local` | `local` for development/test or `s3` for staging/production |
| `MESSQRY_ARTIFACT_LOCAL_ROOT` | `./artifacts` | Local development artifact root; ignored by Git |
| `MESSQRY_ARTIFACT_S3_BUCKET` | unset | Required when S3 storage is selected |
| `MESSQRY_ARTIFACT_S3_PREFIX` | `messqry` | Server-controlled prefix for private S3 object keys |
| `MESSQRY_AUTH_DEV_BYPASS` | `true` | Local development only; staging/production must set `false` |
| `MESSQRY_OIDC_ISSUER` | unset | Cognito User Pool issuer URL; required in staging/production |
| `MESSQRY_OIDC_AUDIENCE` | unset | Cognito web app client ID; required in staging/production |
| `MESSQRY_OIDC_SERVICE_CLIENT_IDS` | unset | Comma-separated Cognito machine-client IDs; required in staging/production |
| `MESSQRY_RUNTIME_BACKEND` | `disabled` | `disabled` or optional `ecs_fargate` launcher |
| `MESSQRY_AWS_REGION` | unset | AWS region when the Fargate launcher is enabled |
| `MESSQRY_ECS_CLUSTER` | unset | ECS cluster name or ARN when Fargate launching is enabled |
| `MESSQRY_ECS_TASK_DEFINITION` | unset | Fargate task definition name/ARN/revision |
| `MESSQRY_ECS_CONTAINER_NAME` | unset | Container in the task definition that runs the model server |
| `MESSQRY_ECS_SUBNET_IDS` | unset | Comma-separated private subnets for model tasks |
| `MESSQRY_ECS_SECURITY_GROUP_IDS` | unset | Comma-separated task security groups |
| `MESSQRY_RUNTIME_PORT` | `8000` | Private model server health and prediction port |
| `MESSQRY_RUNTIME_STARTUP_TIMEOUT_SECONDS` | `60` | Maximum time for ECS start, private IP discovery, and model readiness (1–60) |
| `MESSQRY_RUNTIME_IDLE_TIMEOUT_SECONDS` | `300` | Warm-task grace period before idle task stop (1–3600) |

Production and staging startup reject SQLite and require `MESSQRY_ARTIFACT_STORAGE=s3` with a bucket name. Install the `aws` extra for the S3 SDK, and the `postgres` extra for the PostgreSQL driver (for example, `python -m pip install -e ".[aws,postgres]"`). S3 authentication uses the AWS SDK credential chain; production should use a scoped workload IAM role, not static credentials. Local/test environments default to filesystem storage under `./artifacts`, which is ignored by Git. Database URLs are kept as secret settings and masked in settings representations and validation errors. Supply production secrets through the runtime secret manager, not a committed `.env` file.

The optional Fargate launcher requires private S3 storage and the `aws` extra. It starts one task with public IP assignment disabled, 1 vCPU / 2 GiB, and Fargate's default 20 GiB ephemeral storage. Before each launch, it describes the selected task definition and fails closed unless it uses Fargate/`awsvpc`, the configured runtime container is non-privileged and read-only at its root, explicitly specifies a non-root numeric user, and mounts a writable task volume at `/tmp/messqry`. It launches the exact immutable task-definition revision returned by that validation. The runtime image declares `/tmp/messqry` as a Docker `VOLUME` and sets its ownership to UID/GID 10001, so ECS can copy that directory ownership into its empty bind volume. The control-plane role therefore needs `ecs:DescribeTaskDefinition` in addition to the permissions used for run, describe-task, and stop-task operations. The task role must have least-privilege access to the selected private S3 artifact and required private AWS endpoints. The prediction route requires `messqry/prediction.invoke`, starts/reuses a task for the selected deployment revision, checks `/health/ready`, forwards requests to `/predict`, and stops idle tasks after the configured grace period. The prototype runtime server downloads the selected S3 object, verifies its SHA-256, safely extracts the archive, and loads it using `mlflow.pyfunc`. It does not install per-model requirements at task startup; model dependencies must already be present in the runtime image. Build the image from the repository root with `docker build -f runtime/Dockerfile -t messqry-model-runtime:local .`. The image has been exercised locally under Docker CPU, memory, PID, no-network, read-only-root, and bounded temporary-filesystem limits, including a fresh empty volume mounted at `/tmp/messqry` as the runtime user. Configure subnet routes, endpoint policies, security-group egress, IAM task roles, image/log access, and a compliant task definition separately. Live ECS launch and AWS policies have not been tested, so keep `MESSQRY_RUNTIME_BACKEND=disabled` until those prerequisites are ready.

Cognito user-pool access tokens are verified against the configured issuer's JWKS. The API accepts the configured web app client and service app clients, requires `token_use=access`, and recognizes `messqry/registry.read`, `messqry/registry.write`, `messqry/deployment.read`, `messqry/deployment.write`, `messqry/prediction.invoke`, `messqry/evaluation.read`, `messqry/evaluation.write`, and `messqry/evaluation.override`. Configure these Cognito resource-server scopes and grant only the needed scopes to each app client. Limit the override scope to release administrators. The development bypass grants these scopes locally and is rejected in staging/production. Deployment, prediction, and evaluation scopes currently apply across the single-organization workspace; per-model/environment roles are not implemented. The user-pool ID, region, app clients, and resource server still need to be provisioned in Phase 6; no AWS resources were created.

API errors use an `{ "error": { "code": "...", "message": "..." } }` envelope. Request validation may include field locations and error types, but never echoes submitted values. Unexpected server failures return a generic message and log only the exception type. The readiness endpoint keeps its documented status/database response shape.

API compatibility, pagination, and error conventions are documented in
[docs/API.md](docs/API.md).

## Current API

- `GET /health` — reports that the API process is running. It does not yet check a database, model server, or other dependency.
- `GET /ready` — checks that the configured database can answer a simple query; returns `503` when it cannot.
- `POST /api/v1/models` — create a logical model record.
- `GET /api/v1/models` — list registered models.
- `GET /api/v1/models/{model_id}` — retrieve a model and its version records.
- `POST /api/v1/models/{model_id}/versions` — register metadata for the next model version.
- `GET /api/v1/models/{model_id}/versions` — list model versions.
- `POST /api/v1/models/{model_id}/versions/artifact` — multipart upload of an MLflow `pyfunc` ZIP; creates an immutable version in private storage.
- `GET /api/v1/models/{model_id}/versions/{version_number}/artifact` — stream a validated package from private storage.
- `POST /api/v1/models/{model_id}/evaluation-policies` and `GET` on the same path — create immutable threshold-policy versions and list them.
- `POST /api/v1/models/{model_id}/versions/{version_number}/evaluations` — backward-compatible evaluation using caller-supplied numeric metrics and thresholds.
- `POST /api/v2/models/{model_id}/versions/{version_number}/evaluations` — record dataset/evaluator provenance and compare supplied numeric metrics against a selected stored policy. Metric computation itself is currently performed by the caller.
- `GET /api/v2/models/{model_id}/versions/{version_number}/evaluations` — list policy-backed evaluation results with provenance and threshold snapshots.
- `GET /api/v1/models/{model_id}/versions/{version_number}/evaluations` — list evaluation records for a version.
- `PUT /api/v1/models/{model_id}/deployments/{environment}` — select a registered version for an environment and record a deployment or promotion.
- `POST /api/v1/models/{model_id}/deployments/{environment}/rollback` — select a previously registered version and record a rollback. Repeating a request that selects the version already active returns the current deployment without adding a duplicate history event. Deployment and rollback also support request-key idempotency through `Idempotency-Key`; see [API.md](docs/API.md).
- `GET /api/v1/models/{model_id}/deployments` — list current version selections.
- `GET /api/v1/models/{model_id}/deployments/{environment}/history` — list deployment changes for an environment.

Registry routes require their corresponding bearer-token scope in staging/production. Deployment list/history routes require `messqry/deployment.read`; promotion and rollback require `messqry/deployment.write`. Every production version change must have a passing evaluation for each latest production policy version, evaluated against the target model version. Missing, failed, stale, or incompatible evidence returns 409. A principal with `messqry/evaluation.override` may bypass the gate only with a non-empty reason; immutable deployment history records the `override` action and reason. Evaluation list routes require `messqry/evaluation.read`; evaluation creation requires `messqry/evaluation.write`. Local development bypass is enabled only by the development setting.

## Local database and migration operations

Apply all pending schema migrations before starting the API:

```powershell
alembic upgrade head
```

Inspect the current revision with `alembic current` and available revisions
with `alembic history`. To roll back a local disposable database by one
revision, run `alembic downgrade -1`; to recreate the current schema, follow
with `alembic upgrade head`. A downgrade can discard columns or tables added
by newer migrations. Never experiment with downgrade commands against a
database containing data you need.

For production, take and verify a database backup before a migration. Run
forward migrations as a controlled release step, verify the resulting revision
with `alembic current`, and prefer a new forward repair migration over
downgrading a production database. Restore procedures and production rollback
must be rehearsed before release; they are not yet validated by this project.

`MESSQRY_DATABASE_URL` selects the target database for both Alembic and the
API. Local development defaults to `sqlite:///./messqry.db`. Staging and
production require `postgresql+psycopg://...` and the optional `postgres`
dependency extra. Do not put credentials in committed files or command logs;
use environment-specific secret delivery for shared environments. Tests use
temporary databases and must not target the ignored local `messqry.db`.

## Development checks

Install the backend development tools with:

```powershell
python -m pip install -e ".[dev]"
```

Run the local checks from the repository root:

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m pip_audit --skip-editable
npm --prefix frontend run lint
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit --audit-level=high
```

The matching backend and frontend jobs run in `.github/workflows/ci.yml` on
pushes and pull requests.

## Direction

Build the product in lifecycle order: model registration and version history; evaluation and promotion policies; runtime deployment and prediction; request and operational monitoring; then optional retraining automation. Evaluation compares caller-supplied metrics against immutable, versioned stored policies and records dataset/evaluator provenance; it does not run a model or benchmark dataset. Production version changes require current passing policy evaluations unless an authorized, reasoned override is recorded; reproducible metric computation remains unimplemented. The deployment API changes a database pointer and records history; the isolated runtime image is implemented but has not been built or deployed, so live prediction is not verified. New registry uploads use validated immutable artifact records and private storage adapters; legacy artifact-URI rows remain unverified and cannot be downloaded through the new route. Live S3/Cognito integration, PostgreSQL production operation, browser Cognito sign-in, and tenant isolation remain unverified or out of scope. Keep model execution isolated and make deployment changes auditable as serving capabilities are added.
