# -*- coding: utf-8 -*-
"""Paridade funcional e regressão das otimizações de performance EO."""
from __future__ import annotations

from datetime import date

from django.core.cache import cache
from django.db.models import Q
from django.test import TestCase

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha, QualidadeFiltroOpcao
from apps.qualidade_operacional.services.analytics import _build_breakdown
from apps.qualidade_operacional.services.contestacao_metrics import (
    build_contestacao_metrics,
    clear_contestacao_tipo_cache,
    contestacao_auditado_q,
)
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.insights import build_insights
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version


class QualidadeEoParityPerfTests(TestCase):
    def setUp(self):
        cache.clear()
        bump_quality_cache_version()
        clear_contestacao_tipo_cache()
        QualidadeFiltroOpcao.objects.create(
            dimensao="tipo_analise", valor="Contestação Externa"
        )
        QualidadeFiltroOpcao.objects.create(
            dimensao="tipo_analise", valor="Contestação Compliance"
        )
        QualidadeFiltroOpcao.objects.create(
            dimensao="tipo_analise", valor="Auditoria Compliance"
        )
        for i, tipo in enumerate(
            ("Contestação Externa", "Contestação Externa", "Auditoria Compliance")
        ):
            QualidadeAuditado.objects.create(
                data=date(2026, 7, 10),
                data_analise=date(2026, 7, 10),
                id_cliente=6 + (i % 2),
                protocolo=f"PAR{i}",
                tipo_analise=tipo,
                tipo_conclusao="Manual",
                matricula=f"c91{i:03d}a",
                source_file="parity",
            )
        QualidadeFalha.objects.create(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            id_cliente=6,
            protocolo="PAR0",
            tipo_analise="Contestação Externa",
            tipo_falha="Manual",
            matricula="c91000a",
            localidade="Brasília",
            source_file="parity",
        )
        self.params = {
            "module": "resumo",
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "date_axis": "auditoria",
            "grain": "etapa",
            "dim": "id_cliente",
            "metric": "quantidade",
        }

    def test_contestacao_catalog_in_matches_icontains(self):
        clear_contestacao_tipo_cache()
        q_in = contestacao_auditado_q()
        self.assertTrue(str(q_in).find("tipo_analise__in") >= 0 or "IN" in str(q_in))
        metrics = build_contestacao_metrics(self.params)
        self.assertEqual(metrics["protocolos_contestados"], 2)
        self.assertEqual(metrics["auditados_contestacao"], 2)
        self.assertEqual(metrics["eventos_contestacao"], 1)
        self.assertEqual(metrics["procedentes"], 1)

        # Oráculo independente: icontains bruto
        from apps.qualidade_operacional.models import QualidadeAuditado as Aud

        oracle = (
            Aud.objects.filter(
                data__gte=date(2026, 7, 1),
                data__lte=date(2026, 7, 31),
            )
            .filter(Q(tipo_analise__icontains="contest"))
            .exclude(tipo_analise__icontains="Compliance")
            .exclude(protocolo="")
            .values("protocolo")
            .distinct()
            .count()
        )
        self.assertEqual(metrics["protocolos_contestados"], oracle)

    def test_dashboard_resumo_skips_global_protocol_distinct_keeps_eo(self):
        dash = build_dashboard(self.params)
        self.assertTrue(dash["ok"])
        self.assertEqual(dash["kpis"]["auditados"], 3)
        self.assertEqual(dash["kpis"]["falhas"], 1)
        self.assertEqual(dash["kpis"]["eo_pct"], 66.7)
        self.assertEqual(dash["kpis"]["protocolos_contestados"], 2)
        # Resumo omite distinct global caro; Cliente/Agentes continuam preenchendo.
        self.assertIsNone(dash["kpis"]["protocolos_auditados"])
        self.assertIsNone(dash["kpis"]["protocolos_com_falha"])

    def test_breakdown_reuses_insights_clientes_without_changing_totals(self):
        dash = build_dashboard(self.params)
        insights = dash["insights"]
        breakdown = dash["breakdown"]
        self.assertEqual(breakdown["dim"], "id_cliente")
        by_insights = {
            str(r["id_cliente"]): (r["auditados"], r["falhas"], r["eo_pct"])
            for r in insights["top_clientes"]
        }
        for row in breakdown["rows"]:
            key = str(row["key"])
            self.assertIn(key, by_insights)
            a, f, eo = by_insights[key]
            self.assertEqual(row["auditados"], a)
            self.assertEqual(row["falhas"], f)
            self.assertEqual(row["eo_pct"], eo)

        # Rebuild sem reuse deve bater nos mesmos totais de falhas/auditados por chave
        insights_only = build_insights(self.params)
        rebuilt = _build_breakdown(
            self.params,
            total_aud=dash["kpis"]["auditados"],
            total_fal=dash["kpis"]["falhas"],
        )
        map_reuse = {r["key"]: (r["auditados"], r["falhas"]) for r in breakdown["rows"]}
        map_fresh = {r["key"]: (r["auditados"], r["falhas"]) for r in rebuilt["rows"]}
        self.assertEqual(map_reuse, map_fresh)
        self.assertGreaterEqual(len(insights_only["top_clientes"]), 1)
