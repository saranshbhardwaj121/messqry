"""Stable, non-sensitive error responses for API failures."""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

logger = logging.getLogger("messqry.api")


class ValidationIssue(BaseModel):
    """Safe pointer to a request field that failed validation."""

    location: list[str | int]
    code: str


class ErrorDetail(BaseModel):
    """Machine-readable error code and a short user-facing message."""

    code: str
    message: str
    fields: list[ValidationIssue] | None = None


class ErrorResponse(BaseModel):
    """Common API error envelope."""

    error: ErrorDetail


def _error_response(
    status_code: int,
    code: str,
    message: str,
    fields: list[ValidationIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(
        error=ErrorDetail(code=code, message=message, fields=fields)
    ).model_dump(exclude_none=True)
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Translate expected HTTP errors into the shared envelope."""

    codes = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "validation_error",
        503: "service_unavailable",
    }
    message = exc.detail if isinstance(exc.detail, str) else "Request failed"
    if exc.status_code >= 500:
        message = "The service could not complete the request"
    return _error_response(
        exc.status_code,
        codes.get(exc.status_code, "http_error"),
        message,
        headers=exc.headers,
    )


def request_validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Return field locations and error types without echoing request values."""

    fields = [
        ValidationIssue(location=list(error.get("loc", ())), code=str(error.get("type", "invalid")))
        for error in exc.errors()
    ]
    return _error_response(422, "validation_error", "Request validation failed", fields)


def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    """Hide implementation details and credentials from unexpected failures."""

    logger.error("Unhandled API exception type: %s", type(exc).__name__)
    return _error_response(500, "internal_error", "An unexpected error occurred")
