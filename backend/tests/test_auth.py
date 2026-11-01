import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from app.auth import (
    OIDCConfigurationError,
    OIDCSettings,
    get_current_principal,
    get_jwks_client,
    load_oidc_settings,
    verify_access_token,
)
from app.main import app


ISSUER = "http://localhost:8080/realms/cit-nexus"
AUDIENCE = "cit-nexus-api"
CLIENT_ID = "cit-nexus-frontend"


class FakeJwksClient:
    def __init__(self, public_key):
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token):
        if not token:
            raise ValueError("A bearer token is required.")
        return SimpleNamespace(key=self.public_key)


class AuthenticationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        cls.public_key = cls.private_key.public_key()
        cls.settings = OIDCSettings(
            issuer=ISSUER,
            audience=AUDIENCE,
            frontend_client_id=CLIENT_ID,
            algorithms=("RS256",),
            role_claim="realm_access.roles",
            department_claim="cit_department_groups",
        )

    def setUp(self):
        self.jwks_patch = patch(
            "app.auth.get_jwks_client",
            return_value=FakeJwksClient(self.public_key),
        )
        self.jwks_patch.start()
        self.addCleanup(self.jwks_patch.stop)

    def make_token(self, **overrides):
        now = datetime.now(timezone.utc)
        claims = {
            "iss": ISSUER,
            "sub": "stable-user-subject",
            "aud": AUDIENCE,
            "azp": CLIENT_ID,
            "iat": now,
            "nbf": now - timedelta(seconds=5),
            "exp": now + timedelta(minutes=5),
            "preferred_username": "verified-user",
            "realm_access": {
                "roles": [
                    "ADMIN",
                    "FACULTY",
                    "STUDENT",
                    "other-realm-role",
                ],
            },
            "cit_department_groups": ["/departments/CSE"],
        }
        claims.update(overrides)
        return jwt.encode(
            claims,
            self.private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    def test_verifies_signature_claims_and_stable_identity(self):
        oidc_environment = {
            "KEYCLOAK_ISSUER_URL": ISSUER,
            "KEYCLOAK_API_AUDIENCE": AUDIENCE,
            "KEYCLOAK_FRONTEND_CLIENT_ID": CLIENT_ID,
            "KEYCLOAK_ALLOWED_ALGORITHMS": "RS256",
        }
        with patch.dict(os.environ, oidc_environment):
            principal = get_current_principal(
                HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=self.make_token(),
                )
            )

        self.assertEqual(principal.issuer, ISSUER)
        self.assertEqual(principal.subject, "stable-user-subject")
        self.assertEqual(principal.roles, ("ADMIN", "FACULTY", "STUDENT"))
        self.assertEqual(principal.departments, ("CSE",))
        self.assertEqual(principal.username, "verified-user")

    def test_auth_me_verifies_each_application_role_and_rejects_bad_tokens(self):
        oidc_environment = {
            "KEYCLOAK_ISSUER_URL": ISSUER,
            "KEYCLOAK_API_AUDIENCE": AUDIENCE,
            "KEYCLOAK_FRONTEND_CLIENT_ID": CLIENT_ID,
            "KEYCLOAK_ALLOWED_ALGORITHMS": "RS256",
        }
        client = TestClient(app)
        app.dependency_overrides.clear()
        self.addCleanup(app.dependency_overrides.clear)

        with patch.dict(os.environ, oidc_environment):
            for role in ("ADMIN", "FACULTY", "STUDENT"):
                with self.subTest(role=role):
                    response = client.get(
                        "/api/auth/me",
                        headers={
                            "Authorization": (
                                f"Bearer {self.make_token(realm_access={'roles': [role]})}"
                            ),
                        },
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["roles"], [role])

            invalid_tokens = (
                (
                    "expired",
                    self.make_token(
                        exp=datetime.now(timezone.utc) - timedelta(minutes=1)
                    ),
                ),
                ("incorrect-audience", self.make_token(aud="another-api")),
                ("malformed", "not-a-jwt"),
            )
            for label, token in invalid_tokens:
                with self.subTest(token_type=label):
                    response = client.get(
                        "/api/auth/me",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    self.assertEqual(response.status_code, 401)

    def test_rejects_missing_bearer_token(self):
        with self.assertRaises(HTTPException) as raised:
            get_current_principal(None)

        self.assertEqual(raised.exception.status_code, 401)

    def test_rejects_wrong_issuer_audience_and_authorized_client(self):
        for claims in (
            {"iss": "https://attacker.example/realm"},
            {"aud": "another-api"},
            {"azp": "another-client"},
        ):
            with self.subTest(claims=claims):
                with self.assertRaises(jwt.InvalidTokenError):
                    verify_access_token(
                        self.make_token(**claims),
                        self.settings,
                    )

    def test_rejects_expired_and_not_yet_valid_tokens(self):
        now = datetime.now(timezone.utc)
        for claims in (
            {"exp": now - timedelta(minutes=1)},
            {"nbf": now + timedelta(minutes=1)},
        ):
            with self.subTest(claims=claims):
                with self.assertRaises(jwt.InvalidTokenError):
                    verify_access_token(
                        self.make_token(**claims),
                        self.settings,
                    )

    def test_rejects_invalid_signature(self):
        other_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        token = jwt.encode(
            {
                "iss": ISSUER,
                "sub": "user",
                "aud": AUDIENCE,
                "azp": CLIENT_ID,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            other_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

        with self.assertRaises(jwt.InvalidTokenError):
            verify_access_token(token, self.settings)

    def test_rejects_disallowed_signing_algorithm(self):
        token = jwt.encode(
            {
                "iss": ISSUER,
                "sub": "user",
                "aud": AUDIENCE,
                "azp": CLIENT_ID,
                "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            },
            os.urandom(32),
            algorithm="HS256",
        )

        with self.assertRaises(jwt.InvalidTokenError):
            verify_access_token(token, self.settings)

    def test_missing_oidc_configuration_fails_closed(self):
        names = (
            "KEYCLOAK_ISSUER_URL",
            "KEYCLOAK_API_AUDIENCE",
            "KEYCLOAK_FRONTEND_CLIENT_ID",
            "KEYCLOAK_ALLOWED_ALGORITHMS",
        )
        original_values = {name: os.environ.pop(name, None) for name in names}
        self.addCleanup(
            lambda: [
                os.environ.__setitem__(name, value)
                if value is not None
                else None
                for name, value in original_values.items()
            ]
        )

        with self.assertRaises(HTTPException) as raised:
            get_current_principal(
                HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials="token",
                )
            )

        self.assertEqual(raised.exception.status_code, 503)

    def test_global_role_without_department_group_is_valid(self):
        claims = {
            "iss": ISSUER,
            "sub": "admin-subject",
            "aud": AUDIENCE,
            "azp": CLIENT_ID,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
            "realm_access": {"roles": ["ADMIN"]},
        }

        environment = {
            "KEYCLOAK_ISSUER_URL": ISSUER,
            "KEYCLOAK_API_AUDIENCE": AUDIENCE,
            "KEYCLOAK_FRONTEND_CLIENT_ID": CLIENT_ID,
            "KEYCLOAK_ALLOWED_ALGORITHMS": "RS256",
        }
        with patch.dict(os.environ, environment):
            principal = get_current_principal(
                HTTPAuthorizationCredentials(
                    scheme="Bearer",
                    credentials=jwt.encode(
                        claims,
                        self.private_key,
                        algorithm="RS256",
                        headers={"kid": "test-key"},
                    ),
                ),
            )

        self.assertEqual(principal.roles, ("ADMIN",))
        self.assertEqual(principal.departments, ())

    def test_discovery_document_must_match_configured_issuer(self):
        get_jwks_client.cache_clear()
        self.addCleanup(get_jwks_client.cache_clear)
        document = BytesIO(json.dumps({
            "issuer": "https://different.example/realm",
            "jwks_uri": "https://different.example/realm/keys",
        }).encode())

        with patch("app.auth.urlopen", return_value=document):
            with self.assertRaises(OIDCConfigurationError):
                get_jwks_client(ISSUER)

    def test_jwks_endpoint_must_share_issuer_origin(self):
        get_jwks_client.cache_clear()
        self.addCleanup(get_jwks_client.cache_clear)
        document = BytesIO(json.dumps({
            "issuer": ISSUER,
            "jwks_uri": "https://attacker.example/keys",
        }).encode())

        with patch("app.auth.urlopen", return_value=document):
            with self.assertRaises(OIDCConfigurationError):
                get_jwks_client(ISSUER)

    def test_settings_reject_symmetric_algorithms(self):
        environment = {
            "KEYCLOAK_ISSUER_URL": ISSUER,
            "KEYCLOAK_API_AUDIENCE": AUDIENCE,
            "KEYCLOAK_FRONTEND_CLIENT_ID": CLIENT_ID,
            "KEYCLOAK_ALLOWED_ALGORITHMS": "HS256",
        }

        with patch.dict(os.environ, environment):
            with self.assertRaises(OIDCConfigurationError):
                load_oidc_settings()
