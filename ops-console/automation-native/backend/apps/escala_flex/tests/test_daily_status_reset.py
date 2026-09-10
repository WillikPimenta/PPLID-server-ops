from datetime import date, datetime, time

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import AgentStatus, Escala, ScheduleToday, StatusEvent, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.daily_status_reset import reset_daily_status
from apps.workforce.models import Agent, AgentHistory


class DailyStatusResetTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=1, name="Disponível", active=True, logged_in=True)
        StatusType.objects.create(pk=3, name="Deslogado", active=True, logged_in=False)
        StatusType.objects.create(pk=4, name="Intervalo", active=True, logged_in=True)
        self.leader = Agent.objects.create(user_lan_id="lead01", full_name="Líder", active=True)
        self.agent = Agent.objects.create(user_lan_id="agt01", full_name="Agente", active=True)
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title="Agente",
            job_activity="BrFlow",
            start_date=date.today(),
            active=True,
        )
        self.today = timezone.localdate()
        Escala.objects.create(
            agent=self.agent,
            data=self.today,
            dia_escala="08:00 - 17:00",
            horario="08:00 - 17:00",
        )
        ScheduleTodayService.build_for_date(self.today)
        self.entry = ScheduleToday.objects.get(agent=self.agent, date=self.today)
        self.entry.work_schedule = "08:00 - 17:00"
        self.entry.status_id = 4
        self.entry.start_of_work = timezone.make_aware(
            datetime.combine(self.today, time(8, 0))
        )
        self.entry.last_change = self.entry.start_of_work
        self.entry.save()

        AgentStatus.objects.create(
            agent=self.agent,
            status_id=4,
            start_of_work=self.entry.start_of_work,
            date_of_change=self.entry.start_of_work,
        )

        StatusEvent.objects.create(
            agent=self.agent,
            leader=self.leader,
            status_id=4,
            start_date=self.entry.start_of_work,
            active_event=True,
        )

    def test_reset_clears_today_status_and_events(self):
        result = reset_daily_status(self.today)
        self.entry.refresh_from_db()

        self.assertGreater(result["events_deleted"], 0)
        self.assertIsNone(self.entry.status_id)
        self.assertIsNone(self.entry.start_of_work)
        self.assertIsNone(self.entry.last_change)
        self.assertFalse(
            StatusEvent.objects.filter(
                agent=self.agent,
                start_date__date=self.today,
            ).exists()
        )
        agent_status = AgentStatus.objects.get(agent=self.agent)
        self.assertIsNone(agent_status.status_id)
        self.assertIsNone(agent_status.start_of_work)

    def test_reset_dry_run_does_not_change_data(self):
        reset_daily_status(self.today, dry_run=True)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status_id, 4)
        self.assertTrue(StatusEvent.objects.filter(agent=self.agent).exists())
