import unittest

from fastapi.testclient import TestClient

from app.auth import AuthenticatedPrincipal, get_current_principal
from app.main import app


class RoleAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()

    def use_role(self, role):
        principal = AuthenticatedPrincipal(
            issuer="test-issuer",
            subject=f"{role.lower()}-user",
            roles=(role,),
            departments=(),
            username=f"{role.lower()}-user",
        )
        app.dependency_overrides[get_current_principal] = lambda: principal

    def test_api_me_requires_authentication(self):
        response = self.client.get("/api/auth/me")
        self.assertEqual(response.status_code, 401)

    def test_crisis_information_is_available_to_admin_and_faculty_only(self):
        for role in ("ADMIN", "FACULTY"):
            with self.subTest(role=role):
                self.use_role(role)
                response = self.client.get("/api/crisis/options")
                self.assertEqual(response.status_code, 200)

        self.use_role("STUDENT")
        response = self.client.get("/api/crisis/options")
        self.assertEqual(response.status_code, 403)

    def test_analytics_is_admin_only(self):
        self.use_role("FACULTY")
        denied = self.client.get("/api/analytics/room-utilization")
        self.assertEqual(denied.status_code, 403)

        self.use_role("ADMIN")
        allowed = self.client.get("/api/analytics/room-utilization")
        self.assertEqual(allowed.status_code, 200)


if __name__ == "__main__":
    unittest.main()
