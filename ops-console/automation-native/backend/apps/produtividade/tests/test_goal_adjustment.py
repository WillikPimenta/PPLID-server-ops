# -*- coding: utf-8 -*-
from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.produtividade.models import CASE_ETAPA, SOURCE_CASE, SOURCE_BRFLOW, ProductivityRecord
from apps.produtividade.services.analytics import (
    META_SHIFT_SECONDS,
    _agent_period_pct_count,
    _agent_productivity_breakdown_maps,
    _agent_rate_maps,
    _build_agent_by_etapa,
    _build_agent_by_hour,
    _group_pct_sums,
    _iter_shift_groups,
    _meta_sec_per_prot,
    build_agent_detail,
    productivity_pct_count,
    serialize_record,
)
from apps.produtividade.services.goal_adjustment import (
    META_CARGA_HORAS,
    META_JORNADA_SECONDS,
    adjust_stage_goal,
    adjust_stage_goal_for_ociosidade,
    adjust_stage_goal_full,
    build_analyzed_daily_lookup_for_qs,
    build_monitor_ociosidade_lookup_for_qs,
    build_net_ociosidade_lookup_for_qs,
    build_ociosidade_lookup_for_qs,
    build_productivity_discount_lookup_for_qs,
    jornada_base_seconds,
    ociosidade_for_goal_adjustment,
)
from apps.workforce.models import Agent, AgentHistory


def _seed_productivity_discount(
    matricula: str,
    on_date: date,
    hours: float | Decimal,
    *,
    pcd: bool = True,
    final_date: date | None = None,
) -> Agent:
    agent = Agent.objects.create(
        full_name=f"Agente {matricula}",
        user_lan_id=matricula,
        active=True,
    )
    AgentHistory.objects.create(
        agent=agent,
        start_date=on_date,
        final_date=final_date,
        active=True,
        pcd=pcd,
        productivity_discount=Decimal(str(hours)),
    )
    return agent


