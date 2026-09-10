# -*- coding: utf-8 -*-
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.escala_flex.models import BreakTime, Schedule
from apps.monitor_eventos.models import MonitorEventoRecord
from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.hourly_forecast import HOURLY_THRESHOLD
from apps.produtividade.services.pace_tracking import (
    NEAR_PP,
    build_daily_actual_pct_map,
    build_pace_for_agent,
    build_pace_map_for_qs,
    classify_pace_delta,
    expected_pct_for_day,
)
from apps.produtividade.services.analytics import build_agent_detail, build_dashboard, build_por_agente
from apps.workforce.models import Agent


class PaceTrackingTests(TestCase):
    def setUp(self):
        self.tz = timezone.get_current_timezone()
        self.day = date(2026, 6, 18)
        self.agent = Agent.objects.create(full_name="Pace Agent", user_lan_id="pace01")

    def _login_hour(self, hour: int, duration_hours: float = 1.0):
        start = datetime(2026, 6, 18, hour, 0, 0, tzinfo=self.tz)
        end = start.replace(hour=hour + int(duration_hours))
        MonitorEventoRecord.objects.create(
            data=self.day,
            hora=hour,
            matricula_usuario="pace01",
            data_evento=start,
            evento="Autenticação com sucesso",
            data_segundo_evento=end,
            segundo_evento="Logout",
        )

    def _productivity(self, hour: int, count: int = 100, goal: str = "900"):
        ProductivityRecord.objects.create(
            matricula_norm="pace01",
            etapa="Etapa A",
            analysis_seconds=600,
            analysis_count=count,
            stage_goal=Decimal(goal),
            recorded_at=datetime(2026, 6, 18, hour, 0, 0, tzinfo=self.tz),
            agent_name="Pace Agent",
            team="Equipe A",
        )

    def test_classify_pace_delta(self):
        self.assertEqual(classify_pace_delta(0), "ok")
        self.assertEqual(classify_pace_delta(-NEAR_PP), "ok")
        self.assertEqual(classify_pace_delta(-NEAR_PP - 0.1), "near")
        self.assertEqual(classify_pace_delta(-8), "near")
        self.assertEqual(classify_pace_delta(-8.1), "behind")
        self.assertEqual(classify_pace_delta(None), "unknown")

    def test_expected_pct_one_full_hour(self):
        schedule_lookup = {}
        expected = expected_pct_for_day(
            "pace01",
            self.day,
            hourly_logado={9: 3600},
            daily_logado=3600,
            schedule_lookup=schedule_lookup,
        )
        self.assertEqual(expected, HOURLY_THRESHOLD)

    def test_expected_pct_overtime_unknown(self):
        Schedule.objects.create(agent=self.agent, date=self.day, overtime=True)
        schedule_lookup = {
            ("pace01", self.day): type("Ctx", (), {"overtime": True})(),
        }
        expected = expected_pct_for_day(
            "pace01",
            self.day,
            hourly_logado={9: 3600},
            daily_logado=3600,
            schedule_lookup=schedule_lookup,
        )
        self.assertIsNone(expected)

    def test_build_pace_map_with_monitor_and_productivity(self):
        self._login_hour(9)
        self._login_hour(10)
        self._productivity(9, count=90)
        self._productivity(10, count=90)
        qs = ProductivityRecord.objects.all()
        pace = build_pace_map_for_qs(qs)["pace01"]
        self.assertIsNotNone(pace["pace_actual_pct"])
        self.assertIsNotNone(pace["pace_expected_pct"])
        self.assertIn(pace["pace_status"], {"ok", "near", "behind"})
        self.assertGreaterEqual(pace["pace_days_count"], 1)

    def test_break_hour_lowers_expected(self):
        BreakTime.objects.create(agent_lan_id="pace01", week="12:00", active=True)
        Schedule.objects.create(
            agent=self.agent,
            date=self.day,
            work_schedule="08:00-17:00",
            overtime=False,
        )
        self._login_hour(12)
        self._productivity(12, count=50)
        qs = ProductivityRecord.objects.all()
        from apps.produtividade.services.hourly_forecast import build_schedule_context_lookup

        schedule_lookup = build_schedule_context_lookup({"pace01"}, {self.day})
        expected = expected_pct_for_day(
            "pace01",
            self.day,
            hourly_logado={12: 3600},
            daily_logado=3600,
            schedule_lookup=schedule_lookup,
        )
        self.assertLess(expected, HOURLY_THRESHOLD)

    def test_multi_day_average(self):
        day2 = date(2026, 6, 19)
        for hour in (9, 10):
            start = datetime(2026, 6, 18, hour, 0, 0, tzinfo=self.tz)
            MonitorEventoRecord.objects.create(
                data=self.day,
                hora=hour,
                matricula_usuario="pace01",
                data_evento=start,
                evento="Autenticação com sucesso",
                data_segundo_evento=start.replace(hour=hour + 1),
                segundo_evento="Logout",
            )
            start2 = datetime(2026, 6, 19, hour, 0, 0, tzinfo=self.tz)
            MonitorEventoRecord.objects.create(
                data=day2,
                hora=hour,
                matricula_usuario="pace01",
                data_evento=start2,
                evento="Autenticação com sucesso",
                data_segundo_evento=start2.replace(hour=hour + 1),
                segundo_evento="Logout",
            )
        self._productivity(9, count=90)
        self._productivity(10, count=90)
        ProductivityRecord.objects.create(
            matricula_norm="pace01",
            etapa="Etapa A",
            analysis_seconds=600,
            analysis_count=90,
            stage_goal=Decimal("900"),
            recorded_at=datetime(2026, 6, 19, 9, 0, 0, tzinfo=self.tz),
            agent_name="Pace Agent",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.all()
        pace = build_pace_for_agent("pace01", qs)
        self.assertGreaterEqual(pace["pace_days_count"], 2)

    def test_analytics_payload_includes_pace(self):
        self._login_hour(9)
        self._productivity(9, count=50)
        qs = ProductivityRecord.objects.all()
        dashboard = build_dashboard(qs)
        if dashboard["priority_agents"]:
            agent = dashboard["priority_agents"][0]
            self.assertIn("pace_status", agent)
        rows, _summary = build_por_agente(qs)
        self.assertIn("pace_status", rows[0])
        detail = build_agent_detail("pace01", qs)
        self.assertIn("pace_status", detail["summary"])
        self.assertIn("pace_by_day", detail["summary"])
        self.assertIn("projected_closing_pct", detail["summary"])
        attainment = dashboard["attainment"]
        self.assertIn("atingimento_intraday_pct", attainment)
        self.assertIn("cut_off_label", attainment)
        if dashboard["priority_agents"]:
            self.assertIn("primary_cause", dashboard["priority_agents"][0])

    def test_pace_actual_uses_daily_sum_not_average(self):
        """Multi-etapa: realizado = soma diária (volume), não média por etapa."""
        for etapa in ("A", "B", "C"):
            ProductivityRecord.objects.create(
                matricula_norm="pace01",
                etapa=f"Etapa {etapa}",
                analysis_seconds=600,
                analysis_count=70,
                stage_goal=Decimal("100"),
                recorded_at=datetime(2026, 6, 18, 9, 0, 0, tzinfo=self.tz),
                agent_name="Pace Agent",
                team="Equipe A",
            )
        self._login_hour(9)
        qs = ProductivityRecord.objects.all()
        from apps.produtividade.services.analytics import _agent_daily_pct_count_sums

        sum_daily = _agent_daily_pct_count_sums(qs)[("pace01", self.day)]
        daily_map = build_daily_actual_pct_map(qs)
        actual_daily = daily_map[("pace01", self.day)]
        self.assertGreater(sum_daily, 150)
        self.assertAlmostEqual(actual_daily, sum_daily, places=1)
        # Média por etapa seria ~sum/3 e distorce o comparativo com esperado do dia.
        self.assertGreater(actual_daily, sum_daily / 3 + 1)
        pace = build_pace_for_agent("pace01", qs)
        self.assertAlmostEqual(pace["pace_actual_pct"], sum_daily, places=1)
        self.assertGreater(pace["pace_actual_pct"], 150)

    def test_pace_actual_multi_etapa_near_volume_scale(self):
        """Regressão tipo print: soma >> média por etapa — pace usa a soma."""
        for i in range(40):
            ProductivityRecord.objects.create(
                matricula_norm="pace01",
                etapa=f"Etapa {i}",
                analysis_seconds=60,
                analysis_count=1,
                stage_goal=Decimal("100"),
                recorded_at=datetime(2026, 6, 18, 9, 0, 0, tzinfo=self.tz),
                agent_name="Pace Agent",
                team="Equipe A",
            )
        # 1h logada ≈ 1h analisada (40×60s) → ociosidade baixa; bruto ≈ 40%.
        self._login_hour(9)
        qs = ProductivityRecord.objects.all()
        from apps.produtividade.services.analytics import _agent_daily_pct_count_sums

        sum_daily = _agent_daily_pct_count_sums(qs)[("pace01", self.day)]
        pace = build_pace_for_agent("pace01", qs)
        avg_por_etapa = sum_daily / 40
        self.assertAlmostEqual(pace["pace_actual_pct"], sum_daily, places=1)
        self.assertGreater(sum_daily, avg_por_etapa * 10)
        self.assertGreater(pace["pace_actual_pct"], 20)
        self.assertIsNotNone(pace["pace_expected_pct"])
        # Comparativo legível: realizado e esperado na mesma ordem de grandeza.
        self.assertLess(abs(pace["pace_actual_pct"] - pace["pace_expected_pct"]), 100)
