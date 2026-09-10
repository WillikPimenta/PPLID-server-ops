# -*- coding: utf-8 -*-
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.analytics import (
    CAUSE_AMBIENTE,
    CAUSE_EXECUCAO,
    CAUSE_INCONCLUSIVO,
    DAILY_THRESHOLD,
    HOURLY_THRESHOLD,
    META_SHIFT_SECONDS,
    SEVERITY_CRITICAL,
    _agent_count_normalized,
    _agent_period_pct,
    _agent_period_pct_count,
    _agent_productivity_breakdown_maps,
    _agent_rate_maps,
    _agent_tma_maps,
    _format_hms,
    _format_recovery_insight,
    _build_supervisao_location_insights,
    _build_supervisao_pareto,
    _iter_shift_groups,
    _location_impact_seconds,
    _location_net_impact_seconds,
    _leader_idle_logged_fields,
    _meta_sec_per_prot,
    _potential_balance_fields,
    _potential_protocols_at_meta_pace,
    _overall_avg_agent_count_normalized,
    _overall_avg_agent_count_sums,
    _rate_gap,
    _sec_per_prot,
    _severity,
    _shift_group_metrics,
    build_agent_detail,
    build_dashboard,
    build_evolucao,
    build_kpis,
    build_por_agente,
    build_por_equipe,
    build_por_hora,
    build_por_hora_page,
    build_supervisao,
    HEATMAP_CLOCK_HOURS,
    productivity_pct_count,
    productivity_pct_time,
    _monitor_time_fields,
)


