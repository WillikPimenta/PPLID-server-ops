# -*- coding: utf-8 -*-
"""Regressão: madrugada usa data_jornada (corte 05:15), não data civil."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.analytics import (
    _monitor_time_fields,
    _agent_monitor_logado_totals,
    apply_record_filters,
    build_agent_detail,
    build_por_agente,
)
from apps.produtividade.services.goal_adjustment import build_logado_lookup_for_qs
from apps.produtividade.services.monitor_bridge import (
    clear_monitor_bridge_cache_for_tests,
    get_monitor_tabela_rows_for_qs,
)


def _aware(dt: datetime) -> datetime:
    return timezone.make_aware(dt, timezone.get_current_timezone())


class MonitorBridgeMadrugadaTests(TestCase):
    def setUp(self):
        clear_monitor_bridge_cache_for_tests()
        self.mat = "madrugada1"
        # Produtividade civil 25/06 04:00 → jornada 24/06.
        ProductivityRecord.objects.create(
            matricula_norm=self.mat,
            etapa="Etapa Madrugada",
            analysis_seconds=1800,
            analysis_count=1,
            stage_goal=Decimal("900"),
            recorded_at=_aware(datetime(2026, 6, 25, 4, 0, 0)),
            agent_name="Madrugada Agent",
            team="Time Madrugada",
            journey_shift="Madrugada",
        )
        # Login começa ainda no dia civil 24 (noite) e cobre a madrugada.
        MonitorEventoRecord.objects.create(
            data=datetime(2026, 6, 24).date(),
            hora=22,
            matricula_usuario=self.mat,
            data_evento=_aware(datetime(2026, 6, 24, 22, 0, 0)),
            evento="Autenticação com sucesso",
            data_segundo_evento=_aware(datetime(2026, 6, 25, 5, 0, 0)),
            segundo_evento="Logout",
        )

    def test_bridge_loads_jornada_not_civil_day(self):
        qs = ProductivityRecord.objects.filter(matricula_norm=self.mat)
        rows = get_monitor_tabela_rows_for_qs(qs)
        self.assertTrue(rows)
        jornadas = {str(r.get("data_jornada"))[:10] for r in rows}
        self.assertIn("2026-06-24", jornadas)
        self.assertNotIn("2026-06-25", jornadas)

        day_totals = [r for r in rows if r.get("hora") is None]
        self.assertTrue(day_totals)
        logado = int(day_totals[0].get("tempo_logado_dia") or 0)
        self.assertGreater(logado, 0)

    def test_logado_lookup_and_monitor_fields_for_madrugada(self):
        # Produção civil 25 04:00 — filtro pelo dia civil da produção.
        qs = apply_record_filters(
            ProductivityRecord.objects.all(),
            {"start_date": "2026-06-25", "end_date": "2026-06-25"},
        )
        self.assertTrue(qs.exists())
        lookup = build_logado_lookup_for_qs(qs)
        self.assertGreater(lookup.get((self.mat, datetime(2026, 6, 24).date()), 0), 0)

        totals = _agent_monitor_logado_totals(qs)
        fields = _monitor_time_fields(self.mat, 1800, totals)
        self.assertIsNotNone(fields["tempo_logado_seconds"])
        self.assertGreater(fields["tempo_logado_seconds"], 0)
        self.assertIsNotNone(fields["tempo_ocioso_seconds"])

    def test_filter_civil_day_includes_morning_madrugada(self):
        """Filtro no dia civil 25 inclui 25 04:00; dia 24 sozinho não."""
        qs = apply_record_filters(
            ProductivityRecord.objects.all(),
            {"start_date": "2026-06-25", "end_date": "2026-06-25"},
        )
        self.assertEqual(qs.count(), 1)

        qs_prev_only = apply_record_filters(
            ProductivityRecord.objects.all(),
            {"start_date": "2026-06-24", "end_date": "2026-06-24"},
        )
        self.assertEqual(qs_prev_only.count(), 0)

    def test_pace_uses_jornada_not_civil_day(self):
        """Realizado e esperado do pace devem casar na data_jornada (não no civil)."""
        from apps.produtividade.services.pace_tracking import build_pace_for_agent

        qs = apply_record_filters(
            ProductivityRecord.objects.all(),
            {"start_date": "2026-06-25", "end_date": "2026-06-25"},
        )
        pace = build_pace_for_agent(self.mat, qs)
        self.assertNotEqual(pace["pace_status"], "unknown")
        self.assertIsNotNone(pace["pace_actual_pct"])
        self.assertIsNotNone(pace["pace_expected_pct"])
        self.assertGreater(pace["pace_days_count"], 0)

    def test_tabela_hora_em_brasilia_nao_utc(self):
        """Sessão 23h–05h SP: buckets em horário de Brasília (não UTC)."""
        from apps.monitor_eventos.services.tabela_monitor import (
            build_tabela_monitor_from_records,
        )
        from datetime import timezone as dt_timezone

        # Simula o que o PG devolve: instante UTC equivalente a 28/06 23:00 SP.
        login_utc = datetime(2026, 6, 29, 2, 0, 0, tzinfo=dt_timezone.utc)
        logout_utc = datetime(2026, 6, 29, 8, 0, 0, tzinfo=dt_timezone.utc)
        records = [
            {
                "matricula_usuario": self.mat,
                "data_evento": login_utc,
                "data_segundo_evento": logout_utc,
                "evento": "Autenticação com sucesso",
                "segundo_evento": "Logout",
            }
        ]
        results = build_tabela_monitor_from_records(records, tempo_analise_lookup={})
        hours = {r["hora"] for r in results if r.get("hora") is not None}
        # SP: 23→04 (logout 05:00 fecha o bucket das 04h)
        self.assertIn(23, hours)
        self.assertTrue({0, 1, 2, 3, 4}.issubset(hours))
        # Sem conversão local, o bucket inicial seria 02h UTC e a 23h sumiria.
        self.assertNotEqual(min(h for h in hours if h is not None), 2)
        row_23 = next(r for r in results if r["hora"] == 23)
        self.assertEqual(str(row_23["data_jornada"])[:10], "2026-06-28")
        self.assertGreater(int(row_23["tempo_logado"] or 0), 0)
        self.assertEqual(int(row_23["tempo_logado"]), 3600)

    def test_por_hora_logged_23h_only_when_prev_day_in_filter(self):
        """Dia civil 25: manhã da madrugada; 23h do dia 24 só com intervalo 24–25."""
        from apps.produtividade.services.analytics import build_por_hora_page
        from apps.produtividade.services.pace_tracking import (
            build_monitor_hourly_logado_lookup,
        )

        clear_monitor_bridge_cache_for_tests()
        # Produção só na madrugada civil 25 04:00 (jornada 24).
        # Login 24 22:00 → 25 05:00 cobre a 23h.
        qs_today = apply_record_filters(
            ProductivityRecord.objects.all(),
            {"start_date": "2026-06-25", "end_date": "2026-06-25"},
        )
        page_today = build_por_hora_page(qs_today)
        overview_today = " ".join(r["hour"] for r in page_today["logged_overview"])
        self.assertIn("T04:00:00", overview_today)
        self.assertNotIn("T23:00:00", overview_today)
        hm_today = next(
            r for r in page_today["logged_heatmap"]["rows"] if r["matricula"] == self.mat
        )
        # eixo 0→23: índice 4 = 04h; 23h fica fora do recorte civil
        self.assertIsNotNone(hm_today["values"][4])
        self.assertIsNone(hm_today["values"][23])

        qs_range = apply_record_filters(
            ProductivityRecord.objects.all(),
            {"start_date": "2026-06-24", "end_date": "2026-06-25"},
        )
        lookup = build_monitor_hourly_logado_lookup(qs_range)
        hours = {h for (_m, _d, h) in lookup.keys()}
        self.assertIn(23, hours)
        self.assertIn(22, hours)

        page_range = build_por_hora_page(qs_range)
        overview_range = " ".join(r["hour"] for r in page_range["logged_overview"])
        self.assertIn("T23:00:00", overview_range)
        hm_range = next(
            r for r in page_range["logged_heatmap"]["rows"] if r["matricula"] == self.mat
        )
        self.assertIsNotNone(hm_range["values"][23])
        self.assertGreater(hm_range["values"][23], 0)

    def test_detail_logado_matches_list_and_ignores_internal_stage_filter(self):
        """Seleção/filtro interno preservam o logado do período da listagem."""
        mat = "detail1"
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=1200,
            analysis_count=10,
            stage_goal=Decimal("900"),
            recorded_at=_aware(datetime(2026, 8, 7, 9, 0, 0)),
            agent_name="Detail Agent",
            team="Time Detail",
        )
        for day in (7, 8):
            MonitorEventoRecord.objects.create(
                data=datetime(2026, 8, day).date(),
                hora=8,
                matricula_usuario=mat,
                data_evento=_aware(datetime(2026, 8, day, 8, 0, 0)),
                evento="Autenticação com sucesso",
                data_segundo_evento=_aware(datetime(2026, 8, day, 9, 0, 0)),
                segundo_evento="Logout",
            )

        qs = apply_record_filters(
            ProductivityRecord.objects.all(),
            {
                "start_date": "2026-08-07",
                "end_date": "2026-08-08",
                "team": ["Time Detail"],
            },
        )
        list_rows, _summary = build_por_agente(qs)
        list_row = next(row for row in list_rows if row["matricula"] == mat)
        detail = build_agent_detail(mat, qs, etapa="Etapa A")

        self.assertEqual(list_row["tempo_logado_seconds"], 7200)
        self.assertEqual(detail["summary"]["tempo_logado_seconds"], 7200)
        self.assertEqual(detail["summary"]["tempo_logado_hms"], "02:00:00")
        self.assertEqual(detail["summary"]["total_protocols"], 10)

    def test_detail_logado_invalidates_thread_cache_after_monitor_update(self):
        """Leandra: detalhe sai de 01:02:06 para 04:04:52 após nova carga."""
        mat = "c13703q"
        ProductivityRecord.objects.create(
            matricula_norm=mat,
            etapa="Etapa A",
            analysis_seconds=1200,
            analysis_count=10,
            stage_goal=Decimal("900"),
            recorded_at=_aware(datetime(2026, 8, 7, 13, 0, 0)),
            agent_name="Leandra Dos Santos Oliveira",
            team="Time Detail",
        )
        sessions = [
            ((8, 4, 56), (8, 8, 15)),
            ((8, 11, 34), (9, 3, 32)),
            ((9, 3, 41), (9, 10, 30)),
            ((9, 20, 39), (10, 0, 7)),
            ((10, 0, 21), (10, 35, 27)),
            ((10, 40, 53), (11, 0, 23)),
            ((11, 0, 29), (11, 3, 37)),
            ((11, 10, 9), (12, 0, 7)),
            ((12, 0, 14), (12, 35, 50)),
        ]

        def create_sessions(items):
            for start, end in items:
                MonitorEventoRecord.objects.create(
                    data=datetime(2026, 8, 7).date(),
                    hora=start[0],
                    matricula_usuario=mat,
                    data_evento=_aware(datetime(2026, 8, 7, *start)),
                    evento="Autenticação com sucesso",
                    data_segundo_evento=_aware(datetime(2026, 8, 7, *end)),
                    segundo_evento="Logout",
                )

        create_sessions(sessions[:3])
        params = {"start_date": "2026-08-07", "end_date": "2026-08-07"}
        before = build_agent_detail(
            mat,
            apply_record_filters(ProductivityRecord.objects.all(), params),
        )
        self.assertEqual(before["summary"]["tempo_logado_seconds"], 3726)
        self.assertEqual(before["summary"]["tempo_logado_hms"], "01:02:06")

        create_sessions(sessions[3:])
        after = build_agent_detail(
            mat,
            apply_record_filters(ProductivityRecord.objects.all(), params),
            etapa="Etapa A",
        )
        self.assertEqual(after["summary"]["tempo_logado_seconds"], 14692)
        self.assertEqual(after["summary"]["tempo_logado_hms"], "04:04:52")
