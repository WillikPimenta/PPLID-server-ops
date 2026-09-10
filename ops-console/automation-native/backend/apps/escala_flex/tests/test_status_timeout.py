from datetime import datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import (
    AgentStatus,
    Escala,
    Schedule,
    ScheduleToday,
    StatusEvent,
    StatusType,
)
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.schedule_utils import schedule_end_datetime
from apps.escala_flex.services.status_timeout import run_status_timeouts
from apps.workforce.models import Agent, AgentHistory


class ScheduleEndDatetimeTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

    def test_day_shift_end_same_day(self):
        end_dt = schedule_end_datetime(self.today, "08:00 - 17:00")
        self.assertIsNotNone(end_dt)
        self.assertEqual(end_dt.date(), self.today)
        self.assertEqual(end_dt.hour, 17)
        self.assertEqual(end_dt.minute, 0)

    def test_night_shift_end_next_day(self):
        end_dt = schedule_end_datetime(self.today, "22:00 - 06:00")
        self.assertIsNotNone(end_dt)
        self.assertEqual(end_dt.date(), self.today + timedelta(days=1))
        self.assertEqual(end_dt.hour, 6)

    def test_previous_night_shift_end_today_morning(self):
        end_dt = schedule_end_datetime(
            self.today, "22:00 - 06:00", is_previous_night_shift=True
        )
        self.assertIsNotNone(end_dt)
        self.assertEqual(end_dt.date(), self.today)
        self.assertEqual(end_dt.hour, 6)

    def test_invalid_schedule_returns_none(self):
        self.assertIsNone(schedule_end_datetime(self.today, "FOLGA"))
        self.assertIsNone(schedule_end_datetime(self.today, ""))


class StatusTimeoutTests(TestCase):
    def setUp(self):
        StatusType.objects.update_or_create(
            pk=1, defaults={"name": "Disponível", "active": True, "logged_in": True}
        )
        StatusType.objects.update_or_create(
            pk=3, defaults={"name": "Deslogado", "active": True, "logged_in": False}
        )
        StatusType.objects.update_or_create(
            pk=4, defaults={"name": "Pausa", "active": True, "logged_in": True}
        )
        self.leader = Agent.objects.create(
            user_lan_id="lead01", full_name="Líder", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="agt01", full_name="Agente", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            start_date=timezone.localdate(),
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
        self.entry = ScheduleToday.objects.get(agent=self.agent)
        self.available = StatusType.objects.get(pk=1)
        self.logged_out = StatusType.objects.get(pk=3)

    def _aware_at(self, hour: int, minute: int = 0, day_offset: int = 0):
        d = self.today + timedelta(days=day_offset)
        naive = datetime.combine(
            d, datetime.min.time().replace(hour=hour, minute=minute)
        )
        return timezone.make_aware(naive)

    def _set_agent_logged_in_with_event(self, status_id=1):
        start = self._aware_at(8, 0)
        status = StatusType.objects.get(pk=status_id)
        self.entry.status = status
        self.entry.last_change = start
        self.entry.save()
        AgentStatus.objects.create(
            agent=self.agent,
            status=status,
            start_of_work=start,
            date_of_change=start,
        )
        StatusEvent.objects.create(
            agent=self.agent,
            leader=self.leader,
            status=status,
            start_date=start,
            active_event=True,
        )

    def test_closes_after_day_shift_end(self):
        self._set_agent_logged_in_with_event()
        now = self._aware_at(17, 5)
        result = run_status_timeouts(now=now)
        self.assertEqual(result["closed"], 1)
        self.assertIn("agt01", result["agents"])

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status_id, 3)

        agent_status = AgentStatus.objects.get(agent=self.agent)
        self.assertEqual(agent_status.status_id, 3)

        event = StatusEvent.objects.get(agent=self.agent, status=self.available)
        self.assertFalse(event.active_event)
        self.assertEqual(event.final_date, self._aware_at(17, 0))
        self.assertEqual(event.total_duration, 9 * 3600)
        self.assertTrue(
            StatusEvent.objects.filter(
                agent=self.agent, status=self.logged_out, active_event=True
            ).exists()
        )

    def test_no_action_before_day_shift_end(self):
        self._set_agent_logged_in_with_event()
        now = self._aware_at(16, 55)
        result = run_status_timeouts(now=now)
        self.assertEqual(result["closed"], 0)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status_id, 1)
        self.assertTrue(
            StatusEvent.objects.filter(agent=self.agent, active_event=True).exists()
        )

    def test_skips_already_logged_out(self):
        self.entry.status = self.logged_out
        self.entry.save()
        now = self._aware_at(18, 0)
        result = run_status_timeouts(now=now)
        self.assertEqual(result["closed"], 0)

    def test_skips_invalid_work_schedule(self):
        self.entry.work_schedule = "FOLGA"
        self.entry.status = self.available
        self.entry.save()
        now = self._aware_at(18, 0)
        result = run_status_timeouts(now=now)
        self.assertEqual(result["closed"], 0)

    def test_dry_run_does_not_persist(self):
        self._set_agent_logged_in_with_event()
        now = self._aware_at(17, 5)
        result = run_status_timeouts(now=now, dry_run=True)
        self.assertEqual(result["closed"], 1)
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status_id, 1)
        self.assertTrue(
            StatusEvent.objects.filter(agent=self.agent, active_event=True).exists()
        )

    def test_night_shift_previous_timeout(self):
        yesterday = self.today - timedelta(days=1)
        night_agent = Agent.objects.create(
            user_lan_id="night01", full_name="Noturno", active=True
        )
        Schedule.objects.create(
            agent=night_agent,
            date=yesterday,
            work_schedule="22:00-06:00",
            working_hour=Decimal("8"),
            work_day=True,
        )
        ScheduleTodayService.build_for_date(self.today)
        night_entry = ScheduleToday.objects.get(
            agent=night_agent, is_previous_night_shift=True
        )
        start = self._aware_at(22, 0, day_offset=-1)
        night_entry.status = self.available
        night_entry.save()
        AgentStatus.objects.create(
            agent=night_agent,
            status=self.available,
            start_of_work=start,
            date_of_change=start,
        )
        StatusEvent.objects.create(
            agent=night_agent,
            leader=None,
            status=self.available,
            start_date=start,
            active_event=True,
        )
        now = self._aware_at(6, 10)
        result = run_status_timeouts(now=now)
        self.assertEqual(result["closed"], 1)
        self.assertIn("night01", result["agents"])
        night_entry.refresh_from_db()
        self.assertEqual(night_entry.status_id, 3)
