from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.access.registry import ADM_FALHAS_IMPORT, OPERACAO_JORNADA_PAINEL_VIEW
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class AuthMeAccessTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_me_unauthenticated(self):
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["authenticated"])
        self.assertIsNone(data["user"])

    def test_me_includes_access_block(self):
        user = User.objects.create_user("c90301a", password="test123")
        agent = Agent.objects.create(
            user_lan_id="c90301a",
            full_name="Auth Me Agent",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional/Fraud",
            job_title="Agente Backoffice I",
            start_date=date(2024, 1, 1),
            active=True,
        )
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        user.groups.add(group)

        self.client.force_login(user)
        response = self.client.get("/api/v1/auth/me/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["authenticated"])
        self.assertIn("access", data)
        self.assertIn("roles", data["access"])
        self.assertIn("permissions", data["access"])
        self.assertIn(ROLE_ADM_PORTAL, data["access"]["roles"])
        self.assertIn(ADM_FALHAS_IMPORT, data["access"]["permissions"])

    def test_login_and_me_flow(self):
        user = User.objects.create_user("c90302a", password="test12345")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        user.groups.add(group)
        csrf = self.client.get("/api/v1/auth/csrf/")
        self.assertEqual(csrf.status_code, 200)

        login = self.client.post(
            "/api/v1/auth/login/",
            {"username": "c90302a", "password": "test12345"},
            content_type="application/json",
        )
        self.assertEqual(login.status_code, 200)
        login_data = login.json()
        self.assertIn("access", login_data)
        self.assertIn(ROLE_ADM_PORTAL, login_data["access"]["roles"])
        self.assertIn(OPERACAO_JORNADA_PAINEL_VIEW, login_data["access"]["permissions"])

        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.json()["authenticated"])
        self.assertIn("access", me.json())
