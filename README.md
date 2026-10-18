# MessQry AI

MessQry AI is an MLOps platform concept for helping teams register, deploy, monitor, evaluate, and safely update machine-learning models.

This repository currently contains the smallest backend foundation: a configurable FastAPI application and a liveness endpoint. Model registry, model serving, persistence, monitoring, and AWS deployment are future capabilities, not implemented features yet.

## Run locally

Requires Python 3.11 or newer.

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
uvicorn messqry.main:app --reload --app-dir src
```

The API is available at `http://127.0.0.1:8000`. When API docs are enabled, Swagger UI is at `/docs` and the OpenAPI schema is at `/openapi.json`.

## Configuration

Copy `.env.example` to `.env` to customize local settings. Variables use the `MESSQRY_` prefix:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MESSQRY_APP_NAME` | `MessQry AI` | API title shown in OpenAPI metadata |
| `MESSQRY_ENVIRONMENT` | `development` | Runtime environment label |
| `MESSQRY_DOCS_ENABLED` | `true` | Enables or disables interactive API documentation |

## Current API

- `GET /health` — reports that the API process is running. It does not yet check a database, model server, or other dependency.

## Direction

Build the product in lifecycle order: model registration and version history; controlled deployment; request and operational monitoring; evaluation and promotion; then optional retraining automation. Keep model and tenant data isolated, protect prediction endpoints, and make deployment changes auditable as those capabilities are added.
