"""Sessão do portal não pode ser encerrada pela validação Okta das automações."""

from datetime import date
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


@override_settings(ALLOWED_HOSTS=["testserver", "localhost", "127.0.0.1"])
class OktaValidateKeepsPortalSessionTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.user = User.objects.create_user("c90401a", password="test12345")
        agent = Agent.objects.create(
            user_lan_id="c90401a",
            full_name="Okta Session Agent",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        UserProfile.objects.create(user=self.user, agent=agent)
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.user.groups.add(group)

    def _login(self):
        csrf = self.client.get("/api/v1/auth/csrf/")
        token = csrf.json()["csrfToken"]
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "c90401a", "password": "test12345"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_ORIGIN="http://localhost:5174",
        )
        self.assertEqual(response.status_code, 200)
        return response.json().get("csrfToken") or token

    def test_credentials_validate_does_not_clear_portal_session(self):
        token = self._login()
        me_before = self.client.get("/api/v1/auth/me/")
        self.assertTrue(me_before.json()["authenticated"])

        robot = MagicMock()
        robot.start_okta_credentials_validation.return_value = (
            True,
            "Validação Okta iniciada",
            {
                "validated": False,
                "checking": True,
                "message": "Validando credenciais no Okta...",
                "last_checked_at": None,
            },
        )

        with patch("apps.automacoes.views.get_robot_manager", return_value=robot):
            validate = self.client.post(
                "/api/v1/automacoes/credentials/validate/",
                {
                    "matricula": "c90401a",
                    "senha": "secret",
                    "headless": True,
                    "client_id": "okta-test-client",
                },
                content_type="application/json",
                HTTP_X_CSRFTOKEN=token,
                HTTP_ORIGIN="http://localhost:5174",
                HTTP_X_AUTOMACAO_CLIENT_ID="okta-test-client",
            )

        self.assertEqual(validate.status_code, 200)
        self.assertTrue(validate.json().get("ok"))

        me_after = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me_after.status_code, 200)
        self.assertTrue(me_after.json()["authenticated"])
        self.assertEqual(me_after.json()["user"]["username"], "c90401a")

    def test_monitoramento_local_origin_is_csrf_trusted_in_debug(self):
        from django.conf import settings

        self.assertIn(
            "http://monitoramento.local:5174",
            settings.CSRF_TRUSTED_ORIGINS,
        )
        csrf = self.client.get("/api/v1/auth/csrf/")
        token = csrf.json()["csrfToken"]
        # Origem LAN por hostname (Vite allowedHosts) — não pode 403 CSRF.
        response = self.client.post(
            "/api/v1/auth/login/",
            {"username": "c90401a", "password": "wrong"},
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
            HTTP_ORIGIN="http://monitoramento.local:5174",
        )
        # Credencial inválida (400), não falha de CSRF (403).
        self.assertEqual(response.status_code, 400)
