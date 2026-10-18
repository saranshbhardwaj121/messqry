"""FastAPI application entry point."""

from fastapi import FastAPI

from messqry.api.routes import models_router, router
from messqry.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)
app.include_router(router)
app.include_router(models_router)
