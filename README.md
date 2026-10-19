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

## Direction

Build the product in lifecycle order: model registration and version history; evaluation and promotion policies; runtime deployment and prediction; request and operational monitoring; then optional retraining automation. Evaluation currently checks caller-supplied metrics against caller-supplied thresholds and records pass/fail; it does not run a model or benchmark dataset, and promotion is not yet gated on evaluation status. The current deployment API changes a database pointer and records history; it does not start a serving process or route prediction traffic. The registry stores artifact URIs and metadata only; it does not upload, inspect, or serve artifacts. Keep model and tenant data isolated, protect prediction endpoints, and make deployment changes auditable as those capabilities are added.
