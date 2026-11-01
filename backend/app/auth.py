import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import (
    InvalidTokenError,
    PyJWKClientConnectionError,
    PyJWKClientError,
)


APPLICATION_ROLES = frozenset({
    "ADMIN",
    "FACULTY",
    "STUDENT",
})
SAFE_ASYMMETRIC_ALGORITHMS = frozenset({
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
})
MAX_DISCOVERY_DOCUMENT_BYTES = 1_048_576
TOKEN_CLOCK_LEEWAY_SECONDS = 30

bearer_scheme = HTTPBearer(auto_error=False)


class OIDCConfigurationError(Exception):
    pass


@dataclass(frozen=True)
class OIDCSettings:
    issuer: str
    audience: str
    frontend_client_id: str
    algorithms: tuple[str, ...]
    role_claim: str
    department_claim: str


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    issuer: str
    subject: str
    roles: tuple[str, ...]
    departments: tuple[str, ...]
    username: str | None


def _required_setting(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise OIDCConfigurationError(f"{name} is not configured.")
    return value


def load_oidc_settings() -> OIDCSettings:
    issuer = _required_setting("KEYCLOAK_ISSUER_URL").rstrip("/")
    issuer_parts = urlsplit(issuer)
    if (
        issuer_parts.scheme not in {"https", "http"}
        or not issuer_parts.hostname
        or issuer_parts.query
        or issuer_parts.fragment
    ):
        raise OIDCConfigurationError(
            "KEYCLOAK_ISSUER_URL must be an absolute HTTP(S) issuer URL."
        )
    if issuer_parts.scheme != "https" and issuer_parts.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise OIDCConfigurationError(
            "HTTP is allowed only for a loopback development issuer."
        )

    algorithms = tuple(
        algorithm.strip()
        for algorithm in _required_setting(
            "KEYCLOAK_ALLOWED_ALGORITHMS"
        ).split(",")
        if algorithm.strip()
    )
    if (
        not algorithms
        or len(set(algorithms)) != len(algorithms)
        or any(
            algorithm not in SAFE_ASYMMETRIC_ALGORITHMS
            for algorithm in algorithms
        )
    ):
        raise OIDCConfigurationError(
            "KEYCLOAK_ALLOWED_ALGORITHMS must contain only explicitly "
            "approved asymmetric signing algorithms."
        )

    return OIDCSettings(
        issuer=issuer,
        audience=_required_setting("KEYCLOAK_API_AUDIENCE"),
        frontend_client_id=_required_setting(
            "KEYCLOAK_FRONTEND_CLIENT_ID"
        ),
        algorithms=algorithms,
        role_claim=os.getenv(
            "KEYCLOAK_ROLE_CLAIM",
            "realm_access.roles",
        ).strip(),
        department_claim=os.getenv(
            "KEYCLOAK_DEPARTMENT_CLAIM",
            "cit_department_groups",
        ).strip(),
    )


def _same_origin(left: str, right: str) -> bool:
    left_parts = urlsplit(left)
    right_parts = urlsplit(right)
    return (
        left_parts.scheme == right_parts.scheme
        and left_parts.hostname == right_parts.hostname
        and left_parts.port == right_parts.port
    )


@lru_cache(maxsize=4)
def get_jwks_client(issuer: str):
    discovery_url = f"{issuer}/.well-known/openid-configuration"
    request = Request(
        discovery_url,
        headers={"Accept": "application/json"},
    )

    try:
        with urlopen(request, timeout=5) as response:
            document_bytes = response.read(MAX_DISCOVERY_DOCUMENT_BYTES + 1)
    except (OSError, TimeoutError, URLError) as error:
        raise OIDCConfigurationError(
            "Could not retrieve Keycloak OIDC discovery metadata."
        ) from error

    if len(document_bytes) > MAX_DISCOVERY_DOCUMENT_BYTES:
        raise OIDCConfigurationError(
            "Keycloak OIDC discovery metadata exceeds the size limit."
        )

    try:
        metadata = json.loads(document_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise OIDCConfigurationError(
            "Keycloak returned invalid OIDC discovery metadata."
        ) from error

    if not isinstance(metadata, dict) or metadata.get("issuer") != issuer:
        raise OIDCConfigurationError(
            "The discovery document issuer does not match the configured issuer."
        )

    jwks_uri = metadata.get("jwks_uri")
    if (
        not isinstance(jwks_uri, str)
        or not jwks_uri
        or not _same_origin(issuer, jwks_uri)
    ):
        raise OIDCConfigurationError(
            "The discovery document JWKS endpoint must share the issuer origin."
        )

    issuer_scheme = urlsplit(issuer).scheme
    if issuer_scheme == "https" and urlsplit(jwks_uri).scheme != "https":
        raise OIDCConfigurationError(
            "The JWKS endpoint must use HTTPS outside local development."
        )

    from jwt import PyJWKClient

    return PyJWKClient(
        jwks_uri,
        cache_jwk_set=True,
        lifespan=300,
        timeout=5,
    )


def _claim_value(claims: dict[str, Any], claim_path: str) -> Any:
    value: Any = claims
    for path_part in claim_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(path_part)
    return value


def verify_access_token(
    token: str,
    settings: OIDCSettings,
) -> dict[str, Any]:
    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")
    if (
        algorithm not in settings.algorithms
        or algorithm not in SAFE_ASYMMETRIC_ALGORITHMS
    ):
        raise InvalidTokenError("The token uses a disallowed algorithm.")

    signing_key = get_jwks_client(
        settings.issuer
    ).get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=list(settings.algorithms),
        audience=settings.audience,
        issuer=settings.issuer,
        leeway=TOKEN_CLOCK_LEEWAY_SECONDS,
        options={
            "require": ["exp", "iss", "sub", "aud"],
        },
    )

    if claims.get("azp") != settings.frontend_client_id:
        raise InvalidTokenError(
            "The token was not issued to the configured frontend client."
        )

    return claims


def _principal_from_claims(
    claims: dict[str, Any],
    settings: OIDCSettings,
) -> AuthenticatedPrincipal:
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject:
        raise InvalidTokenError("The token subject is invalid.")

    raw_roles = _claim_value(claims, settings.role_claim)
    if not isinstance(raw_roles, list) or any(
        not isinstance(role, str) for role in raw_roles
    ):
        raise InvalidTokenError("The token role claim is invalid.")

    roles = tuple(sorted(
        role.upper()
        for role in raw_roles
        if role.upper() in APPLICATION_ROLES
    ))
    raw_department_groups = _claim_value(claims, settings.department_claim)
    if raw_department_groups is None:
        raw_department_groups = []
    if not isinstance(raw_department_groups, list) or any(
        not isinstance(group, str) for group in raw_department_groups
    ):
        raise InvalidTokenError("The token department claim is invalid.")
    department_codes = {
        match.group(1)
        for group in raw_department_groups
        if (match := re.fullmatch(r"/departments/([A-Za-z0-9_-]+)", group))
    }
    username = claims.get("preferred_username")

    return AuthenticatedPrincipal(
        issuer=settings.issuer,
        subject=subject,
        roles=roles,
        departments=tuple(sorted(department_codes)),
        username=username if isinstance(username, str) else None,
    )


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> AuthenticatedPrincipal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer access token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        settings = load_oidc_settings()
    except OIDCConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak authentication is not configured.",
        ) from error

    try:
        claims = verify_access_token(credentials.credentials, settings)
        return _principal_from_claims(claims, settings)
    except PyJWKClientConnectionError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak signing keys are temporarily unavailable.",
        ) from error
    except OIDCConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keycloak authentication configuration is unavailable.",
        ) from error
    except (InvalidTokenError, PyJWKClientError, ValueError, TypeError) as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or unverifiable bearer access token.",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def require_authenticated_user(
    principal: AuthenticatedPrincipal = Depends(get_current_principal),
) -> AuthenticatedPrincipal:
    return principal


def require_roles(*allowed_roles: str):
    required_roles = frozenset(role.upper() for role in allowed_roles)
    if not required_roles or not required_roles.issubset(APPLICATION_ROLES):
        raise ValueError("At least one valid application role is required.")

    def role_dependency(
        principal: AuthenticatedPrincipal = Depends(get_current_principal),
    ) -> AuthenticatedPrincipal:
        if not required_roles.intersection(principal.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account does not have permission to access this resource.",
            )
        return principal

    return role_dependency
