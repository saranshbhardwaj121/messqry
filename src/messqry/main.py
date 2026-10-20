"""FastAPI application entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from messqry.api.deployments import router as deployments_router
from messqry.api.errors import (
    http_exception_handler,
    request_validation_handler,
    unhandled_exception_handler,
)
from messqry.api.evaluations import router as evaluations_router
from messqry.api.middleware import ArtifactUploadBodyLimitMiddleware
from messqry.api.routes import models_router, router
from messqry.artifact_storage import create_artifact_store
from messqry.config import get_settings
from messqry.db import dispose_engine
from messqry.package_validation import PackageLimits
from messqry.runtime.ecs import create_runtime_manager

settings = get_settings()


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Release pooled database connections when the API stops."""

    artifact_store = None
    runtime_manager = None
    try:
        artifact_store = create_artifact_store(settings)
        application.state.artifact_store = artifact_store
        runtime_manager = create_runtime_manager(settings)
        application.state.runtime_manager = runtime_manager
        yield
    finally:
        close_runtime_manager = getattr(runtime_manager, "close", None)
        if close_runtime_manager is not None:
            close_runtime_manager()
        close = getattr(getattr(artifact_store, "client", None), "close", None)
        if close is not None:
            close()
        dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)
app.add_middleware(
    ArtifactUploadBodyLimitMiddleware,
    max_body_bytes=PackageLimits().max_archive_bytes + 1024 * 1024,
    prediction_max_body_bytes=1024 * 1024,
)
app.include_router(router)
app.include_router(models_router)
app.include_router(deployments_router)
app.include_router(evaluations_router)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
