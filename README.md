# MessQry AI

MessQry AI is an MLOps platform concept for helping teams register, deploy, monitor, evaluate, and safely update machine-learning models.

This repository contains an initial backend foundation: a configurable FastAPI application, liveness and readiness endpoints, a persistent registry for model metadata and version records, control-plane deployment selection with an audit history, and metric evaluation records. Model artifact storage, inference serving, authentication, request monitoring, automated evaluation, and AWS deployment are not implemented yet.

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

Open `http://127.0.0.1:5173`. The development server proxies API and health requests to `http://127.0.0.1:8000`. The Overview displays model, deployment, and evaluation records returned by the API. Monitoring metrics remain unavailable until an inference telemetry source is implemented; no demo metrics are presented as live data. Set `VITE_DEPLOYMENT_ENV` to `local`, `development`, `staging`, or `production` in the frontend build environment so the badge names the API deployment, independent of Vite's build mode. See `frontend/.env.example`; these values are public configuration and must not contain secrets.

## Configuration

Copy `.env.example` to `.env` to customize local settings. Variables use the `MESSQRY_` prefix:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MESSQRY_APP_NAME` | `MessQry AI` | API title shown in OpenAPI metadata |
| `MESSQRY_ENVIRONMENT` | `development` | One of `development`, `test`, `staging`, or `production` |
| `MESSQRY_DOCS_ENABLED` | `true` | Enables or disables interactive API documentation |
| `MESSQRY_DATABASE_URL` | `sqlite:///./messqry.db` | Secret SQLAlchemy database URL; staging and production require PostgreSQL and the `postgres` extra |

Production startup rejects SQLite and requires `MESSQRY_DOCS_ENABLED=false`. Database URLs are kept as secret settings and masked in settings representations and validation errors. Supply production secrets through the runtime secret manager, not a committed `.env` file.

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
- `POST /api/v1/models/{model_id}/versions/{version_number}/evaluations` — compare supplied numeric metrics against supplied thresholds and record the result.
- `GET /api/v1/models/{model_id}/versions/{version_number}/evaluations` — list evaluation records for a version.
- `PUT /api/v1/models/{model_id}/deployments/{environment}` — select a registered version for an environment and record a deployment or promotion.
- `POST /api/v1/models/{model_id}/deployments/{environment}/rollback` — select a previously registered version and record a rollback.
- `GET /api/v1/models/{model_id}/deployments` — list current version selections.
- `GET /api/v1/models/{model_id}/deployments/{environment}/history` — list deployment changes for an environment.

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

Build the product in lifecycle order: model registration and version history; evaluation and promotion policies; runtime deployment and prediction; request and operational monitoring; then optional retraining automation. Evaluation currently checks caller-supplied metrics against caller-supplied thresholds and records pass/fail; it does not run a model or benchmark dataset, and promotion is not yet gated on evaluation status. The current deployment API changes a database pointer and records history; it does not start a serving process or route prediction traffic. The registry stores artifact URIs and metadata only; it does not upload, inspect, or serve artifacts. Keep model and tenant data isolated, protect prediction endpoints, and make deployment changes auditable as those capabilities are added.
