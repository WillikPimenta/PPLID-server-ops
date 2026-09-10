# -*- coding: utf-8 -*-
"""Testes do relatório de validação e perfil Geovana."""
from datetime import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.validate_agent import (
    format_validation_report,
    validate_agent_metrics,
)


class ValidateAgentMetricsTests(TestCase):
    """Regressão do perfil Geovana (c14054q, 30/06/2026).

    Reconciliação em banco real (manage.py validate_produtividade_agent):
    - TMA 45,70% (cobrado/analisado) = 7865/17210
    - Impacto 02:36:18 = dashboard
    - Ritmo unknown = sem eventos monitor (monitor_events_exact_match: 0)
  - stage_goal 2200 -> meta 9 s/prot nas etapas de validação
    """
    def setUp(self):
        self.tz = timezone.get_current_timezone()
        self.day = datetime(2026, 6, 30, tzinfo=self.tz)
        self.mat = "c14054q"

    def _row(self, hour, etapa, count, seconds, goal="2200"):
        ProductivityRecord.objects.create(
            matricula_norm=self.mat,
            agent_name="Geovana Test",
            etapa=etapa,
            analysis_seconds=seconds,
            analysis_count=count,
            stage_goal=Decimal(goal),
            recorded_at=self.day.replace(hour=hour),
            team="Operacional/Fraud",
        )

    def test_geovana_profile_tma_impact_and_pace_unknown(self):
        """Perfil volume baixo + TMA alto + sem monitor (ritmo unknown)."""
        self._row(10, "Validacao A", 152, 4874)
        self._row(11, "Validacao B", 347, 4387)
        self._row(12, "Validacao C", 348, 7740)
        self._row(13, "Reclassificacao", 11, 209, goal="900")

        report = validate_agent_metrics(self.mat, day=self.day.date())
        totals = report["totals"]

        self.assertAlmostEqual(totals["tma_ratio_pct"], 45.70, places=0)
        self.assertEqual(totals["impact_time_seconds"], 9378)
        self.assertEqual(totals["tma_actual_seconds"], 17210)
        self.assertEqual(report["pace"]["pace_status"], "unknown")
        self.assertFalse(report["monitor_audit"]["linked"])
        self.assertFalse(report["stage_goal_audit"]["has_inconsistent_goals"])

        meta_etapas = [
            e for e in report["stage_goal_audit"]["per_etapa"] if e["goal_max"] == 2200.0
        ]
        self.assertEqual(len(meta_etapas), 3)
        self.assertAlmostEqual(meta_etapas[0]["meta_sec_per_prot_at_max"], 9.0, places=1)

    def test_format_report_contains_key_sections(self):
        self._row(9, "Etapa X", 50, 1000)
        report = validate_agent_metrics(self.mat, day=self.day.date())
        text = format_validation_report(report)
        self.assertIn("Totais", text)
        self.assertIn("Monitor", text)
        self.assertIn("Auditoria stage_goal", text)

    def test_stage_goal_audit_flags_inconsistent_goals(self):
        self._row(9, "Etapa X", 10, 200, goal="900")
        self._row(10, "Etapa X", 10, 200, goal="1000")
        report = validate_agent_metrics(self.mat, day=self.day.date())
        self.assertTrue(report["stage_goal_audit"]["has_inconsistent_goals"])

    def test_empty_agent_returns_error(self):
        report = validate_agent_metrics("inexistente", day=self.day.date())
        self.assertIn("error", report)

    def test_monitor_audit_idle_breakdown(self):
        """Relatório expõe divergência monitor horário vs ocioso líquido."""
        ProductivityRecord.objects.create(
            matricula_norm="idle1",
            agent_name="Idle Test",
            etapa="Etapa A",
            analysis_seconds=200,
            analysis_count=10,
            stage_goal=Decimal("900"),
            recorded_at=self.day.replace(hour=10),
            team="Equipe A",
        )
        ProductivityRecord.objects.create(
            matricula_norm="idle1",
            agent_name="Idle Test",
            etapa="Etapa B",
            analysis_seconds=1500,
            analysis_count=5,
            stage_goal=Decimal("900"),
            recorded_at=self.day.replace(hour=8),
            team="Equipe A",
        )
        MonitorEventoRecord.objects.create(
            data=self.day.date(),
            hora=10,
            matricula_usuario="idle1",
            data_evento=self.day.replace(hour=10),
            evento="Autenticação com sucesso",
            data_segundo_evento=self.day.replace(hour=11),
            segundo_evento="Logout",
        )
        report = validate_agent_metrics("idle1", day=self.day.date())
        mon = report["monitor_audit"]
        self.assertEqual(mon["display_idle_seconds"], 1900)
        self.assertGreater(mon["monitor_idle_sum_seconds"], mon["display_idle_seconds"])
        self.assertEqual(mon["net_idle_sum_seconds"], 1900)
        self.assertEqual(mon["abatement_idle_sum_seconds"], 1900)
        self.assertEqual(len(mon["idle_by_day"]), 1)
        text = format_validation_report(report)
        self.assertIn("display_idle_seconds", text)
        self.assertIn("idle_mismatch_seconds", text)
