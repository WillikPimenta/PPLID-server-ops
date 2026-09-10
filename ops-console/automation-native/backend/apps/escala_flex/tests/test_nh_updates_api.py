"""Testes da API de alteração de NH (nível hierárquico)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import AgentStatus, CurrentActivity, HierarchicalLevel, ScheduleToday, StatusType
from apps.workforce.models import Agent, UserProfile

User = get_user_model()


class NHUpdateApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        StatusType.objects.create(pk=1, name="Disponível", color="#109010", active=True)
        self.level = HierarchicalLevel.objects.create(
            name="BrScan - SLA 15 - Análise Visual",
            sharepoint_id=1200,
            active=True,
        )
        self.agent = Agent.objects.create(
            user_lan_id="agent01", full_name="Agente Um", active=True
        )
        self.user = User.objects.create_user(username="lider01", password="test12345")
        UserProfile.objects.create(user=self.user, agent=self.agent)
        self.client.force_authenticate(user=self.user)

        today = timezone.localdate()
        self.entry = ScheduleToday.objects.create(
            agent=self.agent,
            date=today,
            work_schedule="08:00 - 17:00",
            full_name=self.agent.full_name,
            job_activity="BrFlow",
            status_id=1,
        )
        AgentStatus.objects.create(agent=self.agent, status_id=1, current_activity="")

    def test_nh_update_sets_hierarchical_level(self):
        response = self.client.post(
            "/api/v1/escala-flex/nh-updates/",
            {
                "items": [
                    {
                        "user_lan_id": "agent01",
                        "hierarchical_level_id": str(self.level.pk),
                        "id_schedule": str(self.entry.id),
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.hierarchical_level_id, self.level.pk)
        self.assertEqual(self.entry.job_activity, "BrFlow")
        self.assertEqual(self.entry.current_activity, "")

        ca = CurrentActivity.objects.get(agent=self.agent)
        self.assertEqual(ca.hierarchical_level_id, self.level.pk)
        self.assertEqual(ca.current_activity, "")

        status_row = AgentStatus.objects.get(agent=self.agent)
        self.assertEqual(status_row.current_activity, "")

    def test_nh_update_batch(self):
        agent2 = Agent.objects.create(
            user_lan_id="agent02", full_name="Agente Dois", active=True
        )
        entry2 = ScheduleToday.objects.create(
            agent=agent2,
            date=timezone.localdate(),
            work_schedule="09:00 - 18:00",
            full_name=agent2.full_name,
            status_id=1,
        )
        response = self.client.post(
            "/api/v1/escala-flex/nh-updates/",
            {
                "items": [
                    {
                        "user_lan_id": "agent01",
                        "hierarchical_level_id": str(self.level.pk),
                        "id_schedule": str(self.entry.id),
                    },
                    {
                        "user_lan_id": "agent02",
                        "hierarchical_level_id": str(self.level.pk),
                        "id_schedule": str(entry2.id),
                    },
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 2)

        self.entry.refresh_from_db()
        entry2.refresh_from_db()
        self.assertEqual(self.entry.hierarchical_level_id, self.level.pk)
        self.assertEqual(entry2.hierarchical_level_id, self.level.pk)

    def test_nh_update_skips_blocked_agent(self):
        self.entry.blocked = True
        self.entry.save(update_fields=["blocked"])
        previous_level = self.entry.hierarchical_level_id

        response = self.client.post(
            "/api/v1/escala-flex/nh-updates/",
            {
                "items": [
                    {
                        "user_lan_id": "agent01",
                        "hierarchical_level_id": str(self.level.pk),
                        "id_schedule": str(self.entry.id),
                    }
                ]
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 0)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.hierarchical_level_id, previous_level)
        self.assertFalse(CurrentActivity.objects.filter(agent=self.agent).exists())
