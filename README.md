# MessQry AI

MessQry AI is an MLOps platform concept for helping teams register, deploy, monitor, evaluate, and safely update machine-learning models.

This repository contains an initial backend foundation: a configurable FastAPI application, a liveness endpoint, and a persistent registry for model metadata and version records. Model artifact storage, serving, authentication, monitoring, evaluation, and AWS deployment are not implemented yet.

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
| `MESSQRY_ENVIRONMENT` | `development` | Runtime environment label |
| `MESSQRY_DOCS_ENABLED` | `true` | Enables or disables interactive API documentation |
| `MESSQRY_DATABASE_URL` | `sqlite:///./messqry.db` | SQLAlchemy database URL; PostgreSQL requires the `postgres` extra |

## Current API

- `GET /health` — reports that the API process is running. It does not yet check a database, model server, or other dependency.
- `POST /api/v1/models` — create a logical model record.
- `GET /api/v1/models` — list registered models.
- `GET /api/v1/models/{model_id}` — retrieve a model and its version records.
- `POST /api/v1/models/{model_id}/versions` — register metadata for the next model version.
- `GET /api/v1/models/{model_id}/versions` — list model versions.

## Direction

Build the product in lifecycle order: model registration and version history; controlled deployment; request and operational monitoring; evaluation and promotion; then optional retraining automation. The registry stores artifact URIs and metadata only; it does not upload, inspect, or serve artifacts. Keep model and tenant data isolated, protect prediction endpoints, and make deployment changes auditable as those capabilities are added.
