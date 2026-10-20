"""Authorization primitives for request identities verified by the auth integration."""

from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWTError
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

from messqry.config import Settings, get_settings

_BEARER = HTTPBearer(auto_error=False)
_KNOWN_SCOPES = frozenset(
    {
        "messqry/registry.read",
        "messqry/registry.write",
        "messqry/deployment.read",
        "messqry/deployment.write",
    }
)
_DEVELOPMENT_SCOPES = _KNOWN_SCOPES


@dataclass(frozen=True)
class Principal:
    """Verified single-organization user or service identity."""

    subject: str
    kind: Literal["user", "service"]
    scopes: frozenset[str]


@lru_cache(maxsize=8)
def _jwk_client(issuer: str) -> PyJWKClient:
    return PyJWKClient(f"{issuer.rstrip('/')}/.well-known/jwks.json")


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A valid access token is required",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _verify_cognito_token(token: str, settings: Settings) -> Principal:
    issuer = settings.oidc_issuer
    audience = settings.oidc_audience
    service_client_ids = settings.service_client_ids
    if not issuer or not audience or not service_client_ids:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="OIDC authentication is not configured",
        )

    try:
        signing_key = _jwk_client(issuer).get_signing_key_from_jwt(token)
        import jwt

        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={
                "verify_aud": False,
                "require": ["exp", "iss", "sub", "token_use", "client_id"],
            },
        )
    except PyJWKClientConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The identity provider could not be reached",
        ) from exc
    except (PyJWTError, PyJWKClientError, ValueError) as exc:
        raise _unauthorized() from exc

    client_id = claims.get("client_id")
    if claims.get("token_use") != "access" or not isinstance(client_id, str):
        raise _unauthorized()
    allowed_clients = service_client_ids | {audience}
    if client_id not in allowed_clients:
        raise _unauthorized()
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise _unauthorized()

    token_scopes = claims.get("scope", "")
    if not isinstance(token_scopes, str):
        raise _unauthorized()
    scopes = frozenset(token_scopes.split()) & _KNOWN_SCOPES
    return Principal(
        subject=subject,
        kind="service" if client_id in service_client_ids else "user",
        scopes=scopes,
    )


def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
    settings: Settings = Depends(get_settings),
) -> Principal:
    """Verify Cognito access tokens; allow an explicit local-only development mode."""

    principal = getattr(request.state, "principal", None)
    if isinstance(principal, Principal):
        return principal
    if credentials is None:
        if settings.environment == "development" and settings.auth_dev_bypass:
            return Principal("local-development", "user", _DEVELOPMENT_SCOPES)
        raise _unauthorized()
    if credentials.scheme.lower() != "bearer":
        raise _unauthorized()
    return _verify_cognito_token(credentials.credentials, settings)


class ScopeRequirement:
    """Require one explicit scope and retain it for route-level verification."""

    def __init__(self, scope: str) -> None:
        self.scope = scope

    def __call__(self, principal: Principal = Depends(get_current_principal)) -> Principal:
        if self.scope not in principal.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The credential does not grant the required scope",
            )
        return principal


def require_scope(scope: str) -> ScopeRequirement:
    """Build a dependency requiring one explicit API scope."""

    return ScopeRequirement(scope)