class GoalAdjustmentTests(TestCase):
    def test_adjust_stage_goal_sem_desconto(self):
        self.assertEqual(adjust_stage_goal(900, 0), 900)
        self.assertEqual(adjust_stage_goal(900, None), 900)

    def test_adjust_stage_goal_excel_exemplo(self):
        """1414 - ((1.0 / 5.5) * 1414) ≈ 1156.9091"""
        self.assertEqual(adjust_stage_goal(1414, 1.0), 1156.9091)
        self.assertEqual(float(META_CARGA_HORAS), 5.5)

    def test_adjust_stage_goal_metade_carga(self):
        half = float(META_CARGA_HORAS) / 2
        self.assertEqual(adjust_stage_goal(900, half), 450.0)

    def test_adjust_stage_goal_carga_completa(self):
        self.assertEqual(adjust_stage_goal(900, float(META_CARGA_HORAS)), 0.0)

    def test_adjust_stage_goal_for_ociosidade_1h(self):
        """meta_final = meta_pcd × (1 − 1h/5,5h)."""
        expected = round(900 * (1.0 - 3600 / META_JORNADA_SECONDS), 4)
        self.assertEqual(adjust_stage_goal_for_ociosidade(900, 3600), expected)

    def test_jornada_base_ate_meta_mantem_530(self):
        self.assertEqual(jornada_base_seconds(None), META_JORNADA_SECONDS)
        self.assertEqual(jornada_base_seconds(0), META_JORNADA_SECONDS)
        self.assertEqual(jornada_base_seconds(META_JORNADA_SECONDS), META_JORNADA_SECONDS)
        self.assertEqual(
            jornada_base_seconds(META_JORNADA_SECONDS - 1), META_JORNADA_SECONDS
        )

    def test_jornada_base_com_hora_extra_usa_logado(self):
        logado_he = META_JORNADA_SECONDS + 3600  # 06:30
        self.assertEqual(jornada_base_seconds(logado_he), logado_he)

    def test_adjust_stage_goal_for_ociosidade_com_hora_extra(self):
        """Com logado > 05:30, ociosidade é proporcional ao tempo logado."""
        idle = 3600
        logado_he = 25200  # 07:00
        expected = round(900 * (1.0 - idle / logado_he), 4)
        self.assertEqual(
            adjust_stage_goal_for_ociosidade(900, idle, logado_seconds=logado_he),
            expected,
        )
        # Sem HE (ou logado ≤ 05:30) continua na base fixa.
        expected_meta = round(900 * (1.0 - idle / META_JORNADA_SECONDS), 4)
        self.assertEqual(
            adjust_stage_goal_for_ociosidade(
                900, idle, logado_seconds=META_JORNADA_SECONDS
            ),
            expected_meta,
        )
        self.assertEqual(adjust_stage_goal_for_ociosidade(900, idle), expected_meta)

    def test_adjust_stage_goal_full_pcd_then_idle(self):
        """PCD primeiro, depois ociosidade sobre a meta já com PCD."""
        after_pcd = adjust_stage_goal(1414, 1.0)
        self.assertEqual(after_pcd, 1156.9091)
        expected = adjust_stage_goal_for_ociosidade(after_pcd, 3600)
        self.assertEqual(adjust_stage_goal_full(1414, 1.0, 3600), expected)
        self.assertLess(expected, after_pcd)

    def test_productivity_pct_count_usa_meta_ajustada(self):
        half = float(META_CARGA_HORAS) / 2
        adjusted = adjust_stage_goal(900, half)
        pct_raw = productivity_pct_count(450, 900, 450)
        pct_adj = productivity_pct_count(450, adjusted, 450)
        self.assertEqual(pct_raw, 50.0)
        self.assertEqual(pct_adj, 100.0)

    def test_iter_shift_groups_aplica_desconto_headcount(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 18, 10, 0, 0, tzinfo=tz)
        mat = "c92928a"
        _seed_productivity_discount(mat, day.date(), 2.75)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=100,
            analysis_count=450,
            stage_goal=Decimal("900"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.all()
        discount = build_productivity_discount_lookup_for_qs(qs)
        self.assertAlmostEqual(discount[(mat, day.date())], 2.75, places=2)
        adjusted = _iter_shift_groups(qs, adjust_goal=True)
        key = (mat, day.date(), "Etapa A")
        self.assertAlmostEqual(adjusted[key]["goal"], 450.0, places=2)
        pct_map = _agent_period_pct_count(qs)
        self.assertGreater(pct_map.get(mat, 0), 50)

    def test_productivity_breakdown_raw_plus_abatement_equals_adjusted(self):
        half = float(META_CARGA_HORAS) / 2
        adjusted = adjust_stage_goal(900, half)
        pct_raw = productivity_pct_count(450, 900, 450)
        pct_adj = productivity_pct_count(450, adjusted, 450)
        self.assertEqual(round(pct_raw + (pct_adj - pct_raw), 2), pct_adj)

    def test_meta_sec_per_prot_puro_nao_muda_com_desconto(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 19, 10, 0, 0, tzinfo=tz)
        mat = "agente1"
        _seed_productivity_discount(mat, day.date(), 2.75)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=1000,
            analysis_count=300,
            stage_goal=Decimal("900"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.all()
        pure = _agent_rate_maps(qs, adjust_goal=False)[mat]["meta_sec_per_prot"]
        adjusted = _agent_rate_maps(qs, adjust_goal=True)[mat]["meta_sec_per_prot"]
        self.assertAlmostEqual(pure, 22.0, places=1)
        self.assertGreater(adjusted, pure)

    def test_agent_breakdown_maps(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 20, 10, 0, 0, tzinfo=tz)
        mat = "agente2"
        _seed_productivity_discount(mat, day.date(), 2.75)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=100,
            analysis_count=450,
            stage_goal=Decimal("900"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.all()
        raw_map, adj_map, abatement_map, abatement_pcd, abatement_idle = (
            _agent_productivity_breakdown_maps(qs)
        )
        self.assertAlmostEqual(raw_map[mat], 50.0, places=1)
        self.assertGreater(adj_map[mat], raw_map[mat])
        self.assertAlmostEqual(
            abatement_map[mat],
            round(adj_map[mat] - raw_map[mat], 2),
            places=1,
        )
        self.assertAlmostEqual(
            abatement_map[mat],
            abatement_pcd[mat] + abatement_idle[mat],
            places=1,
        )
        self.assertGreater(abatement_pcd[mat], 0)
        raw_sums = _group_pct_sums(qs, "productivity_pct_count", adjust_goal=False)
        self.assertAlmostEqual(raw_sums[mat], raw_map[mat], places=1)

    def test_serialize_record_meta_pura_com_desconto(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 21, 15, 0, 0, tzinfo=tz)
        mat = "rec1"
        _seed_productivity_discount(mat, day.date(), 1.0)
        record = ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=1000,
            analysis_count=300,
            stage_goal=Decimal("1414"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.all()
        payload = serialize_record(
            record,
            build_ociosidade_lookup_for_qs(qs),
            build_productivity_discount_lookup_for_qs(qs),
        )
        self.assertEqual(payload["stage_goal_adjusted"], 1156.9091)
        self.assertEqual(payload["productivity_discount_hours"], 1.0)
        self.assertGreater(
            payload["productivity_pct_count_adjusted"],
            payload["productivity_pct_count"],
        )
        pure_meta = payload["meta_sec_per_prot"]
        pcd_meta = payload["meta_sec_per_prot_pcd"]
        self.assertIsNotNone(pure_meta)
        self.assertIsNotNone(pcd_meta)
        self.assertGreater(pcd_meta, pure_meta)
        pcd_goal = adjust_stage_goal(1414, 1.0)
        self.assertAlmostEqual(
            pcd_meta, _meta_sec_per_prot(pcd_goal, META_SHIFT_SECONDS), places=2
        )

    def test_build_agent_by_etapa_meta_pcd(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 21, 11, 0, 0, tzinfo=tz)
        mat = "etapa_pcd"
        _seed_productivity_discount(mat, day.date(), 1.0)
        # Ritmo lento vs meta (100 s/prot) para gerar impacto a recuperar.
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=30000,
            analysis_count=300,
            stage_goal=Decimal("1414"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        row = _build_agent_by_etapa(qs)[0]
        self.assertAlmostEqual(row["meta_sec_per_prot"], 14.0, places=1)
        self.assertGreater(row["meta_sec_per_prot_pcd"], row["meta_sec_per_prot"])
        # Sem ociosidade líquida, ajustado (PCD+idle) = PCD.
        self.assertAlmostEqual(
            row["meta_sec_per_prot_adjusted"],
            row["meta_sec_per_prot_pcd"],
            places=2,
        )
        # Meta PCD mais folgada → impacto a recuperar menor que o puro.
        self.assertGreater(row["impact_time_seconds"], 0)
        self.assertLess(row["impact_time_seconds_pcd"], row["impact_time_seconds"])
        self.assertLess(
            row["net_impact_time_seconds_pcd"], row["net_impact_time_seconds"]
        )

    def test_build_agent_detail_summary_exposes_meta_pcd_for_ritmo_card(self):
        """Summary do detalhe traz meta PCD para o card (mesma regra da tabela)."""
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 21, 14, 0, 0, tzinfo=tz)
        mat = "card_pcd"
        _seed_productivity_discount(mat, day.date(), 1.0)
        # Ritmo abaixo da meta PCD (menor s/prot) para o semáforo “Na meta”.
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=3000,
            analysis_count=214,
            stage_goal=Decimal("1414"),
            recorded_at=day,
            agent_name="Teste PCD",
            team="Equipe A",
        )
        detail = build_agent_detail(mat, ProductivityRecord.objects.filter(matricula_norm=mat))
        summary = detail["summary"]
        self.assertAlmostEqual(summary["agent_sec_per_prot"], 14.02, places=2)
        self.assertIsNotNone(summary["meta_sec_per_prot"])
        self.assertIsNotNone(summary["meta_sec_per_prot_pcd"])
        self.assertGreater(summary["meta_sec_per_prot_pcd"], summary["meta_sec_per_prot"])
        # Agente mais rápido que a meta PCD (menor s/prot).
        self.assertLessEqual(
            summary["agent_sec_per_prot"], summary["meta_sec_per_prot_pcd"]
        )

    def test_build_agent_detail_summary_meta_without_pcd_uses_pure(self):
        """Sem PCD, meta_sec_per_prot_pcd ausente/igual → card usa meta pura."""
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 21, 15, 0, 0, tzinfo=tz)
        mat = "card_nopcd"
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=7898,
            analysis_count=214,
            stage_goal=Decimal("1414"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        detail = build_agent_detail(mat, ProductivityRecord.objects.filter(matricula_norm=mat))
        summary = detail["summary"]
        self.assertIsNotNone(summary["meta_sec_per_prot"])
        # Sem desconto PCD, o campo PCD não deve afrouxar a meta pura.
        if summary.get("meta_sec_per_prot_pcd") is not None:
            self.assertAlmostEqual(
                summary["meta_sec_per_prot_pcd"],
                summary["meta_sec_per_prot"],
                places=2,
            )

    def test_build_agent_by_hour_dual_metrics(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 22, 9, 0, 0, tzinfo=tz)
        mat = "hora1"
        _seed_productivity_discount(mat, day.date(), 2.75)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=100,
            analysis_count=450,
            stage_goal=Decimal("900"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        rows = _build_agent_by_hour(qs)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["productivity_pct_count_raw"], 50.0, places=1)
        self.assertAlmostEqual(row["meta_sec_per_prot"], 22.0, places=1)
        self.assertIsNotNone(row.get("meta_sec_per_prot_adjusted"))
        self.assertIsNotNone(row.get("productivity_pct_abatement"))
        self.assertGreaterEqual(row["productivity_pct_abatement"], 0)

    def test_build_agent_by_hour_sums_pct_per_etapa_not_inflated_goal(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 22, 12, 0, 0, tzinfo=tz)
        for etapa, count in [("Etapa A", 450), ("Etapa B", 225)]:
            ProductivityRecord.objects.create(
                matricula_norm="hora2",
                etapa=etapa,
                analysis_seconds=100,
                analysis_count=count,
                stage_goal=Decimal("900"),
                recorded_at=day,
                agent_name="Teste",
                team="Equipe A",
            )
        qs = ProductivityRecord.objects.filter(matricula_norm="hora2")
        rows = _build_agent_by_hour(qs)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertAlmostEqual(row["productivity_pct_count_raw"], 75.0, places=1)
        # Volume (soma qntd/meta), não blend count/Σmeta (675/1800 ≈ 37,5%).
        self.assertAlmostEqual(row["productivity_pct_count"], 75.0, places=1)
        self.assertNotAlmostEqual(row["productivity_pct_count"], 675 / 1800 * 100, places=1)
        self.assertAlmostEqual(row["meta_sec_per_prot"], 22.0, places=1)

    def test_build_agent_by_hour_potential_sums_etapas_not_blend(self):
        """Regressão: potencial da hora não explode com Σ metas diárias no blend."""
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 22, 6, 0, 0, tzinfo=tz)
        for etapa, count, seconds in (
            ("Etapa A", 10, 76),
            ("Etapa B", 10, 295),
            ("Etapa C", 10, 120),
        ):
            ProductivityRecord.objects.create(
                matricula_norm="hora_pot",
                etapa=etapa,
                analysis_seconds=seconds,
                analysis_count=count,
                stage_goal=Decimal("900"),
                recorded_at=day,
                agent_name="Teste",
                team="Equipe A",
            )
        rows = _build_agent_by_hour(ProductivityRecord.objects.filter(matricula_norm="hora_pot"))
        row = rows[0]
        etapa_sum = sum(float(e["potential_protocols"] or 0) for e in row["etapas"])
        self.assertAlmostEqual(row["potential_protocols"], etapa_sum, places=4)
        self.assertEqual(row["count"], 30)
        self.assertLess(row["potential_protocols"], 500)
        # Não vaza impact_protocols do blend multi-meta (~milhares).
        self.assertNotIn("impact_protocols", row)

    def test_build_agent_by_hour_volume_sum_matches_period(self):
        """Soma das % por hora deve fechar com a produção volume do período."""
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 22, 0, 0, 0, tzinfo=tz)
        for hour, etapa, count in (
            (6, "Etapa A", 450),
            (7, "Etapa A", 225),
            (8, "Etapa B", 180),
        ):
            ProductivityRecord.objects.create(
                matricula_norm="hora_vol",
                etapa=etapa,
                analysis_seconds=100,
                analysis_count=count,
                stage_goal=Decimal("900"),
                recorded_at=day.replace(hour=hour),
                agent_name="Teste",
                team="Equipe A",
            )
        qs = ProductivityRecord.objects.filter(matricula_norm="hora_vol")
        hour_sum = round(sum(float(r["productivity_pct_count"] or 0) for r in _build_agent_by_hour(qs)), 2)
        period = _agent_period_pct_count(qs).get("hora_vol")
        self.assertIsNotNone(period)
        self.assertAlmostEqual(hour_sum, period, places=1)

    def test_build_agent_by_hour_etapas_and_potential(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 22, 12, 0, 0, tzinfo=tz)
        for etapa, count in [("Etapa A", 450), ("Etapa B", 225)]:
            ProductivityRecord.objects.create(
                matricula_norm="hora3",
                etapa=etapa,
                analysis_seconds=2148,
                analysis_count=count,
                stage_goal=Decimal("900"),
                recorded_at=day,
                agent_name="Teste",
                team="Equipe A",
            )
        qs = ProductivityRecord.objects.filter(matricula_norm="hora3")
        rows = _build_agent_by_hour(qs)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["count"], 675)
        self.assertAlmostEqual(row["stage_goal_raw_total"], 1800.0, places=1)
        self.assertIn("etapas", row)
        self.assertEqual(len(row["etapas"]), 2)
        self.assertEqual(row["etapas"][0]["etapa"], "Etapa A")
        self.assertEqual(row["etapas"][0]["count"], 450)
        self.assertEqual(row["etapas"][0]["total_seconds"], 2148)
        # Rápido vs meta 22 s/prot: potencial = 2148/22 ≈ 97,6 < realizado 450.
        self.assertLess(row["etapas"][0]["potential_protocols"], row["etapas"][0]["count"])
        self.assertIsNotNone(row["etapas"][0]["agent_sec_per_prot"])
        self.assertIsNotNone(row["etapas"][0]["actual_hms"])
        self.assertIsNotNone(row["etapas"][0].get("charged_seconds"))
        self.assertLess(row["potential_protocols"], row["count"])

    def test_build_agent_by_hour_etapa_potential_not_shift_goal(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 22, 10, 0, 0, tzinfo=tz)
        ProductivityRecord.objects.create(
            matricula_norm="hora4",
            etapa="Etapa lenta",
            analysis_seconds=300,
            analysis_count=2,
            stage_goal=Decimal("900"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.filter(matricula_norm="hora4")
        rows = _build_agent_by_hour(qs)
        self.assertEqual(len(rows), 1)
        etapa = rows[0]["etapas"][0]
        self.assertEqual(etapa["count"], 2)
        self.assertLess(etapa["potential_protocols"], 900)
        # Lento (300s para 2 prot vs meta 22): potencial = 300/22 ≈ 13,6 > realizado.
        self.assertGreater(etapa["potential_protocols"], etapa["count"])
        self.assertIsNotNone(etapa.get("time_balance_seconds"))
        self.assertLess(etapa["time_balance_seconds"], 0)

    def test_build_agent_by_hour_potential_uses_pcd_meta(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 22, 11, 0, 0, tzinfo=tz)
        mat = "hora_pcd_pot"
        _seed_productivity_discount(mat, day.date(), 1.0)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=300,
            analysis_count=2,
            stage_goal=Decimal("900"),
            recorded_at=day,
            agent_name="Teste",
            team="Equipe A",
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        row = _build_agent_by_hour(qs)[0]
        etapa = row["etapas"][0]
        self.assertIn("potential_protocols_pure", etapa)
        # Meta PCD mais folgada (s/prot maior) → potencial PCD <= puro.
        self.assertLessEqual(etapa["potential_protocols"], etapa["potential_protocols_pure"])
        self.assertLessEqual(row["potential_protocols"], row["potential_protocols_pure"])
        self.assertGreaterEqual(etapa["potential_protocols"], 0)
        self.assertGreaterEqual(etapa["potential_protocols_pure"], 0)

    def test_agent_potential_display_uses_pcd_not_pure_negative(self):
        """PcD folga a meta: potencial principal usa PCD e nunca fica negativo."""
        from apps.produtividade.services.analytics import (
            _agent_count_normalized,
            _agent_etapa_impact_detail,
            _agent_metadata_maps,
            _agent_monitor_logado_totals,
            _agent_period_pct_count,
            _agent_rate_maps,
            _build_priority_agents,
            _last_activity_map,
            _potential_display_fields,
            build_agent_detail,
        )

        tz = timezone.get_current_timezone()
        day = datetime(2026, 8, 4, 10, 0, 0, tzinfo=tz)
        mat = "arn_pcd"
        _seed_productivity_discount(mat, day.date(), 3.0)
        # 214 prot · 7898 s · meta 18 s/prot → esperado puro ≈ 438,8.
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=7898,
            analysis_count=214,
            stage_goal=Decimal("1100"),
            recorded_at=day,
            agent_name="Arnaldo",
            team="Equipe A",
            leader_name="Lider A",
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        pure = _agent_rate_maps(qs, adjust_goal=False)[mat]
        pcd = _agent_rate_maps(qs, adjust_goal=True, pcd_only=True)[mat]
        self.assertGreater(pure["potential_protocols"], 0)
        self.assertLessEqual(pcd["potential_protocols"], pure["potential_protocols"])
        self.assertAlmostEqual(pure["potential_protocols"], 7898 / 18.0, places=2)
        display = _potential_display_fields(pure, pcd)
        self.assertAlmostEqual(
            display["potential_protocols"], pcd["potential_protocols"], places=4
        )
        self.assertAlmostEqual(
            display["potential_protocols_pure"], pure["potential_protocols"], places=4
        )

        detail = build_agent_detail(mat, qs)
        self.assertAlmostEqual(
            detail["summary"]["potential_protocols"],
            pcd["potential_protocols"],
            places=4,
        )
        self.assertGreater(detail["summary"]["potential_protocols_pure"], 0)

        names, teams, leaders = _agent_metadata_maps(qs)
        impact, top_etapa, surplus = _agent_etapa_impact_detail(qs)
        agents = _build_priority_agents(
            qs,
            _agent_count_normalized(qs),
            _agent_period_pct_count(qs),
            names,
            teams,
            leaders,
            _last_activity_map(qs),
            _agent_rate_maps(qs, adjust_goal=False),
            impact,
            _agent_monitor_logado_totals(qs),
            top_n=None,
            top_etapa_map=top_etapa,
            surplus_totals=surplus,
            rate_map_pcd=_agent_rate_maps(qs, adjust_goal=True, pcd_only=True),
        )
        row = next(a for a in agents if a["matricula"] == mat)
        self.assertEqual(row["protocols_count"], 214)
        self.assertGreater(row["potential_protocols"], 0)
        self.assertAlmostEqual(row["potential_protocols"], pcd["potential_protocols"], places=4)
        # Com PCD (meta ~39,6 s/prot): potencial ≈ 199 < 214 (adiantado na meta PCD).
        self.assertLess(row["potential_protocols"], row["protocols_count"])
        self.assertGreaterEqual(row["potential_protocols"], 0)

    def test_net_ociosidade_ajusta_meta_apos_pcd(self):
        """Ociosidade líquida reduz a meta (após PCD) na proporção da carga 5h30."""
        tz = timezone.get_current_timezone()
        day = datetime(2026, 6, 30, 0, 0, 0, tzinfo=tz)
        mat = "ariane1"
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=200,
            analysis_count=100,
            stage_goal=Decimal("900"),
            recorded_at=day.replace(hour=10),
            agent_name="Ariane",
            team="Equipe A",
        )
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa B",
            analysis_seconds=1500,
            analysis_count=50,
            stage_goal=Decimal("900"),
            recorded_at=day.replace(hour=8),
            agent_name="Ariane",
            team="Equipe A",
        )
        MonitorEventoRecord.objects.create(
            data=day.date(),
            hora=10,
            matricula_usuario=mat,
            data_evento=day.replace(hour=10),
            evento="Autenticação com sucesso",
            data_segundo_evento=day.replace(hour=11),
            segundo_evento="Logout",
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        jornada = day.date()
        monitor_idle = build_monitor_ociosidade_lookup_for_qs(qs)[(mat, jornada)]
        net_idle = build_net_ociosidade_lookup_for_qs(qs)[(mat, jornada)]
        self.assertGreater(monitor_idle, net_idle)
        self.assertEqual(net_idle, 1900)
        self.assertEqual(build_ociosidade_lookup_for_qs(qs)[(mat, jornada)], net_idle)

        expected_goal = adjust_stage_goal_for_ociosidade(900.0, net_idle)
        adjusted = _iter_shift_groups(qs, adjust_goal=True)
        for key, bucket in adjusted.items():
            if key[0] == mat:
                self.assertAlmostEqual(bucket["goal"], expected_goal, places=2)
                self.assertLess(bucket["goal"], 900.0)

        raw_map, adj_map, abatement_map, abatement_pcd, abatement_idle = (
            _agent_productivity_breakdown_maps(qs)
        )
        self.assertGreater(adj_map[mat], raw_map[mat])
        self.assertGreater(abatement_map[mat], 0)
        self.assertAlmostEqual(
            abatement_map[mat],
            abatement_pcd[mat] + abatement_idle[mat],
            places=1,
        )
        self.assertGreater(abatement_idle[mat], 0)
        self.assertEqual(abatement_pcd[mat], 0)

    def test_monitor_time_fields_matches_net_idle(self):
        from apps.produtividade.services.analytics import _monitor_time_fields

        fields = _monitor_time_fields("ariane1", 1700, {"ariane1": 3400})
        self.assertEqual(fields["tempo_ocioso_seconds"], 1700)

    def test_jornada_seconds_ainda_igual_carga_5h30(self):
        self.assertEqual(META_JORNADA_SECONDS, int(float(META_CARGA_HORAS) * 3600))

    def test_case_seconds_included_in_analyzed_idle_lookup(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 7, 17, 10, 0, 0, tzinfo=tz)
        mat = "caseidle1"
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa BRFlow",
            analysis_seconds=100,
            analysis_count=10,
            stage_goal=Decimal("900"),
            recorded_at=day,
            source=SOURCE_BRFLOW,
        )
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa=CASE_ETAPA,
            analysis_seconds=5000,
            analysis_count=50,
            stage_goal=Decimal("320"),
            recorded_at=day.replace(hour=11),
            source=SOURCE_CASE,
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        analyzed = build_analyzed_daily_lookup_for_qs(qs)
        jornada = day.date()
        self.assertEqual(analyzed[(mat, jornada)], 5100)

    def test_case_shift_group_discounted_by_idle(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 7, 17, 10, 0, 0, tzinfo=tz)
        mat = "caseidle2"
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa=CASE_ETAPA,
            analysis_seconds=100,
            analysis_count=50,
            stage_goal=Decimal("320"),
            recorded_at=day,
            source=SOURCE_CASE,
        )
        MonitorEventoRecord.objects.create(
            data=day.date(),
            hora=10,
            matricula_usuario=mat,
            data_evento=day,
            evento="Autenticação com sucesso",
            data_segundo_evento=day.replace(hour=12),
            segundo_evento="Logout",
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        adjusted = _iter_shift_groups(qs, adjust_goal=True)
        for key, bucket in adjusted.items():
            if key[0] == mat and key[2] == CASE_ETAPA:
                self.assertLess(bucket["goal"], 320.0)

        payload = serialize_record(
            qs.get(),
            build_ociosidade_lookup_for_qs(qs),
            {},
        )
        self.assertGreater(payload["tempo_ocioso_dia"], 0)
        self.assertGreater(payload["productivity_pct_abatement_idle"], 0)
        self.assertLess(payload["stage_goal_adjusted"], 320.0)

    def test_discount_requires_pcd_true(self):
        """Desconto residual sem pcd=True não entra no lookup."""
        tz = timezone.get_current_timezone()
        day = datetime(2026, 7, 17, 10, 0, 0, tzinfo=tz)
        mat = "nopcd_residual"
        _seed_productivity_discount(mat, day.date(), 2.0, pcd=False)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="E",
            analysis_seconds=1000,
            analysis_count=50,
            stage_goal=Decimal("900"),
            recorded_at=day,
            source=SOURCE_BRFLOW,
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        lookup = build_productivity_discount_lookup_for_qs(qs)
        self.assertEqual(lookup, {})

    def test_discount_pcd_true_applies(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 7, 17, 10, 0, 0, tzinfo=tz)
        mat = "pcd_ok"
        _seed_productivity_discount(mat, day.date(), 1.0, pcd=True)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="E",
            analysis_seconds=1000,
            analysis_count=50,
            stage_goal=Decimal("900"),
            recorded_at=day,
            source=SOURCE_BRFLOW,
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        lookup = build_productivity_discount_lookup_for_qs(qs)
        self.assertEqual(lookup[(mat, day.date())], 1.0)

    def test_discount_pcd_true_zero_hours(self):
        tz = timezone.get_current_timezone()
        day = datetime(2026, 7, 17, 10, 0, 0, tzinfo=tz)
        mat = "pcd_zero"
        _seed_productivity_discount(mat, day.date(), 0, pcd=True)
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="E",
            analysis_seconds=1000,
            analysis_count=50,
            stage_goal=Decimal("900"),
            recorded_at=day,
            source=SOURCE_BRFLOW,
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        self.assertEqual(build_productivity_discount_lookup_for_qs(qs), {})

    def test_discount_no_history_for_day(self):
        """Sem histórico vigente na data — não usa fallback do ciclo mais recente."""
        tz = timezone.get_current_timezone()
        day = datetime(2026, 7, 17, 10, 0, 0, tzinfo=tz)
        mat = "pcd_gap"
        _seed_productivity_discount(
            mat,
            date(2026, 1, 1),
            2.0,
            pcd=True,
            final_date=date(2026, 1, 31),
        )
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="E",
            analysis_seconds=1000,
            analysis_count=50,
            stage_goal=Decimal("900"),
            recorded_at=day,
            source=SOURCE_BRFLOW,
        )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        self.assertEqual(build_productivity_discount_lookup_for_qs(qs), {})

    def test_discount_pcd_change_mid_period(self):
        tz = timezone.get_current_timezone()
        mat = "pcd_switch"
        agent = Agent.objects.create(
            full_name="Agente switch",
            user_lan_id=mat,
            active=True,
        )
        AgentHistory.objects.create(
            agent=agent,
            start_date=date(2026, 7, 1),
            final_date=date(2026, 7, 10),
            active=True,
            pcd=True,
            productivity_discount=Decimal("1.0"),
        )
        AgentHistory.objects.create(
            agent=agent,
            start_date=date(2026, 7, 11),
            active=True,
            pcd=False,
            productivity_discount=Decimal("1.0"),
        )
        day_pcd = datetime(2026, 7, 5, 10, 0, 0, tzinfo=tz)
        day_no = datetime(2026, 7, 15, 10, 0, 0, tzinfo=tz)
        for d in (day_pcd, day_no):
            ProductivityRecord.objects.create(
                matricula_norm=mat,
                etapa="E",
                analysis_seconds=1000,
                analysis_count=50,
                stage_goal=Decimal("900"),
                recorded_at=d,
                source=SOURCE_BRFLOW,
            )
        qs = ProductivityRecord.objects.filter(matricula_norm=mat)
        lookup = build_productivity_discount_lookup_for_qs(qs)
        self.assertEqual(lookup.get((mat, day_pcd.date())), 1.0)
        self.assertNotIn((mat, day_no.date()), lookup)
