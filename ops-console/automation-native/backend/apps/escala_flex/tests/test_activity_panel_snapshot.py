"""Testes do snapshot de atividade para aprovação de ocorrências."""

from datetime import datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import Escala, HierarchicalLevel, ScheduleToday, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.panel_schedule import BACKOFFICE_JOB_TITLE
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


class ActivityPanelSnapshotTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        StatusType.objects.create(pk=1, name="Disponível", color="#109010", active=True, logged_in=True)
        StatusType.objects.create(pk=3, name="Deslogado", color="#bdb2b0", active=True, logged_in=False)
        StatusType.objects.create(pk=4, name="Intervalo", color="#f59e0b", active=True, logged_in=True)

        self.today = timezone.localdate()
        self.leader = Agent.objects.create(user_lan_id="lider01", full_name="Líder", active=True)
        self.agent_online = Agent.objects.create(user_lan_id="agt01", full_name="Agente Online", active=True)
        self.agent_absent = Agent.objects.create(user_lan_id="agt02", full_name="Agente Ausente", active=True)

        for agent in (self.agent_online, self.agent_absent):
            AgentHistory.objects.create(
                agent=agent,
                leader=self.leader,
                location="Brasília",
                team="Operacional",
                job_title=BACKOFFICE_JOB_TITLE,
                job_activity="BrFlow",
                start_date=self.today - timedelta(days=30),
                active=True,
            )
            Escala.objects.create(
                agent=agent,
                leader=self.leader,
                data=self.today,
                dia_escala="08:00 - 17:00",
                horario="08:00 - 17:00",
            )

        self.nh_alpha = HierarchicalLevel.objects.create(name="NH Alpha", active=True)
        self.nh_beta = HierarchicalLevel.objects.create(name="NH Beta", active=True)

        user = User.objects.create_user(username="planejador", password="test12345")
        self.client.force_authenticate(user=user)

        ScheduleTodayService.build_for_date(self.today)
        ScheduleToday.objects.filter(agent=self.agent_online).update(
            job_activity="BrFlow",
            work_schedule="08:00 - 17:00",
            status_id=1,
            start_of_work=timezone.make_aware(datetime.combine(self.today, time(8, 0))),
            hierarchical_level=self.nh_alpha,
        )
        ScheduleToday.objects.filter(agent=self.agent_absent).update(
            job_activity="BrFlow",
            work_schedule="08:00 - 17:00",
            status_id=3,
            start_of_work=None,
            hierarchical_level=self.nh_beta,
        )

    def test_requires_job_activity(self):
        response = self.client.get("/api/v1/escala-flex/monitoring/activity-snapshot/")
        self.assertEqual(response.status_code, 400)

    def test_returns_summary_and_nh_groups(self):
        reference = timezone.make_aware(datetime.combine(self.today, time(10, 0)))
        with patch(
            "apps.escala_flex.services.activity_panel_snapshot.timezone.now",
            return_value=reference,
        ):
            response = self.client.get(
                "/api/v1/escala-flex/monitoring/activity-snapshot/",
                {"date": str(self.today), "job_activity": "BrFlow"},
            )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["job_activity"], "BrFlow")
        self.assertGreaterEqual(payload["summary"]["escalados"], 2)
        self.assertEqual(payload["summary"]["online"], 1)
        self.assertEqual(payload["summary"]["ausentes"], 1)
        nh_labels = {group["nh"] for group in payload["by_nh"]}
        self.assertIn("NH Alpha", nh_labels)
        self.assertIn("NH Beta", nh_labels)

    def test_filter_panel_rows_by_job_activity(self):
        from django.http import QueryDict

        from apps.escala_flex.services.panel_schedule import build_panel_rows, filter_panel_rows

        rows = build_panel_rows(self.today, profile=None)
        params = QueryDict("job_activity=BrFlow")
        filtered = filter_panel_rows(rows, params)
        self.assertTrue(filtered)
        self.assertTrue(all(row.get("job_activity") == "BrFlow" for row in filtered))
