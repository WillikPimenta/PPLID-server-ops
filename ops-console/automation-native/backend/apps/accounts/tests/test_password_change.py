from datetime import date

from django.contrib.auth import authenticate, get_user_model
from django.test import Client, TestCase, override_settings

from apps.workforce.models import Agent
from apps.workforce.services.portal_user_sync import provision_portal_users

User = get_user_model()


class PasswordChangeFlowTests(TestCase):
    def setUp(self):
        self.client = Client()

    def _agent(self, lan: str) -> Agent:
        return Agent.objects.create(
            user_lan_id=lan,
            full_name="Test User",
            active=True,
            hire_date=date(2020, 1, 1),
        )

    def _provision(self, lan: str) -> None:
        # A política atual cria contas inativas por padrão. Estes testes cobrem
        # o fluxo de senha de uma conta explicitamente liberada para login.
        provision_portal_users(config={"always_active": [lan]})

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_provision_sets_must_change_password(self):
        self._agent("c92001a")
        self._provision("c92001a")
        user = User.objects.get(username="c92001a")
        self.assertTrue(user.must_change_password)
        self.assertTrue(user.check_password("defaultpass"))

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_login_with_default_password_succeeds(self):
        self._agent("c92002a")
        self._provision("c92002a")
        user = authenticate(username="c92002a", password="defaultpass")
        self.assertIsNotNone(user)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_login_returns_must_change_password_flag(self):
        self._agent("c92003a")
        self._provision("c92003a")
        self.client.get("/api/v1/auth/csrf/")
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "C92003A", "password": "defaultpass"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["must_change_password"])

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_me_returns_flag_while_pending(self):
        self._agent("c92004a")
        self._provision("c92004a")
        user = User.objects.get(username="c92004a")
        self.client.force_login(user)
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["authenticated"])
        self.assertTrue(data["user"]["must_change_password"])

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_change_password_clears_flag(self):
        self._agent("c92005a")
        self._provision("c92005a")
        user = User.objects.get(username="c92005a")
        self.client.force_login(user)

        response = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "new_password": "newpass123",
                "confirm_password": "newpass123",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["must_change_password"])

        user.refresh_from_db()
        self.assertFalse(user.must_change_password)
        self.assertTrue(user.check_password("newpass123"))

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_change_password_wrong_current_when_not_forced(self):
        self._agent("c92006a")
        self._provision("c92006a")
        user = User.objects.get(username="c92006a")
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        self.client.force_login(user)

        response = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "current_password": "wrongpass",
                "new_password": "newpass123",
                "confirm_password": "newpass123",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Senha atual incorreta.")

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_change_password_requires_current_when_not_forced(self):
        self._agent("c92006b")
        self._provision("c92006b")
        user = User.objects.get(username="c92006b")
        user.must_change_password = False
        user.save(update_fields=["must_change_password"])
        self.client.force_login(user)

        response = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "new_password": "newpass123",
                "confirm_password": "newpass123",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("current_password", response.json())

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_change_password_too_short(self):
        self._agent("c92007a")
        self._provision("c92007a")
        user = User.objects.get(username="c92007a")
        self.client.force_login(user)

        response = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "new_password": "short",
                "confirm_password": "short",
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("new_password", response.json())

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="defaultpass")
    def test_cannot_access_home_logic(self):
        self._agent("c92008a")
        self._provision("c92008a")
        user = User.objects.get(username="c92008a")
        self.assertTrue(user.must_change_password)

        self.client.force_login(user)
        me = self.client.get("/api/v1/auth/me/")
        self.assertTrue(me.json()["user"]["must_change_password"])

        change = self.client.post(
            "/api/v1/auth/change-password/",
            {
                "new_password": "personal123",
                "confirm_password": "personal123",
            },
            content_type="application/json",
        )
        self.assertEqual(change.status_code, 200)
        self.assertFalse(change.json()["must_change_password"])

        me_after = self.client.get("/api/v1/auth/me/")
        self.assertFalse(me_after.json()["user"]["must_change_password"])
