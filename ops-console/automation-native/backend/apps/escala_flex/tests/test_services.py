from datetime import date, datetime, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import Escala, Schedule, ScheduleToday, StatusType
from apps.escala_flex.services import ScheduleTodayService, compute_all_kpis
from apps.escala_flex.services.kpis import compute_day_capacity, compute_now_capacity
from apps.escala_flex.services.schedule_utils import (
    resolve_escala_work_schedule,
    work_schedule_from_dia_escala,
)
from apps.workforce.models import Agent, AgentHistory


class ScheduleTodayServiceTests(TestCase):
    def setUp(self):
        StatusType.objects.create(pk=1, name="Disponível", active=True, logged_in=True)
        StatusType.objects.create(pk=3, name="Deslogado", active=True, logged_in=False)
        self.leader = Agent.objects.create(
            user_lan_id="lead01", full_name="Líder Teste", active=True
        )
        self.agent = Agent.objects.create(
            user_lan_id="agt01", full_name="Agente Teste", active=True
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
        Escala.objects.create(
            agent=self.agent,
            data=self.today,
            dia_escala="08:00 - 17:00",
            horario="08:00 - 17:00",
        )

    def test_work_schedule_from_dia_escala(self):
        self.assertEqual(work_schedule_from_dia_escala("14:00 - 20:00"), "14:00 - 20:00")
        self.assertEqual(work_schedule_from_dia_escala("FOLGA"), "")
        self.assertEqual(work_schedule_from_dia_escala("FERIAS"), "")

    def test_resolve_escala_work_schedule(self):
        self.assertEqual(
            resolve_escala_work_schedule("", "14:00 - 20:00"),
            "14:00 - 20:00",
        )
        self.assertEqual(resolve_escala_work_schedule("FOLGA", "08:00 - 14:00"), "")
        self.assertEqual(
            resolve_escala_work_schedule("16:00 - 22:00", "08:00 - 14:00"),
            "16:00 - 22:00",
        )

    def test_build_preserves_blocked_and_observation(self):
        ScheduleTodayService.build_for_date(self.today)
        entry = ScheduleToday.objects.get(agent=self.agent)
        entry.blocked = True
        entry.blocked_by = "Líder Teste"
        entry.blocked_description = "Ajuste pendente"
        entry.blocked_at = timezone.now()
        entry.observation = "Não alterar NH"
        entry.save()

        ScheduleTodayService.build_for_date(self.today)
        restored = ScheduleToday.objects.get(agent=self.agent)
        self.assertTrue(restored.blocked)
        self.assertEqual(restored.blocked_by, "Líder Teste")
        self.assertEqual(restored.blocked_description, "Ajuste pendente")
        self.assertIsNotNone(restored.blocked_at)
        self.assertEqual(restored.observation, "Não alterar NH")

    def test_build_sets_overtime_for_he_schedule(self):
        Escala.objects.filter(agent=self.agent, data=self.today).update(
            dia_escala="11:00 - 20:00",
            horario="11:00 - 20:00",
        )
        ScheduleTodayService.build_for_date(self.today)
        entry = ScheduleToday.objects.get(agent=self.agent)
        self.assertEqual(entry.work_schedule, "11:00 - 20:00")
        self.assertEqual(entry.working_hour, Decimal("9.0"))
        self.assertTrue(entry.overtime)

    def test_build_uses_horario_when_dia_escala_empty(self):
        Escala.objects.create(
            agent=Agent.objects.create(user_lan_id="agt03", full_name="Horário Padrão", active=True),
            data=self.today,
            dia_escala="",
            horario="09:00 - 15:00",
        )
        count = ScheduleTodayService.build_for_date(self.today)
        self.assertEqual(count, 2)
        entry = ScheduleToday.objects.get(full_name="Horário Padrão")
        self.assertEqual(entry.work_schedule, "09:00 - 15:00")

    def test_build_skips_absence_dia_escala(self):
        Escala.objects.create(
            agent=Agent.objects.create(user_lan_id="agt02", full_name="Folga", active=True),
            data=self.today,
            dia_escala="FOLGA",
            horario="08:00 - 17:00",
        )
        count = ScheduleTodayService.build_for_date(self.today)
        self.assertEqual(count, 1)

    def test_kpis_return_structure(self):
        ScheduleTodayService.build_for_date(self.today)
        noon = timezone.make_aware(
            datetime.combine(self.today, datetime.strptime("12:00", "%H:%M").time())
        )
        kpis = compute_all_kpis(reference_time=noon, target_date=self.today)
        self.assertIn("capacity_day", kpis)
        self.assertIn("capacity_now", kpis)
        self.assertEqual(kpis["capacity_day"]["scheduled"], 1)
        self.assertEqual(kpis["capacity_day"]["absent"], 1)
        slices = kpis["capacity_day"]["slices"]
        self.assertEqual(slices[0]["label"], "Escalados")
        self.assertEqual(slices[0]["count"], 1)
        self.assertTrue(slices[0]["legend_only"])
        self.assertEqual(slices[1]["label"], "Presentes")
        self.assertEqual(slices[1]["count"], 0)
        self.assertEqual(slices[1]["chart_count"], 0)
        self.assertEqual(slices[2]["label"], "Ausentes")
        self.assertEqual(slices[2]["count"], 1)
        self.assertTrue(slices[2]["info_only"])

    def test_day_capacity_uses_scheduled_total_in_legend(self):
        ScheduleTodayService.build_for_date(self.today)
        capacity = compute_day_capacity(target_date=self.today)
        self.assertEqual(capacity["scheduled"], 1)
        self.assertEqual(capacity["slices"][0]["count"], 1)
        self.assertEqual(capacity["slices"][1]["chart_count"], 0)
        self.assertEqual(capacity["slices"][2]["count"], 1)

    def test_kpis_absences_now_counts_missing_start_of_work(self):
        ScheduleTodayService.build_for_date(self.today)
        noon = timezone.make_aware(
            datetime.combine(self.today, datetime.strptime("12:00", "%H:%M").time())
        )
        kpis = compute_all_kpis(reference_time=noon, target_date=self.today)
        self.assertEqual(kpis["operation_counts"][0]["count"], 1)
        self.assertEqual(kpis["operation_counts"][1]["count"], 1)

        entry = ScheduleToday.objects.get(agent=self.agent)
        entry.start_of_work = timezone.make_aware(
            datetime.combine(self.today, datetime.strptime("08:00", "%H:%M").time())
        )
        entry.status_id = 1
        entry.save(update_fields=["start_of_work", "status_id"])

        kpis_after = compute_all_kpis(reference_time=noon, target_date=self.today)
        self.assertEqual(kpis_after["operation_counts"][0]["count"], 1)
        self.assertEqual(kpis_after["operation_counts"][1]["count"], 0)

    def test_sync_schedule_metrics_backfills_overtime(self):
        ScheduleTodayService.build_for_date(self.today)
        entry = ScheduleToday.objects.get(agent=self.agent)
        ScheduleToday.objects.filter(pk=entry.pk).update(overtime=False)
        updated = ScheduleTodayService.sync_schedule_metrics(self.today)
        self.assertGreaterEqual(updated, 1)
        entry.refresh_from_db()
        self.assertTrue(entry.overtime)

    def test_night_shift_from_yesterday(self):
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
        count = ScheduleTodayService.build_for_date(self.today)
        self.assertEqual(count, 2)
        night = ScheduleToday.objects.get(agent=night_agent, is_previous_night_shift=True)
        self.assertEqual(night.work_schedule, "22:00 - 06:00")