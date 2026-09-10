"""Testes da listagem do Painel por headcount (all_headcount=true)."""

from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import Escala, ScheduleToday, StatusEvent, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.panel_schedule import BACKOFFICE_JOB_TITLE, _dedupe_headcount_histories
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class PanelScheduleApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        StatusType.objects.create(pk=1, name="Disponível", color="#109010", active=True, logged_in=True)
        StatusType.objects.create(pk=3, name="Deslogado", color="#bdb2b0", active=True, logged_in=False)

        self.leader = Agent.objects.create(user_lan_id="lider01", full_name="Líder", active=True)
        self.backoffice = Agent.objects.create(user_lan_id="back01", full_name="Backoffice Um", active=True)
        self.other_role = Agent.objects.create(user_lan_id="sup01", full_name="Supervisor", active=True)
        self.inactive_backoffice = Agent.objects.create(
            user_lan_id="back02", full_name="Backoffice Inativo", active=True
        )
        self.no_scale = Agent.objects.create(user_lan_id="back03", full_name="Sem Escala", active=True)

        today = timezone.localdate()
        AgentHistory.objects.create(
            agent=self.backoffice,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title=BACKOFFICE_JOB_TITLE,
            start_date=today - timedelta(days=30),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.other_role,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title="Supervisor",
            start_date=today - timedelta(days=30),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.inactive_backoffice,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title=BACKOFFICE_JOB_TITLE,
            start_date=today - timedelta(days=60),
            final_date=today - timedelta(days=1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.no_scale,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title=BACKOFFICE_JOB_TITLE,
            start_date=today - timedelta(days=10),
            active=True,
        )

        Escala.objects.create(
            agent=self.backoffice,
            leader=self.leader,
            data=today,
            dia_escala="08:00 - 17:00",
            horario="08:00 - 17:00",
        )
        Escala.objects.create(
            agent=Agent.objects.create(user_lan_id="back04", full_name="Folga", active=True),
            leader=self.leader,
            data=today,
            dia_escala="FOLGA",
            horario="08:00 - 17:00",
        )
        AgentHistory.objects.create(
            agent=Agent.objects.get(user_lan_id="back04"),
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title=BACKOFFICE_JOB_TITLE,
            start_date=today - timedelta(days=5),
            active=True,
        )

        self.user = User.objects.create_user(username="lider01", password="test12345")
        UserProfile.objects.create(user=self.user, agent=self.leader)
        self.client.force_authenticate(user=self.user)
        self.today = today

    def _panel_results(self, **params):
        query = {"all_headcount": "true", "date": str(self.today), **params}
        response = self.client.get("/api/v1/escala-flex/schedule/today/", query)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["results"]

    def test_includes_backoffice_with_null_final_date(self):
        names = {row["full_name"] for row in self._panel_results()}
        self.assertIn("Backoffice Um", names)
        self.assertIn("Sem Escala", names)

    def test_excludes_other_job_title(self):
        names = {row["full_name"] for row in self._panel_results()}
        self.assertNotIn("Supervisor", names)

    def test_excludes_backoffice_with_final_date(self):
        names = {row["full_name"] for row in self._panel_results()}
        self.assertNotIn("Backoffice Inativo", names)

    def test_agent_without_escala_has_no_time_schedule(self):
        rows = {row["full_name"]: row for row in self._panel_results()}
        self.assertFalse(rows["Sem Escala"]["has_time_schedule"])
        self.assertEqual(rows["Sem Escala"]["schedule_display"], "Sem escala")

    def test_folga_visible_without_time_bar(self):
        rows = {row["full_name"]: row for row in self._panel_results()}
        folga = rows["Folga"]
        self.assertFalse(folga["has_time_schedule"])
        self.assertEqual(folga["schedule_display"], "FOLGA")

    def test_past_date_returns_status_events(self):
        past = self.today - timedelta(days=2)
        Escala.objects.create(
            agent=self.backoffice,
            leader=self.leader,
            data=past,
            dia_escala="08:00 - 17:00",
            horario="08:00 - 17:00",
        )
        ScheduleTodayService.build_for_date(past)
        start = timezone.make_aware(datetime.combine(past, datetime.strptime("08:00", "%H:%M").time()))
        end = timezone.make_aware(datetime.combine(past, datetime.strptime("10:00", "%H:%M").time()))
        StatusEvent.objects.create(
            agent=self.backoffice,
            leader=self.leader,
            status_id=1,
            start_date=start,
            final_date=end,
            active_event=False,
        )
        response = self.client.get(
            "/api/v1/escala-flex/schedule/today/",
            {"all_headcount": "true", "date": str(past)},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        events = payload["status_events_by_lan"].get("back01", [])
        self.assertEqual(len(events), 1)

    def test_future_date_uses_published_escala(self):
        future = self.today + timedelta(days=3)
        Escala.objects.create(
            agent=self.backoffice,
            leader=self.leader,
            data=future,
            dia_escala="09:00 - 18:00",
            horario="09:00 - 18:00",
        )
        response = self.client.get(
            "/api/v1/escala-flex/schedule/today/",
            {"all_headcount": "true", "date": str(future)},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["max_panel_date"], str(future))
        rows = {row["full_name"]: row for row in payload["results"]}
        self.assertEqual(rows["Backoffice Um"]["work_schedule"], "09:00 - 18:00")
        self.assertEqual(payload["status_events_by_lan"], {})

    def test_deduplicates_multiple_open_histories(self):
        today = self.today
        agent = Agent.objects.create(user_lan_id="back05", full_name="Duplicado", active=True)
        older = AgentHistory(
            agent=agent,
            leader=self.leader,
            job_title=BACKOFFICE_JOB_TITLE,
            start_date=today - timedelta(days=90),
            active=True,
        )
        newer = AgentHistory(
            agent=agent,
            leader=self.leader,
            job_title=BACKOFFICE_JOB_TITLE,
            start_date=today - timedelta(days=10),
            active=True,
        )
        deduped = _dedupe_headcount_histories([older, newer])
        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].start_date, newer.start_date)
