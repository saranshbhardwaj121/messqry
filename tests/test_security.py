from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from messqry.api import security
from messqry.config import Settings


@pytest.fixture
def signing_keys():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def test_cognito_access_token_requires_issuer_audience_and_scopes(monkeypatch, signing_keys):
    private_pem, public_pem = signing_keys

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_pem)

    monkeypatch.setattr(security, "_jwk_client", lambda issuer: FakeJwkClient())
    settings = Settings(
        oidc_issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
        oidc_audience="web-client",
        oidc_service_client_ids="worker-client",
    )
    token = jwt.encode(
        {
            "iss": settings.oidc_issuer,
            "sub": "user-123",
            "token_use": "access",
            "client_id": "web-client",
            "scope": "messqry/registry.read messqry/deployment.write unrelated/scope",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    principal = security._verify_cognito_token(token, settings)

    assert principal.subject == "user-123"
    assert principal.kind == "user"
    assert principal.scopes == frozenset({"messqry/registry.read", "messqry/deployment.write"})


def test_cognito_machine_client_is_classified_as_service(signing_keys, monkeypatch):
    private_pem, public_pem = signing_keys

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_pem)

    monkeypatch.setattr(security, "_jwk_client", lambda issuer: FakeJwkClient())
    settings = Settings(
        oidc_issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
        oidc_audience="web-client",
        oidc_service_client_ids="worker-client",
    )
    token = jwt.encode(
        {
            "iss": settings.oidc_issuer,
            "sub": "service-123",
            "token_use": "access",
            "client_id": "worker-client",
            "scope": "messqry/registry.write",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "test-key"},
    )

    principal = security._verify_cognito_token(token, settings)

    assert principal.kind == "service"
    assert principal.scopes == frozenset({"messqry/registry.write"})


def test_cognito_token_from_unconfigured_client_is_rejected(signing_keys, monkeypatch):
    private_pem, public_pem = signing_keys

    class FakeJwkClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_pem)

    monkeypatch.setattr(security, "_jwk_client", lambda issuer: FakeJwkClient())
    settings = Settings(
        oidc_issuer="https://cognito-idp.us-east-1.amazonaws.com/us-east-1_example",
        oidc_audience="web-client",
        oidc_service_client_ids="worker-client",
    )
    token = jwt.encode(
        {
            "iss": settings.oidc_issuer,
            "sub": "attacker-client",
            "token_use": "access",
            "client_id": "unknown-client",
            "scope": "messqry/registry.write",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        },
        private_pem,
        algorithm="RS256",
    )

    with pytest.raises(HTTPException) as rejected:
        security._verify_cognito_token(token, settings)
    assert rejected.value.status_code == 401


def test_protected_routes_reject_missing_tokens_outside_local_development():
    request = Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/models",
            "raw_path": b"/api/v1/models",
            "query_string": b"",
            "headers": [],
            "server": ("api.example.com", 443),
            "client": ("127.0.0.1", 1000),
        }
    )
    with pytest.raises(HTTPException) as rejected:
        security.get_current_principal(request, None, Settings(auth_dev_bypass=False))
    assert rejected.value.status_code == 401
    assert rejected.value.headers["WWW-Authenticate"] == "Bearer"


def test_scope_dependency_denies_missing_scope():
    require_write = security.require_scope("messqry/registry.write")
    with pytest.raises(HTTPException) as rejected:
        require_write(security.Principal("reader", "user", frozenset({"messqry/registry.read"})))
    assert rejected.value.status_code == 403


def test_deployment_scope_dependency_allows_only_matching_grants():
    require_deployment_write = security.require_scope("messqry/deployment.write")
    writer = security.Principal("operator", "user", frozenset({"messqry/deployment.write"}))
    reader = security.Principal("viewer", "user", frozenset({"messqry/deployment.read"}))

    assert require_deployment_write(writer) is writer
    with pytest.raises(HTTPException) as rejected:
        require_deployment_write(reader)
    assert rejected.value.status_code == 403
