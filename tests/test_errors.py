import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from starlette.requests import Request

from messqry.api.errors import (
    http_exception_handler,
    request_validation_handler,
    unhandled_exception_handler,
)


@pytest.mark.parametrize(
    ("status_code", "headers"),
    [
        (409, None),
        (405, {"Allow": "GET"}),
        (401, {"WWW-Authenticate": "Bearer"}),
        (429, {"Retry-After": "30"}),
    ],
)
def test_http_errors_use_stable_error_envelope_and_preserve_headers(status_code, headers):
    request = Mock(spec=Request)
    response = http_exception_handler(
        request,
        HTTPException(status_code=status_code, detail="Request failed", headers=headers),
    )

    assert response.status_code == status_code
    if headers:
        for name, value in headers.items():
            assert response.headers[name] == value
    assert json.loads(response.body)["error"]["message"] == "Request failed"


def test_validation_and_internal_errors_do_not_echo_sensitive_values(caplog):
    request = Mock(spec=Request)
    sensitive_value = "customer-sensitive-input"
    validation = RequestValidationError(
        [
            {
                "loc": ("body", "name"),
                "type": "string_type",
                "input": sensitive_value,
                "msg": f"invalid value {sensitive_value}",
            }
        ]
    )
    validation_response = request_validation_handler(request, validation)
    validation_text = validation_response.body.decode()
    assert validation_response.status_code == 422
    assert sensitive_value not in validation_text
    assert json.loads(validation_text) == {
        "error": {
            "code": "validation_error",
            "message": "Request validation failed",
            "fields": [{"location": ["body", "name"], "code": "string_type"}],
        }
    }

    internal_response = unhandled_exception_handler(
        request, RuntimeError(f"database password: {sensitive_value}")
    )
    assert internal_response.status_code == 500
    assert sensitive_value not in internal_response.body.decode()
    assert json.loads(internal_response.body) == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred"}
    }
    assert sensitive_value not in caplog.text
    assert "RuntimeError" in caplog.text
