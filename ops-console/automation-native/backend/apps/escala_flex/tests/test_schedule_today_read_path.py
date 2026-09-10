"""Regressão: GETs de schedule/today não podem chamar sync_schedule_metrics."""

from datetime import date
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import Escala, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class ScheduleTodayReadPathTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        StatusType.objects.create(
            pk=3, name="Deslogado", color="#bdb2b0", active=True, logged_in=False
        )
        self.leader = Agent.objects.create(
            user_lan_id="lider_rd", full_name="Líder RD", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="agent_rd", full_name="Agente RD", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title="Agente",
            start_date=date.today(),
            active=True,
        )
        self.user = User.objects.create_user(username="lider_rd", password="test12345")
        UserProfile.objects.create(user=self.user, agent=self.leader)
        self.client.force_authenticate(user=self.user)

        self.today = timezone.localdate()
        Escala.objects.create(
            agent=self.agent,
            leader=self.leader,
            data=self.today,
            dia_escala="08:00 - 17:00",
            horario="08:00 - 17:00",
        )
        ScheduleTodayService.build_for_date(self.today)

    @patch.object(ScheduleTodayService, "sync_schedule_metrics")
    def test_schedule_today_list_does_not_sync_metrics(self, sync_mock):
        response = self.client.get(
            "/api/v1/escala-flex/schedule/today/",
            {"search": "agent_rd", "page_size": 1, "date": str(self.today)},
        )
        self.assertEqual(response.status_code, 200)
        sync_mock.assert_not_called()

    @patch.object(ScheduleTodayService, "sync_schedule_metrics")
    def test_schedule_today_panel_does_not_sync_metrics(self, sync_mock):
        response = self.client.get(
            "/api/v1/escala-flex/schedule/today/",
            {"all_headcount": "true", "date": str(self.today)},
        )
        self.assertEqual(response.status_code, 200)
        sync_mock.assert_not_called()

    @patch.object(ScheduleTodayService, "sync_schedule_metrics")
    def test_operational_dashboard_does_not_sync_metrics(self, sync_mock):
        response = self.client.get(
            "/api/v1/escala-flex/monitoring/dashboard/",
            {"date": str(self.today)},
        )
        self.assertEqual(response.status_code, 200)
        sync_mock.assert_not_called()

    @patch.object(ScheduleTodayService, "sync_schedule_metrics")
    def test_activity_snapshot_does_not_sync_metrics(self, sync_mock):
        response = self.client.get(
            "/api/v1/escala-flex/monitoring/activity-snapshot/",
            {"date": str(self.today), "job_activity": "Operacional"},
        )
        self.assertEqual(response.status_code, 200)
        sync_mock.assert_not_called()
