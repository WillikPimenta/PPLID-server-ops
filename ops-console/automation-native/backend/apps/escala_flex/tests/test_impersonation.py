"""Testes de impersonação (Alterar usuário)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import ScheduleToday, StatusType
from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class ImpersonationApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()

        self.admin_agent = Agent.objects.create(
            user_lan_id="c91763a",
            full_name="Admin Teste",
            active=True,
        )
        self.target_agent = Agent.objects.create(
            user_lan_id="agent999",
            full_name="Agente Alvo",
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.target_agent,
            team="Operacional BrFlow",
            job_title="Agente Backoffice I",
            start_date=date(2026, 1, 1),
            active=True,
        )

        self.admin_user = User.objects.create_user(
            username="admin.user",
            email="admin@test.local",
            password="test1234",
        )
        admin_group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.admin_user.groups.add(admin_group)
        UserProfile.objects.create(user=self.admin_user, agent=self.admin_agent)

        self.regular_agent = Agent.objects.create(
            user_lan_id="agent001",
            full_name="Agente Regular",
            active=True,
        )
        self.regular_user = User.objects.create_user(
            username="regular.user",
            email="regular@test.local",
            password="test1234",
        )
        UserProfile.objects.create(user=self.regular_user, agent=self.regular_agent)

    def test_admin_can_impersonate(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/v1/escala-flex/impersonate/",
            {"user_lan_id": "agent999"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["impersonating"])
        self.assertEqual(response.data["impersonate_lan_id"], "agent999")
        self.assertEqual(response.data["profile"]["lan_id"], "agent999")
        self.assertEqual(response.data["real_profile"]["lan_id"], "c91763a")
        self.assertFalse(
            any(item["key"] == "usuario" for item in response.data["profile"]["menu_items"])
        )

    def test_context_reflects_impersonation_session(self):
        self.client.force_authenticate(user=self.admin_user)
        self.client.post(
            "/api/v1/escala-flex/impersonate/",
            {"user_lan_id": "agent999"},
            format="json",
        )
        response = self.client.get("/api/v1/escala-flex/context/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["impersonating"])
        self.assertEqual(response.data["profile"]["full_name"], "Agente Alvo")

    def test_clear_impersonation(self):
        self.client.force_authenticate(user=self.admin_user)
        self.client.post(
            "/api/v1/escala-flex/impersonate/",
            {"user_lan_id": "agent999"},
            format="json",
        )
        response = self.client.post("/api/v1/escala-flex/impersonate/clear/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["impersonating"])
        self.assertEqual(response.data["profile"]["lan_id"], "c91763a")

    def test_non_admin_cannot_impersonate(self):
        self.client.force_authenticate(user=self.regular_user)
        response = self.client.post(
            "/api/v1/escala-flex/impersonate/",
            {"user_lan_id": "agent999"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_impersonate_unknown_agent_returns_404(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/v1/escala-flex/impersonate/",
            {"user_lan_id": "inexistente"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_impersonate_agent_with_schedule_today(self):
        logged_out, _ = StatusType.objects.get_or_create(
            pk=3,
            defaults={"name": "Deslogado", "color": "#bdb2b0"},
        )
        ScheduleToday.objects.create(
            agent=self.target_agent,
            date=timezone.localdate(),
            status=logged_out,
            full_name=self.target_agent.full_name,
        )
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.post(
            "/api/v1/escala-flex/impersonate/",
            {"user_lan_id": "agent999"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["agent_status"]["status"], 3)
        self.assertIsNotNone(response.data["schedule_today_id"])
