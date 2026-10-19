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
from messqry.api.routes import models_router, router
from messqry.config import get_settings
from messqry.db import dispose_engine

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Release pooled database connections when the API stops."""

    try:
        yield
    finally:
        dispose_engine()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(models_router)
app.include_router(deployments_router)
app.include_router(evaluations_router)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, request_validation_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)