class AgentDailyPctAggregationTests(TestCase):
    def setUp(self):
        from apps.produtividade.services.monitor_bridge import (
            clear_monitor_bridge_cache_for_tests,
        )

        clear_monitor_bridge_cache_for_tests()
        tz = timezone.get_current_timezone()
        self.day = datetime(2026, 6, 17, tzinfo=tz)

    def _record(
        self,
        matricula,
        hour,
        count,
        goal,
        seconds=100,
        etapa="Etapa A",
        team="Operacional Fraud Compliance",
        leader_name="",
        location="",
        journey_shift="",
    ):
        return ProductivityRecord.objects.create(
            matricula_norm=matricula,
            etapa=etapa,
            analysis_seconds=seconds,
            analysis_count=count,
            stage_goal=Decimal(goal),
            recorded_at=self.day.replace(hour=hour),
            agent_name=matricula,
            team=team,
            leader_name=leader_name,
            location=location,
            journey_shift=journey_shift,
        )

    def test_meta_sec_per_prot_1414(self):
        self.assertAlmostEqual(_meta_sec_per_prot(1414), 14.0, places=1)

    def test_ludimila_sec_per_prot_and_impact(self):
        agent_spp = _sec_per_prot(2148, 143)
        meta_spp = _meta_sec_per_prot(1523)
        self.assertAlmostEqual(agent_spp, 15.02, places=2)
        self.assertAlmostEqual(meta_spp, 13.0, places=1)
        metrics = _shift_group_metrics(143, 2148, 1523)
        self.assertAlmostEqual(metrics["gap_sec_per_prot"], 2.02, places=2)
        self.assertGreater(metrics["impact_time_seconds"], 280)
        self.assertAlmostEqual(metrics["productivity_pct"], 86.6, places=1)

    def test_count_sum_and_normalized_two_etapas(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 10, 143, 1523, seconds=2148, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        single = _shift_group_metrics(143, 2148, 1523)["productivity_pct_count"]
        self.assertAlmostEqual(_agent_period_pct_count(qs)["a1"], single * 2, places=1)
        self.assertAlmostEqual(_agent_count_normalized(qs)["a1"], single, places=1)

    def test_repeated_hxH_goal_uses_max_not_sum(self):
        self._record("a1", 9, 70, 1523, seconds=1000, etapa="Etapa A")
        self._record("a1", 10, 73, 1523, seconds=1148, etapa="Etapa A")
        groups = _iter_shift_groups(ProductivityRecord.objects.all())
        key = next(k for k in groups if k[0] == "a1")
        self.assertEqual(groups[key]["goal"], 1523)
        self.assertEqual(groups[key]["count"], 143)
        metrics = _shift_group_metrics(groups[key]["count"], groups[key]["seconds"], groups[key]["goal"])
        self.assertAlmostEqual(metrics["productivity_pct_count"], 9.39, places=1)

    def test_dashboard_attainment_uses_count_normalized(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 10, 143, 1523, seconds=2148, etapa="Etapa B")
        self._record("b1", 9, 50, 900, seconds=2148, etapa="Etapa A")
        qs = ProductivityRecord.objects.all()
        dashboard = build_dashboard(qs)
        attainment = dashboard["attainment"]
        self.assertLess(attainment["pct"], 100)
        self.assertGreater(attainment["pct_sum_avg"], attainment["pct"])
        self.assertAlmostEqual(attainment["pct"], _overall_avg_agent_count_normalized(qs), places=1)
        self.assertAlmostEqual(attainment["pct_sum_avg"], _overall_avg_agent_count_sums(qs), places=1)

    def test_taxa_sum_still_available_separately(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 10, 143, 1523, seconds=2148, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        taxa_sum = _agent_period_pct(qs)["a1"]
        self.assertGreater(taxa_sum, 100)

    def test_tma_charged_per_etapa_and_api_payload(self):
        self._record("a1", 9, 100, 900, seconds=2000, etapa="Etapa A")
        self._record("a1", 10, 100, 900, seconds=2000, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        sum_pct = _agent_period_pct_count(qs)["a1"]
        self.assertGreater(sum_pct, 20)
        tma = _agent_tma_maps(qs)["a1"]
        meta_spp = META_SHIFT_SECONDS / 900
        expected_charged = int(round(200 * meta_spp))
        self.assertEqual(tma["tma_actual_seconds"], 4000)
        self.assertEqual(tma["tma_charged_seconds"], expected_charged)
        self.assertAlmostEqual(
            tma["tma_ratio_pct"],
            round(expected_charged / 4000 * 100, 2),
            places=1,
        )
        rows, _summary = build_por_agente(qs)
        self.assertIn("tma_ratio_pct", rows[0])
        detail = build_agent_detail("a1", qs)
        self.assertIn("tma_charged_seconds", detail["summary"])

    def test_tma_uses_raw_stage_goal_not_ociosidade(self):
        from apps.monitor_eventos.models import MonitorEventoRecord

        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 30, 10, 0, 0, tzinfo=tz)
        ProductivityRecord.objects.create(
            matricula_norm="tma1",
            etapa="Etapa A",
            analysis_seconds=2000,
            analysis_count=100,
            stage_goal=Decimal("900"),
            recorded_at=day,
            agent_name="TMA Test",
        )
        MonitorEventoRecord.objects.create(
            data=day.date(),
            hora=10,
            matricula_usuario="tma1",
            data_evento=day,
            evento="Autenticação com sucesso",
            data_segundo_evento=day.replace(hour=11),
            segundo_evento="Logout",
        )
        qs = ProductivityRecord.objects.all()
        pure = _agent_tma_maps(qs, adjust_goal=False)["tma1"]
        adjusted = _agent_tma_maps(qs, adjust_goal=True)["tma1"]
        default = _agent_tma_maps(qs)["tma1"]
        self.assertEqual(default["tma_charged_seconds"], pure["tma_charged_seconds"])
        self.assertNotEqual(pure["tma_charged_seconds"], adjusted["tma_charged_seconds"])

    def test_build_kpis_below_goal_uses_volume_sum(self):
        self._record("a1", 9, 143, 1523, seconds=2148)
        self._record("b1", 9, 50, 900, seconds=2148)
        kpis = build_kpis(ProductivityRecord.objects.all())
        self.assertIsNotNone(kpis["avg_productivity_pct"])
        self.assertLess(kpis["avg_productivity_pct"], 100)
        self.assertEqual(kpis["agents_below_goal"], 2)

    def test_below_goal_uses_volume_sum_not_normalized(self):
        """Duas etapas ~50% cada: soma ≥ meta (barra ok), normalizado ~50% — não conta abaixo."""
        from apps.produtividade.services.analytics import build_supervisao

        self._record("multi", 9, 450, 900, seconds=2148, etapa="Etapa A", location="Loc A")
        self._record("multi", 10, 450, 900, seconds=2148, etapa="Etapa B", location="Loc A")
        self._record("low", 9, 50, 900, seconds=2148, etapa="Etapa A", location="Loc B")
        qs = ProductivityRecord.objects.all()
        sums = _agent_period_pct_count(qs)
        norms = _agent_count_normalized(qs)
        self.assertGreaterEqual(sums["multi"], DAILY_THRESHOLD)
        self.assertLess(norms["multi"], DAILY_THRESHOLD)
        self.assertLess(sums["low"], DAILY_THRESHOLD)

        kpis = build_kpis(qs)
        self.assertEqual(kpis["agents_below_goal"], 1)

        dashboard = build_dashboard(qs)
        self.assertEqual(dashboard["attainment"]["agents_below_count"], 1)
        priority = {r["matricula"]: r for r in dashboard["priority_agents"]}
        self.assertFalse(priority["multi"]["below_daily_threshold"])
        self.assertTrue(priority["low"]["below_daily_threshold"])

        supervisao = build_supervisao(qs)
        breakdown = {row["location"]: row for row in supervisao["location_breakdown"]}
        self.assertEqual(breakdown["Loc A"]["agents_below_count"], 0)
        self.assertEqual(breakdown["Loc B"]["agents_below_count"], 1)
        self.assertGreater(breakdown["Loc A"]["productivity_pct_sum"], 0)
        self.assertAlmostEqual(
            breakdown["Loc A"]["productivity_pct_avg"],
            breakdown["Loc A"]["productivity_pct_sum"],
            places=2,
        )
        self.assertLess(breakdown["Loc B"]["productivity_pct_avg"], DAILY_THRESHOLD)
        kpis = supervisao["location_kpis"]
        self.assertEqual(kpis["agents_monitored"], 2)
        self.assertEqual(kpis["agents_below_count"], 1)
        self.assertNotIn("best_location", kpis)
        self.assertIsNotNone(kpis.get("avg_realized_protocols"))
        self.assertIsNotNone(kpis.get("avg_potential_protocols"))
        priority_agents = build_dashboard(qs)["priority_agents"]
        for agent in priority_agents:
            self.assertIn("protocols_count", agent)
            self.assertIn("potential_protocols", agent)
            # Lento pode ter potencial < realizado (sem floor).
            self.assertIsNotNone(agent["potential_protocols"])
        self.assertTrue(supervisao["location_critical_agents"])
        self.assertTrue(supervisao["location_insights"])
        self.assertTrue(any(p.get("outlier") is not None for p in supervisao["location_scatter"]))
        self.assertIn("location_pareto", supervisao)
        self.assertIn("shift_breakdown", supervisao)
        self.assertNotIn("location_trend", supervisao)

    def test_build_por_equipe_averages_agents(self):
        self._record("a1", 9, 143, 1523, seconds=2148, team="Time X Fraud Compliance")
        self._record("a2", 9, 50, 900, seconds=2148, team="Time X Fraud Compliance")
        results = build_por_equipe(ProductivityRecord.objects.all(), group_by="team")
        team_x = next(row for row in results if row["group"] == "Time X Fraud Compliance")
        self.assertEqual(team_x["agents"], 2)
        self.assertEqual(team_x["below_threshold"], 2)

    def test_build_por_hora_averages_agent_hourly_sums(self):
        self._record("a1", 9, 10, 100)
        self._record("a2", 9, 20, 100)
        results = build_por_hora(ProductivityRecord.objects.all())
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["productivity_pct"], 15.0)

    def test_por_hora_heatmap_full_0h_axis(self):
        """Eixo fixo 0h→23h (24 colunas), mesmo sem produção em todas as horas."""
        self._record("low", 9, 30, 900, seconds=2148)
        self._record("low", 23, 5, 100, seconds=100)
        self._record("low", 2, 8, 100, seconds=100)
        page = build_por_hora_page(ProductivityRecord.objects.all())
        heatmap = page["hourly_heatmap"]
        expected = [f"{h:02d}:00" for h in HEATMAP_CLOCK_HOURS]
        self.assertEqual(heatmap["hours"], expected)
        self.assertEqual(len(heatmap["hours"]), 24)
        self.assertEqual(heatmap["hours"][0], "00:00")
        self.assertEqual(heatmap["hours"][-1], "23:00")
        self.assertTrue(heatmap["rows"])
        row = heatmap["rows"][0]
        self.assertEqual(len(row["values"]), 24)
        # Índices no eixo 0→23: hora = índice
        self.assertIsNotNone(row["values"][9])  # 09h
        self.assertIsNotNone(row["values"][23])  # 23h
        self.assertIsNotNone(row["values"][2])  # 02h
        self.assertIsNone(row["values"][6])  # 06h sem dado
        self.assertIsNone(row["values"][5])  # 05h sem dado

    def test_por_hora_heatmap_includes_all_agents_staircase(self):
        """Todos os agentes; escada pela 1ª hora com produção (mais cedo no topo)."""
        self._record("tarde", 14, 50, 100, seconds=100)
        self._record("cedo", 7, 40, 100, seconds=100)
        self._record("cedo", 10, 40, 100, seconds=100)
        self._record("meio", 10, 80, 100, seconds=100)
        page = build_por_hora_page(ProductivityRecord.objects.all())
        mats = [r["matricula"] for r in page["hourly_heatmap"]["rows"]]
        self.assertEqual(mats, ["cedo", "meio", "tarde"])
        self.assertEqual(len(mats), 3)
        for row in page["hourly_heatmap"]["rows"]:
            self.assertIn("total", row)
            self.assertIsInstance(row["total"], (int, float))
            expected_sum = round(sum(v for v in row["values"] if v is not None), 2)
            self.assertEqual(row["total"], expected_sum)

    def test_por_hora_logged_heatmap_from_monitor(self):
        """logged_heatmap: eixo 0→23, total = soma segundos, célula na hora logada."""
        from datetime import date
        from unittest.mock import patch

        mat = "logado1"
        self._record(mat, 9, 50, 100, seconds=100)
        fake_lookup = {
            (mat, date(2026, 6, 17), 9): 3600,
            (mat, date(2026, 6, 17), 10): 1800,
        }
        with patch(
            "apps.produtividade.services.analytics.build_monitor_hourly_logado_lookup",
            return_value=fake_lookup,
        ):
            page = build_por_hora_page(ProductivityRecord.objects.all())
        self.assertIn("logged_heatmap", page)
        self.assertIn("logged_overview", page)
        heatmap = page["logged_heatmap"]
        expected = [f"{h:02d}:00" for h in HEATMAP_CLOCK_HOURS]
        self.assertEqual(heatmap["hours"], expected)
        self.assertEqual(heatmap["hours"][0], "00:00")
        self.assertEqual(heatmap["hours"][-1], "23:00")
        row = next(r for r in heatmap["rows"] if r["matricula"] == mat)
        # Índices 0→23: 9→9, 10→10
        self.assertEqual(row["values"][9], 3600.0)
        self.assertEqual(row["values"][10], 1800.0)
        self.assertEqual(row["total"], 5400.0)
        overview = page["logged_overview"]
        self.assertEqual(len(overview), 2)
        self.assertIn("T09:00:00", overview[0]["hour"])
        self.assertEqual(overview[0]["logged_seconds_avg"], 3600.0)
        self.assertIn("T10:00:00", overview[1]["hour"])
        self.assertEqual(overview[1]["logged_seconds_avg"], 1800.0)

    def test_logged_overview_madrugada_hora_23_civil_brasilia(self):
        """23h da jornada fica no dia civil da jornada; 03h no dia seguinte (Brasília)."""
        from datetime import date
        from unittest.mock import patch

        from apps.produtividade.services.analytics import (
            _civil_datetime_from_jornada_hour,
            _logged_overview_from_detail,
        )

        jornada = date(2026, 7, 28)
        slot_23 = _civil_datetime_from_jornada_hour(jornada, 23)
        slot_03 = _civil_datetime_from_jornada_hour(jornada, 3)
        self.assertEqual(slot_23.hour, 23)
        self.assertEqual(slot_23.date(), jornada)
        self.assertEqual(slot_03.hour, 3)
        self.assertEqual(slot_03.date(), date(2026, 7, 29))

        detail = {
            ("mad1", jornada, 23): 3600.0,
            ("mad1", jornada, 3): 1800.0,
        }
        overview = _logged_overview_from_detail(detail)
        self.assertEqual(len(overview), 2)
        # Ordem civil: 28 23:00 depois 29 03:00
        self.assertIn("2026-07-28T23:00:00", overview[0]["hour"])
        self.assertIn("2026-07-29T03:00:00", overview[1]["hour"])
        self.assertEqual(overview[0]["logged_seconds_avg"], 3600.0)
        self.assertEqual(overview[1]["logged_seconds_avg"], 1800.0)

        mat = "mad1"
        self._record(mat, 3, 50, 100, seconds=100)  # civil 17/06 03:00 → jornada 16
        fake_lookup = {
            (mat, date(2026, 6, 16), 23): 3500,
            (mat, date(2026, 6, 16), 3): 2000,
        }
        with patch(
            "apps.produtividade.services.analytics.build_monitor_hourly_logado_lookup",
            return_value=fake_lookup,
        ):
            page = build_por_hora_page(ProductivityRecord.objects.all())
        hours = [r["hour"] for r in page["logged_overview"]]
        self.assertTrue(any("T23:00:00" in h for h in hours))
        self.assertTrue(any("T03:00:00" in h for h in hours))
        hm = page["logged_heatmap"]
        row = next(r for r in hm["rows"] if r["matricula"] == mat)
        # eixo 0→23: hora = índice
        self.assertEqual(row["values"][23], 3500.0)
        self.assertEqual(row["values"][3], 2000.0)

    def test_build_dashboard_threshold_alerts(self):
        self._record("low_daily", 9, 30, 900, seconds=2148)
        self._record("low_hour", 9, 90, 100)
        self._record("low_hour", 10, 15, 100)
        self._record("ok", 9, 90, 100)
        dashboard = build_dashboard(ProductivityRecord.objects.all())
        self.assertGreaterEqual(dashboard["agents_below_daily_count"], 1)
        self.assertIn("heatmap_insights", dashboard)
        self.assertNotIn("hourly_heatmap", dashboard)

    def test_severity(self):
        self.assertEqual(_severity(40), SEVERITY_CRITICAL)
        self.assertEqual(_severity(50), SEVERITY_CRITICAL)
        self.assertEqual(_severity(55), "high")
        self.assertEqual(_severity(60), "high")
        self.assertEqual(_severity(65), "medium")
        self.assertEqual(_severity(70), "medium")
        self.assertEqual(_severity(85), "low")
        self.assertEqual(_severity(90), "low")
        self.assertEqual(_severity(91), "expected")
        self.assertEqual(_severity(None), "unclassifiable")

    def test_rate_pct_differs_from_count_pct(self):
        self.assertEqual(productivity_pct_count(2693, Decimal("900"), 146), 16.22)
        rate_pct = productivity_pct_time(2693, Decimal("900"), 146)
        self.assertIsNotNone(rate_pct)
        self.assertNotAlmostEqual(rate_pct, 16.22, places=1)

    def test_rate_gap_global(self):
        ProductivityRecord.objects.create(
            matricula_norm="a1",
            etapa="E",
            analysis_seconds=2148,
            analysis_count=143,
            stage_goal=Decimal("1523"),
            recorded_at=self.day.replace(hour=14),
            agent_name="a1",
        )
        gap = _rate_gap(ProductivityRecord.objects.all())
        self.assertAlmostEqual(gap["agent_sec_per_prot"], 15.02, places=2)
        self.assertAlmostEqual(gap["meta_sec_per_prot"], 13.0, places=1)
        self.assertGreater(gap["impact_time_seconds"], 0)

    def test_agent_potential_fast_etapas_offset_slow(self):
        """Etapas rápidas e lentas: potencial = Σ (tempo ÷ meta) por etapa."""
        from apps.produtividade.services.analytics import _potential_protocols_at_meta_pace

        goal = 900
        # Rápida: 100 prot em 2000s vs meta 22 s/prot → potencial < count.
        self._record("fast", 9, 100, goal, seconds=2000, etapa="Etapa Rápida")
        # Lenta: 50 prot em 1300s → potencial > count.
        self._record("fast", 10, 50, goal, seconds=1300, etapa="Etapa Lenta")

        pot_fast = _potential_protocols_at_meta_pace(100, 2000, goal)
        pot_slow = _potential_protocols_at_meta_pace(50, 1300, goal)
        self.assertLess(pot_fast, 100)
        self.assertGreater(pot_slow, 50)

        rates = _agent_rate_maps(ProductivityRecord.objects.all(), adjust_goal=False)["fast"]
        self.assertEqual(rates["protocols_count"], 150)
        self.assertAlmostEqual(
            rates["potential_protocols"],
            pot_fast + pot_slow,
            places=4,
        )

    def test_potential_balance_fast_scenario(self):
        """Aceite: 5 prot · 70s · meta 18 s/prot → potencial = 70/18 ≈ 3,8889."""
        goal = 1100  # 19800 / 1100 = 18
        bal = _potential_balance_fields(5, 70, goal, META_SHIFT_SECONDS)
        self.assertAlmostEqual(bal["charged_seconds"], 90.0, places=2)
        self.assertAlmostEqual(bal["time_balance_seconds"], 20.0, places=2)
        self.assertAlmostEqual(bal["potential_protocols"], 3.8889, places=4)
        self.assertGreater(5, bal["potential_protocols"])

    def test_potential_balance_slow_scenario(self):
        """Lento: potencial > realizado (esperado no tempo > produzido)."""
        bal = _potential_balance_fields(5, 100, 1100, META_SHIFT_SECONDS)
        self.assertAlmostEqual(bal["charged_seconds"], 90.0, places=2)
        self.assertAlmostEqual(bal["time_balance_seconds"], -10.0, places=2)
        self.assertAlmostEqual(bal["potential_protocols"], 5.5556, places=4)
        self.assertLess(5, bal["potential_protocols"])

    def test_potential_balance_on_meta(self):
        bal = _potential_balance_fields(5, 90, 1100, META_SHIFT_SECONDS)
        self.assertEqual(bal["time_balance_seconds"], 0.0)
        self.assertAlmostEqual(bal["potential_protocols"], 5.0, places=4)

    def test_potential_balance_invalid_inputs(self):
        for count, seconds, goal in ((5, 70, 0), (5, 70, None), (5, 0, 0)):
            bal = _potential_balance_fields(count, seconds, float(goal or 0), META_SHIFT_SECONDS)
            self.assertIsNone(bal["potential_protocols"])
            self.assertIsNone(bal["charged_seconds"])
        # Tempo zero com meta válida → potencial zero (não negativo).
        zero = _potential_balance_fields(5, 0, 1100, META_SHIFT_SECONDS)
        self.assertEqual(zero["potential_protocols"], 0.0)
        # Count não entra na fórmula: count=0 ainda produz potencial pelo tempo.
        no_count = _potential_balance_fields(0, 70, 1100, META_SHIFT_SECONDS)
        self.assertAlmostEqual(no_count["potential_protocols"], 3.8889, places=4)

    def test_potential_multi_etapas_sums_balances(self):
        """Duas etapas com metas distintas: soma dos potenciais, sem média de metas."""
        # Meta A: 1100 → 18 s/prot; Meta B: 900 → 22 s/prot
        self._record("multi_pot", 9, 5, 1100, seconds=70, etapa="Etapa A")
        self._record("multi_pot", 10, 5, 900, seconds=90, etapa="Etapa B")
        pot_a = _potential_protocols_at_meta_pace(5, 70, 1100)
        pot_b = _potential_protocols_at_meta_pace(5, 90, 900)
        rates = _agent_rate_maps(ProductivityRecord.objects.all(), adjust_goal=False)["multi_pot"]
        self.assertAlmostEqual(rates["potential_protocols"], pot_a + pot_b, places=4)
        # Não usar tempo_total ÷ meta_média.
        self.assertNotAlmostEqual(
            rates["potential_protocols"],
            _potential_protocols_at_meta_pace(10, 160, 1000),
            places=2,
        )

    def test_potential_multi_hours_independent(self):
        self._record("hora_pot2", 9, 5, 1100, seconds=70, etapa="Etapa A")
        self._record("hora_pot2", 11, 5, 1100, seconds=100, etapa="Etapa A")
        detail = build_agent_detail("hora_pot2", ProductivityRecord.objects.all())
        self.assertEqual(len(detail["by_hour"]), 2)
        pots = [r["potential_protocols"] for r in detail["by_hour"]]
        self.assertAlmostEqual(pots[0] + pots[1], sum(pots), places=4)
        fast = next(r for r in detail["by_hour"] if r["count"] == 5 and r["total_seconds"] == 70)
        slow = next(r for r in detail["by_hour"] if r["total_seconds"] == 100)
        self.assertAlmostEqual(fast["potential_protocols"], 3.8889, places=4)
        self.assertAlmostEqual(fast["charged_seconds"], 90.0, places=2)
        self.assertAlmostEqual(fast["time_balance_seconds"], 20.0, places=2)
        self.assertAlmostEqual(slow["potential_protocols"], 5.5556, places=4)
        self.assertGreater(slow["potential_protocols"], slow["count"])
        # realizado − potencial = saldo ÷ meta
        self.assertAlmostEqual(
            fast["count"] - fast["potential_protocols"],
            fast["time_balance_seconds"] / 18.0,
            places=4,
        )

    def test_potential_filters_recalculate(self):
        self._record("filt_pot", 9, 5, 1100, seconds=70, etapa="Etapa A")
        self._record("filt_pot", 10, 5, 1100, seconds=100, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        filtered = build_agent_detail("filt_pot", qs, etapa="Etapa A")
        self.assertEqual(filtered["summary"]["total_protocols"], 5)
        hour = filtered["by_hour"][0]
        self.assertAlmostEqual(hour["potential_protocols"], 3.8889, places=4)
        self.assertAlmostEqual(
            hour["potential_protocols"],
            hour["total_seconds"] / 18.0,
            places=4,
        )

    def test_potential_elica_brsafe_hour_fixture(self):
        """Regressão sintética Elica: BrSafe 115·3414s ≈ 96, não 134."""
        # meta ≈ 35,55 s/prot → goal = 19800 / 35.55 ≈ 556.962
        goal = META_SHIFT_SECONDS / 35.55
        bal = _potential_balance_fields(115, 3414, goal, META_SHIFT_SECONDS)
        self.assertAlmostEqual(bal["potential_protocols"], 3414 / 35.55, places=2)
        self.assertLess(bal["potential_protocols"], 100)
        self.assertGreater(bal["potential_protocols"], 95)
        self.assertNotAlmostEqual(bal["potential_protocols"], 134, places=0)

        # Dia: 620 realizados · potencial esperado ≈ 509,4 (não 731).
        self._record("elica_pot", 6, 115, goal, seconds=3414, etapa="BrSafe")
        # Demais etapas: tempo restante para fechar ≈ 509,4 no total.
        # 509.4 - 96.03 ≈ 413.37 → seconds = 413.37 * 35.55 ≈ 14695
        other_seconds = int(round(413.37 * 35.55))
        self._record("elica_pot", 7, 505, goal, seconds=other_seconds, etapa="Outras")
        rates = _agent_rate_maps(ProductivityRecord.objects.filter(matricula_norm="elica_pot"), adjust_goal=False)[
            "elica_pot"
        ]
        self.assertEqual(rates["protocols_count"], 620)
        self.assertAlmostEqual(rates["potential_protocols"], 509.4, places=0)
        self.assertLess(rates["potential_protocols"], 520)
        self.assertGreater(rates["potential_protocols"], 500)
        self.assertNotAlmostEqual(rates["potential_protocols"], 731, places=0)

    def test_priority_agents_impact_in_seconds(self):
        self._record("crit", 9, 30, 900, seconds=2148)
        dashboard = build_dashboard(ProductivityRecord.objects.all())
        agent = dashboard["priority_agents"][0]
        self.assertIn("impact_time_seconds", agent)
        self.assertIn("productivity_pct_sum", agent)

    def test_build_dashboard_v2_fields(self):
        self._record("crit", 9, 30, 900, seconds=2148)
        dashboard = build_dashboard(ProductivityRecord.objects.all())
        self.assertIn("attainment", dashboard)
        self.assertIn("pct_sum_avg", dashboard["attainment"])
        self.assertIn("rate_gap", dashboard["attainment"])
        self.assertIn("agent_sec_per_prot", dashboard["attainment"]["rate_gap"])

    def test_build_dashboard_intraday_fields(self):
        self._record("crit", 9, 30, 900, seconds=2148)
        dashboard = build_dashboard(ProductivityRecord.objects.all())
        attainment = dashboard["attainment"]
        for key in (
            "pace_actual_pct",
            "pace_expected_pct",
            "atingimento_intraday_pct",
            "projected_closing_pct",
            "projection_insufficient",
            "cut_off_label",
            "agents_attention_count",
        ):
            self.assertIn(key, attainment)
        self.assertIsInstance(attainment["cut_off_label"], str)
        self.assertGreaterEqual(attainment["agents_attention_count"], 1)
        agent = dashboard["priority_agents"][0]
        self.assertIn("primary_cause", agent)
        self.assertIn("suggested_action", agent)
        self.assertIn("atingimento_intraday_pct", agent)

    def test_priority_agent_atingimento_matches_agent_detail(self):
        """Lista e modal devem expor o mesmo aproveitamento para o mesmo agente."""
        from apps.monitor_eventos.models import MonitorEventoRecord

        tz = timezone.get_current_timezone()
        day = self.day.date()
        self._record("crit", 9, 90, 900, seconds=600)
        self._record("crit", 10, 90, 900, seconds=600)
        for hour in (9, 10):
            start = datetime(day.year, day.month, day.day, hour, 0, 0, tzinfo=tz)
            end = start.replace(hour=hour + 1)
            MonitorEventoRecord.objects.create(
                data=day,
                hora=hour,
                matricula_usuario="crit",
                data_evento=start,
                evento="Autenticação com sucesso",
                data_segundo_evento=end,
                segundo_evento="Logout",
            )
        qs = ProductivityRecord.objects.filter(matricula_norm="crit")
        dashboard = build_dashboard(qs)
        agent = next(a for a in dashboard["priority_agents"] if a["matricula"] == "crit")
        detail = build_agent_detail("crit", qs)
        self.assertIn("atingimento_intraday_pct", agent)
        self.assertIn("atingimento_intraday_pct", detail["summary"])
        self.assertEqual(
            agent["atingimento_intraday_pct"],
            detail["summary"]["atingimento_intraday_pct"],
        )
        self.assertEqual(agent["pace_actual_pct"], detail["summary"]["pace_actual_pct"])
        self.assertEqual(agent["pace_expected_pct"], detail["summary"]["pace_expected_pct"])
        self.assertIsNotNone(agent["atingimento_intraday_pct"])

    def test_build_agent_detail_with_filters(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 11, 50, 900, seconds=1000, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        detail = build_agent_detail("a1", qs)
        self.assertIn("by_hour", detail)
        self.assertIn("filter_options", detail)
        self.assertEqual(len(detail["by_etapa"]), 2)

        filtered = build_agent_detail("a1", qs, etapa="Etapa A")
        self.assertEqual(filtered["filters_applied"]["etapa"], "Etapa A")
        self.assertEqual(filtered["summary"]["count"], 1)
        self.assertEqual(len(filtered["by_etapa"]), 1)

    def test_build_agent_detail_volume_sum_matches_dashboard(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 10, 143, 1523, seconds=2148, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        detail = build_agent_detail("a1", qs)
        expected_sum = _agent_period_pct_count(qs)["a1"]
        self.assertAlmostEqual(detail["summary"]["productivity_pct_sum"], expected_sum, places=1)
        self.assertAlmostEqual(detail["summary"]["productivity_pct_count"], expected_sum, places=1)
        self.assertGreater(detail["summary"]["productivity_pct_sum"], 15)

    def test_build_agent_detail_by_etapa_exposes_protocols_and_sum(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 10, 50, 900, seconds=1000, etapa="Etapa B")
        detail = build_agent_detail("a1", ProductivityRecord.objects.all())
        etapa_a = next(row for row in detail["by_etapa"] if row["etapa"] == "Etapa A")
        etapa_b = next(row for row in detail["by_etapa"] if row["etapa"] == "Etapa B")
        self.assertEqual(etapa_a["count"], 143)
        self.assertEqual(etapa_b["count"], 50)
        self.assertIsNotNone(etapa_a["productivity_pct_sum"])
        self.assertIsNotNone(etapa_b["productivity_pct_sum"])

    def test_build_agent_detail_by_hour_exposes_protocols(self):
        self._record("a1", 9, 10, 100, seconds=600)
        self._record("a1", 9, 20, 100, seconds=600, etapa="Etapa B")
        detail = build_agent_detail("a1", ProductivityRecord.objects.all())
        hour_row = detail["by_hour"][0]
        self.assertEqual(hour_row["count"], 30)
        self.assertIsNotNone(hour_row["productivity_pct_sum"])

    def test_build_agent_detail_filtered_tabs_match_summary(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 11, 50, 900, seconds=1000, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        filtered = build_agent_detail("a1", qs, etapa="Etapa A")
        self.assertEqual(filtered["summary"]["total_protocols"], 143)
        self.assertEqual(len(filtered["by_etapa"]), 1)
        self.assertEqual(filtered["by_etapa"][0]["etapa"], "Etapa A")
        self.assertEqual(len(filtered["records"]), 1)

    def test_build_agent_detail_ritmo_weighted_by_protocols(self):
        """Consolidado = soma(segundos) / soma(protocolos), não média simples das etapas."""
        # Etapa A: 100 prot · 5000 s → 50,00 s/prot
        self._record("ritmo1", 9, 100, 900, seconds=5000, etapa="Etapa A")
        # Etapa B: 10 prot · 1000 s → 100,00 s/prot
        self._record("ritmo1", 10, 10, 900, seconds=1000, etapa="Etapa B")
        detail = build_agent_detail("ritmo1", ProductivityRecord.objects.all())
        summary = detail["summary"]
        etapa_a = next(r for r in detail["by_etapa"] if r["etapa"] == "Etapa A")
        etapa_b = next(r for r in detail["by_etapa"] if r["etapa"] == "Etapa B")

        self.assertEqual(summary["total_protocols"], 110)
        self.assertEqual(summary["actual_seconds"], 6000)
        self.assertAlmostEqual(etapa_a["agent_sec_per_prot"], 50.0, places=2)
        self.assertAlmostEqual(etapa_b["agent_sec_per_prot"], 100.0, places=2)

        weighted = _sec_per_prot(6000, 110)
        simple_avg = round(
            (etapa_a["agent_sec_per_prot"] + etapa_b["agent_sec_per_prot"]) / 2, 2
        )
        self.assertAlmostEqual(summary["agent_sec_per_prot"], weighted, places=2)
        self.assertAlmostEqual(summary["agent_sec_per_prot"], 54.55, places=2)
        self.assertNotAlmostEqual(summary["agent_sec_per_prot"], simple_avg, places=2)

    def test_build_agent_detail_filter_recalculates_ritmo_summary(self):
        """detail_etapa/detail_hour: card e linhas usam o mesmo recorte do summary."""
        self._record("ritmo2", 9, 100, 900, seconds=5000, etapa="Etapa A")
        self._record("ritmo2", 11, 10, 900, seconds=1000, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        filtered = build_agent_detail("ritmo2", qs, etapa="Etapa A")
        self.assertEqual(filtered["filters_applied"]["etapa"], "Etapa A")
        self.assertEqual(len(filtered["by_etapa"]), 1)
        row = filtered["by_etapa"][0]
        self.assertEqual(filtered["summary"]["total_protocols"], row["count"])
        self.assertEqual(filtered["summary"]["actual_seconds"], row["actual_seconds"])
        self.assertAlmostEqual(
            filtered["summary"]["agent_sec_per_prot"],
            row["agent_sec_per_prot"],
            places=2,
        )
        self.assertAlmostEqual(filtered["summary"]["agent_sec_per_prot"], 50.0, places=2)

    def test_build_agent_detail_acceptance_sec_per_prot(self):
        """Exemplo de aceite: 214 prot · 7898 s → 36,91 s/prot (não prot/h como consolidado)."""
        self._record("ritmo3", 9, 214, 900, seconds=7898, etapa="Etapa A")
        detail = build_agent_detail("ritmo3", ProductivityRecord.objects.all())
        summary = detail["summary"]
        self.assertEqual(summary["total_protocols"], 214)
        self.assertEqual(summary["actual_seconds"], 7898)
        self.assertAlmostEqual(summary["agent_sec_per_prot"], 36.91, places=2)
        self.assertIsNotNone(summary.get("agent_pph"))
        # prot/h permanece disponível, mas o consolidado de ritmo é s/prot.
        self.assertNotEqual(summary["agent_sec_per_prot"], summary["agent_pph"])

    def test_build_agent_detail_by_etapa_sorted_by_impact(self):
        self._record("a1", 9, 30, 900, seconds=2148, etapa="Etapa Baixa")
        self._record("a1", 10, 143, 1523, seconds=2148, etapa="Etapa Alta")
        detail = build_agent_detail("a1", ProductivityRecord.objects.all())
        impacts = [row["impact_time_seconds"] for row in detail["by_etapa"]]
        self.assertEqual(impacts, sorted(impacts, reverse=True))

    def test_build_evolucao_rolling_and_compare(self):
        tz = timezone.get_current_timezone()
        for day_offset in range(5):
            d = datetime(2026, 6, 10 + day_offset, 9, tzinfo=tz)
            ProductivityRecord.objects.create(
                matricula_norm="a1",
                etapa="E",
                analysis_seconds=100,
                analysis_count=50 + day_offset * 5,
                stage_goal=Decimal(100),
                recorded_at=d,
                agent_name="a1",
                team="T",
            )
        evo = build_evolucao(ProductivityRecord.objects.all(), compare_days=2)
        self.assertEqual(len(evo["daily_series"]), 5)

    def test_build_agent_detail_cause_ambiente(self):
        for i in range(5):
            self._record(f"a{i}", 9, 50, 900, seconds=2500, etapa="Bio", team="Time X")
        detail = build_agent_detail("a0", ProductivityRecord.objects.all())
        row = detail["by_etapa"][0]
        self.assertEqual(row["cause"], CAUSE_AMBIENTE)
        self.assertGreater(detail["summary"]["impact_ambiente_est"], 0)

    def test_build_agent_detail_cause_execucao(self):
        for i in range(1, 5):
            self._record(f"a{i}", 9, 100, 900, seconds=900, etapa="Doc", team="Time Y")
        self._record("a0", 9, 100, 900, seconds=3500, etapa="Doc", team="Time Y")
        detail = build_agent_detail("a0", ProductivityRecord.objects.all())
        row = next(r for r in detail["by_etapa"] if r["etapa"] == "Doc")
        self.assertEqual(row["cause"], CAUSE_EXECUCAO)
        self.assertGreater(detail["summary"]["impact_execucao_est"], 0)

    def test_build_agent_detail_cause_inconclusivo_small_sample(self):
        self._record("a0", 9, 100, 900, seconds=3500, etapa="Rare", team="Time Z")
        self._record("a1", 9, 100, 900, seconds=900, etapa="Rare", team="Time Z")
        detail = build_agent_detail("a0", ProductivityRecord.objects.all())
        row = detail["by_etapa"][0]
        self.assertEqual(row["cause"], CAUSE_INCONCLUSIVO)

    def test_por_agente_exposes_sum_and_normalized(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 10, 143, 1523, seconds=2148, etapa="Etapa B")
        rows, summary = build_por_agente(ProductivityRecord.objects.all())
        row = next(r for r in rows if r["matricula"] == "a1")
        self.assertGreater(row["productivity_pct"], row["productivity_pct_normalized"])
        self.assertAlmostEqual(row["productivity_pct"], row["productivity_pct_sum"], places=1)
        self.assertIn("mean_pct_normalized", summary)

    def test_por_agente_percentile_and_sort_use_sum(self):
        self._record("low", 9, 50, 900, seconds=2148, etapa="Etapa A")
        self._record("high", 9, 450, 900, seconds=2148, etapa="Etapa A")
        self._record("high", 10, 100, 400, seconds=2148, etapa="Etapa B")
        rows, _summary = build_por_agente(ProductivityRecord.objects.all())
        by_mat = {row["matricula"]: row for row in rows}
        self.assertAlmostEqual(by_mat["high"]["productivity_pct_sum"], 75.0, places=1)
        self.assertAlmostEqual(by_mat["low"]["productivity_pct_sum"], 5.56, places=1)
        self.assertGreater(by_mat["high"]["percentile"], by_mat["low"]["percentile"])
        self.assertEqual(rows[0]["matricula"], "high")

    def test_por_agente_impact_matches_agent_detail_by_etapa_sum(self):
        self._record("a1", 9, 30, 900, seconds=2148, etapa="Etapa Baixa")
        self._record("a1", 10, 143, 1523, seconds=100, etapa="Etapa Alta")
        qs = ProductivityRecord.objects.all()
        rows, _summary = build_por_agente(qs)
        detail = build_agent_detail("a1", qs)
        expected_impact = sum(row.get("impact_time_seconds") or 0 for row in detail["by_etapa"])
        expected_surplus = sum(row.get("surplus_time_seconds") or 0 for row in detail["by_etapa"])
        expected_net = expected_impact - expected_surplus
        row = next(r for r in rows if r["matricula"] == "a1")
        self.assertEqual(row["impact_time_seconds"], expected_impact)
        self.assertEqual(row["surplus_time_seconds"], expected_surplus)
        self.assertEqual(row["net_impact_time_seconds"], expected_net)
        self.assertEqual(detail["summary"]["net_impact_time_seconds"], expected_net)
        self.assertGreater(expected_impact, 0)
        self.assertGreater(expected_surplus, 0)
        self.assertLess(expected_net, expected_impact)
        global_impact = _agent_rate_maps(qs)["a1"]["impact_time_seconds"]
        self.assertNotEqual(global_impact, expected_impact)

    def test_monitor_time_fields_aggregate_idle(self):
        """Jaqueline: logado 9993s, analisado 7023s → ocioso 2970s (00:49:30)."""
        fields = _monitor_time_fields("jaq", 7023, {"jaq": 9993})
        self.assertEqual(fields["tempo_ocioso_seconds"], 2970)
        self.assertEqual(fields["tempo_ocioso_hms"], "00:49:30")
        self.assertEqual(fields["tempo_logado_seconds"], 9993)
        self.assertEqual(fields["tempo_analisado_seconds"], 7023)

    def test_leader_name_in_por_agente_and_dashboard(self):
        self._record("a1", 9, 143, 1523, seconds=2148, leader_name="Maria Líder")
        qs = ProductivityRecord.objects.all()
        rows, _summary = build_por_agente(qs)
        row = next(r for r in rows if r["matricula"] == "a1")
        self.assertEqual(row["leader_name"], "Maria Líder")
        dashboard = build_dashboard(qs)
        priority = next(a for a in dashboard["priority_agents"] if a["matricula"] == "a1")
        self.assertEqual(priority["leader_name"], "Maria Líder")

    def test_build_agent_detail_by_hour_includes_forecast(self):
        self._record("a1", 9, 10, 100, seconds=600)
        detail = build_agent_detail("a1", ProductivityRecord.objects.all())
        hour_row = detail["by_hour"][0]
        self.assertIn("hourly_forecast_pct", hour_row)
        self.assertEqual(hour_row["hourly_forecast_pct"], HOURLY_THRESHOLD)

    def test_productivity_breakdown_abatement_equals_adjusted_minus_raw(self):
        self._record("a1", 9, 143, 1523, seconds=2148, etapa="Etapa A")
        self._record("a1", 10, 143, 1523, seconds=2148, etapa="Etapa B")
        qs = ProductivityRecord.objects.all()
        raw_map, adj_map, abatement_map, abatement_pcd, abatement_idle = (
            _agent_productivity_breakdown_maps(qs)
        )
        for matricula in adj_map:
            self.assertAlmostEqual(
                abatement_map.get(matricula, 0),
                max(0.0, adj_map[matricula] - raw_map.get(matricula, 0)),
                places=2,
            )
            self.assertAlmostEqual(
                abatement_map.get(matricula, 0),
                abatement_pcd.get(matricula, 0) + abatement_idle.get(matricula, 0),
                places=2,
            )
        dashboard = build_dashboard(qs)
        self.assertAlmostEqual(
            dashboard["attainment"]["pct_sum_avg"],
            _overall_avg_agent_count_sums(qs),
            places=1,
        )


class FormatRecoveryInsightTests(TestCase):
    def test_scales_minutes_hours_and_days(self):
        self.assertEqual(_format_recovery_insight(45), "45s")
        self.assertEqual(_format_recovery_insight(48 * 60), "48 min")
        self.assertEqual(_format_recovery_insight(2 * 3600 + 15 * 60), "2h 15min")
        self.assertEqual(_format_recovery_insight(3 * 3600), "3h")
        self.assertEqual(_format_recovery_insight(24 * 3600), "1d")
        self.assertEqual(_format_recovery_insight(2 * 86400 + 9 * 3600), "2d 9h")
        self.assertEqual(_format_recovery_insight(48 * 3600 + 9 * 60), "2d")


class SupervisaoParetoNetImpactTests(TestCase):
    def test_ranks_by_net_not_gross_recovery(self):
        """Bruto maior não vence se o líquido for menor (surplus nas etapas)."""
        agents = [
            {
                "matricula": "a",
                "nome": "Alto bruto",
                "below_daily_threshold": True,
                "impact_time_seconds": 1000,
                "surplus_time_seconds": 400,
                "net_impact_time_seconds": 600,
            },
            {
                "matricula": "b",
                "nome": "Maior líquido",
                "below_daily_threshold": True,
                "impact_time_seconds": 900,
                "surplus_time_seconds": 0,
                "net_impact_time_seconds": 900,
            },
        ]
        rows = _build_supervisao_pareto(agents)
        self.assertEqual([r["matricula"] for r in rows], ["b", "a"])
        self.assertEqual(rows[0]["net_impact_time_seconds"], 900)
        self.assertEqual(rows[1]["net_impact_time_seconds"], 600)
        # Compat: impact_time_seconds no payload do Pareto também é o líquido.
        self.assertEqual(rows[0]["impact_time_seconds"], 900)

    def test_location_net_keeps_signed_values_for_quadrant(self):
        ahead = {
            "impact_time_seconds": 100,
            "surplus_time_seconds": 400,
            "net_impact_time_seconds": -300,
        }
        behind = {"net_impact_time_seconds": 500}
        self.assertEqual(_location_net_impact_seconds(ahead), -300)
        self.assertEqual(_location_impact_seconds(ahead), 0)
        self.assertEqual(_location_net_impact_seconds(behind), 500)
        self.assertEqual(_location_impact_seconds(behind), 500)


class SupervisaoActionableInsightsTests(TestCase):
    def test_multi_location_priority_language(self):
        locs = [
            {
                "location": "São Carlos",
                "agents_count": 100,
                "agents_below_count": 86,
                "productivity_pct_avg": 72.3,
                "impact_time_seconds": 48 * 3600,
                "net_impact_time_seconds": 40 * 3600,
            },
            {
                "location": "Brasília",
                "agents_count": 80,
                "agents_below_count": 40,
                "productivity_pct_avg": 76.2,
                "impact_time_seconds": 10 * 3600,
                "net_impact_time_seconds": 8 * 3600,
            },
        ]
        shifts = [
            {
                "journey_shift": "Tarde",
                "agents_count": 90,
                "agents_below_count": 60,
                "productivity_pct_avg": 62.0,
                "impact_time_seconds": 20_000,
            }
        ]
        peak = {
            "label": "21:00",
            "avg_productivity_pct": 8.16,
            "agents_count": 81,
            "agents_below_count": 70,
        }
        insights = _build_supervisao_location_insights(
            locs,
            {"productivity_avg": 73.8, "agents_below_count": 126},
            [],
            peak_hour=peak,
            shift_breakdown=shifts,
        )
        self.assertTrue(insights)
        self.assertTrue(any(t.startswith("Prioridade 1: São Carlos") for t in insights))
        self.assertTrue(any("turno Tarde" in t for t in insights))
        self.assertTrue(any(t.startswith("Horário crítico: às 21:00") for t in insights))
        self.assertTrue(any("limiar horário" in t for t in insights))
        self.assertFalse(any("21 operadores" in t for t in insights))


class SupervisaoShiftBreakdownTests(TestCase):
    def setUp(self):
        tz = timezone.get_current_timezone()
        self.day = datetime(2026, 6, 17, tzinfo=tz)

    def _record(self, matricula, count, goal, journey_shift, seconds=100):
        return ProductivityRecord.objects.create(
            matricula_norm=matricula,
            etapa="Etapa A",
            analysis_seconds=seconds,
            analysis_count=count,
            stage_goal=Decimal(goal),
            recorded_at=self.day.replace(hour=9),
            agent_name=matricula,
            team="Operacional Fraud Compliance",
            location="Loc A",
            journey_shift=journey_shift,
        )

    def test_shift_breakdown_exposes_denominators_for_integral(self):
        # 2 Integral com produção quase nula; 1 Tarde com produção alta.
        self._record("i1", 1, 900, "Integral")
        self._record("i2", 2, 900, "Integral")
        self._record("t1", 800, 900, "Tarde", seconds=2148)

        supervisao = build_supervisao(ProductivityRecord.objects.all())
        by_shift = {r["journey_shift"]: r for r in supervisao["shift_breakdown"]}
        self.assertIn("Integral", by_shift)
        integral = by_shift["Integral"]
        self.assertEqual(integral["agents_count"], 2)
        self.assertEqual(integral["agents_below_count"], 2)
        self.assertEqual(integral["agents_below_pct"], 100.0)
        self.assertLess(integral["productivity_pct_avg"], 5)

        tarde = by_shift["Tarde"]
        self.assertEqual(tarde["agents_count"], 1)
        self.assertIn("agents_below_count", tarde)
        self.assertIn("agents_below_pct", tarde)


class LeaderIdleMedianTests(TestCase):
    def test_leader_idle_uses_median_not_sum(self):
        rows = [
            {"matricula": "a1", "tempo_ocioso_seconds": 1000},
            {"matricula": "a2", "tempo_ocioso_seconds": 3000},
            {"matricula": "a3", "tempo_ocioso_seconds": 5000},
        ]
        day = date(2026, 6, 17)
        logado_lookup = {
            ("a1", day): 7200,
            ("a2", day): 7200,
            ("a3", day): 7200,
        }
        fields = _leader_idle_logged_fields(rows, logado_lookup)
        self.assertEqual(fields["idle_seconds"], 3000)
        self.assertEqual(fields["idle_hms"], "00:50:00")
        self.assertEqual(fields["tempo_ocioso_seconds"], 9000)
        self.assertIsNotNone(fields["idle_pct"])

    def test_leader_idle_median_even_count(self):
        rows = [
            {"matricula": "a1", "tempo_ocioso_seconds": 1000},
            {"matricula": "a2", "tempo_ocioso_seconds": 3000},
        ]
        day = date(2026, 6, 17)
        logado_lookup = {("a1", day): 7200, ("a2", day): 7200}
        fields = _leader_idle_logged_fields(rows, logado_lookup)
        self.assertEqual(fields["idle_seconds"], 2000)
        self.assertEqual(fields["tempo_ocioso_seconds"], 4000)

    def test_leader_idle_null_without_agent_idle(self):
        rows = [{"matricula": "a1", "tempo_ocioso_seconds": None}]
        fields = _leader_idle_logged_fields(rows, {})
        self.assertIsNone(fields["idle_seconds"])
        self.assertIsNone(fields["idle_hms"])
