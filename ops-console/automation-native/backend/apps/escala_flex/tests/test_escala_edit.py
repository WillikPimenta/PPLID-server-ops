from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import Escala, Schedule, ScheduleToday, StatusType
from apps.escala_flex.services import ScheduleTodayService
from apps.escala_flex.services.escala_edit import PublishedEscalaEditService
from apps.workforce.models import Agent, AgentHistory


class PublishedEscalaEditTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=3, name="Deslogado", active=True, logged_in=False)
        self.leader = Agent.objects.create(
            user_lan_id="lead01", full_name="Líder", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="agt01", full_name="Agente", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional BrFlow",
            job_title="Agente Backoffice I",
            job_activity="BrFlow",
            start_date=date.today(),
            active=True,
        )
        self.today = timezone.localdate()
        self.escala = Escala.objects.create(
            agent=self.agent,
            leader=self.leader,
            data=self.today,
            horario="08:00 - 17:00",
            dia_escala="08:00 - 17:00",
        )
        ScheduleTodayService.build_for_date(self.today)

    def test_change_work_schedule_updates_published_and_today(self):
        escala, schedule_today = PublishedEscalaEditService.apply_changes(
            self.escala,
            dia_escala="09:00 - 18:00",
        )
        self.assertEqual(escala.dia_escala, "09:00 - 18:00")
        schedule = Schedule.objects.get(agent=self.agent, date=self.today)
        self.assertEqual(schedule.work_schedule, "09:00 - 18:00")
        self.assertTrue(schedule.work_day)
        entry = ScheduleToday.objects.get(agent=self.agent, date=self.today)
        self.assertEqual(entry.work_schedule, "09:00 - 18:00")
        self.assertEqual(schedule.working_hour, Decimal("9.0"))
        self.assertEqual(entry.working_hour, Decimal("9.0"))
        self.assertTrue(schedule.overtime)
        self.assertTrue(entry.overtime)
        self.assertEqual(schedule_today.id, entry.id)

    def test_set_folga_removes_schedule_today_row(self):
        PublishedEscalaEditService.apply_changes(self.escala, dia_escala="FOLGA")
        self.escala.refresh_from_db()
        self.assertEqual(self.escala.dia_escala, "FOLGA")
        schedule = Schedule.objects.get(agent=self.agent, date=self.today)
        self.assertEqual(schedule.work_schedule, "")
        self.assertFalse(schedule.work_day)
        self.assertFalse(
            ScheduleToday.objects.filter(agent=self.agent, date=self.today).exists()
        )

    def test_overtime_auto_for_one_extra_hour(self):
        PublishedEscalaEditService.apply_changes(
            self.escala,
            dia_escala="14:00 - 22:00",
        )
        schedule = Schedule.objects.get(agent=self.agent, date=self.today)
        entry = ScheduleToday.objects.get(agent=self.agent, date=self.today)
        self.assertTrue(schedule.overtime)
        self.assertTrue(entry.overtime)
        self.assertEqual(schedule.working_hour, Decimal("8.0"))

    def test_no_overtime_for_standard_day_with_lunch(self):
        PublishedEscalaEditService.apply_changes(
            self.escala,
            dia_escala="14:00 - 21:00",
        )
        schedule = Schedule.objects.get(agent=self.agent, date=self.today)
        self.assertFalse(schedule.overtime)

    def test_accepts_reduced_night_shift_workload(self):
        PublishedEscalaEditService.apply_changes(
            self.escala,
            dia_escala="23:30 - 05:05",
        )
        schedule = Schedule.objects.get(agent=self.agent, date=self.today)
        entry = ScheduleToday.objects.get(agent=self.agent, date=self.today)
        self.assertEqual(schedule.work_schedule, "23:30 - 05:05")
        self.assertEqual(schedule.working_hour, Decimal("5.6"))
        self.assertFalse(schedule.overtime)
        self.assertEqual(entry.work_schedule, "23:30 - 05:05")
        self.assertEqual(entry.working_hour, Decimal("5.6"))

    def test_rejects_overtime_above_two_hours(self):
        with self.assertRaises(ValueError):
            PublishedEscalaEditService.apply_changes(
                self.escala,
                dia_escala="14:00 - 00:00",
            )

    def test_update_break_times_from_escala_edit(self):
        from apps.escala_flex.models import BreakTime

        escala, schedule_today = PublishedEscalaEditService.apply_changes(
            self.escala,
            week="13:00",
            weekend="12:30",
        )
        self.assertEqual(escala.dia_escala, "08:00 - 17:00")
        bt = BreakTime.objects.get(agent_lan_id="agt01", active=True)
        self.assertEqual(bt.week, "13:00")
        self.assertEqual(bt.weekend, "12:30")
        self.assertIsNotNone(schedule_today)
        schedule_today.refresh_from_db()
        self.assertEqual(schedule_today.week_break, "13:00")
        self.assertEqual(schedule_today.weekend_break, "12:30")
