# -*- coding: utf-8 -*-
"""Testes da Métrica oficial (official-v1) de Excelência Operacional."""
from __future__ import annotations

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha
from apps.qualidade_operacional.services.analytics import (
    build_kpis,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.official_metric import (
    BLOCKED_FALHA_TIPO_ANALISE,
    BLOCKED_FALHA_TIPO_FALHA,
    BLOCKED_FALHA_TIPO_MODULO_2,
    METRIC_MODE_COMPLETE,
    METRIC_MODE_OFFICIAL,
    OFFICIAL_METRIC_CUTOVER,
    OFFICIAL_METRIC_VERSION,
    normalize_official_label,
    resolve_metric_mode,
)
from apps.qualidade_operacional.services.performance_cache import (
    _PAYLOAD_SCHEMA,
    cache_key_parts,
)
from apps.qualidade_operacional.services.queries import params_with

User = get_user_model()


def _aud(**kwargs):
    defaults = {
        "data": date(2026, 6, 30),
        "data_analise": date(2026, 6, 30),
        "protocolo": "P-AUD",
        "tipo_analise": "Auditoria Compliance",
        "tipo_conclusao": "Manual",
        "source_file": "test",
    }
    defaults.update(kwargs)
    return QualidadeAuditado.objects.create(**defaults)


def _fal(**kwargs):
    defaults = {
        "data": date(2026, 6, 30),
        "data_analise": date(2026, 6, 30),
        "protocolo": "P-FAL",
        "tipo_analise": "Auditoria Compliance",
        "tipo_modulo_2": "Compliance",
        "tipo_falha": "Manual",
        "categoria_falha": "Não Crítica",
        "nivel_dificuldade": "Fácil",
        "etapa": "Análise Visual",
        "source_file": "test",
    }
    defaults.update(kwargs)
    return QualidadeFalha.objects.create(**defaults)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OfficialMetricResolveTests(TestCase):
    def test_default_is_complete(self):
        self.assertEqual(resolve_metric_mode({}), METRIC_MODE_COMPLETE)
        self.assertEqual(resolve_metric_mode({"metric_mode": ""}), METRIC_MODE_COMPLETE)
        self.assertEqual(resolve_metric_mode({"metric_mode": "xyz"}), METRIC_MODE_COMPLETE)

    def test_complete_aliases(self):
        self.assertEqual(resolve_metric_mode({"metric_mode": "complete"}), METRIC_MODE_COMPLETE)
        self.assertEqual(resolve_metric_mode({"metric_mode": "completo"}), METRIC_MODE_COMPLETE)

    def test_normalize_label(self):
        self.assertEqual(normalize_official_label("  análise direcionada  "), "ANÁLISE DIRECIONADA")
        self.assertNotEqual(
            normalize_official_label("(APP) Análise direcionada"),
            normalize_official_label("Análise direcionada"),
        )


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OfficialMetricAuditadosTests(TestCase):
    def test_blocked_before_cutover_and_included_after(self):
        cases = (
            (date(2026, 6, 30), "Auditoria Redoc", False),
            (date(2026, 7, 1), "Auditoria Redoc", True),
            (date(2026, 6, 30), "Análise direcionada", False),
            (date(2026, 6, 30), "(APP) Análise direcionada", True),
            (date(2026, 6, 30), "Análise SF", False),
            (date(2026, 6, 30), "ANÁLISE ESPECIAL - SF", True),
        )
        for i, (d, tipo, expected) in enumerate(cases):
            with self.subTest(tipo=tipo, data=d):
                QualidadeAuditado.objects.all().delete()
                _aud(data=d, data_analise=d, tipo_analise=tipo, protocolo=f"A{i}")
                qs = filtered_auditados(
                    {
                        "start_date": "2026-06-01",
                        "end_date": "2026-07-31",
                        "metric_mode": "official",
                        "date_axis": "auditoria",
                    }
                )
                self.assertEqual(qs.count(), 1 if expected else 0)

    def test_null_data_excluded_even_with_analise_axis(self):
        _aud(data=None, data_analise=date(2026, 6, 15), tipo_analise="Auditoria Compliance")
        qs = filtered_auditados(
            {
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "metric_mode": "official",
                "date_axis": "analise",
            }
        )
        self.assertEqual(qs.count(), 0)

    def test_substring_not_used(self):
        _aud(tipo_analise="Pré Análise direcionada extra")
        self.assertEqual(
            filtered_auditados({"metric_mode": "official", "date_axis": "auditoria"}).count(),
            1,
        )

    def test_complete_keeps_blocked(self):
        _aud(tipo_analise="Auditoria Redoc")
        self.assertEqual(
            filtered_auditados({"metric_mode": "complete"}).count(),
            1,
        )
        self.assertEqual(
            filtered_auditados({"metric_mode": "official"}).count(),
            0,
        )


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OfficialMetricFalhasTests(TestCase):
    def test_each_blocked_tipo_analise(self):
        for tipo in BLOCKED_FALHA_TIPO_ANALISE:
            with self.subTest(tipo=tipo):
                QualidadeFalha.objects.all().delete()
                _fal(tipo_analise=tipo)
                self.assertEqual(filtered_falhas({"metric_mode": "official"}).count(), 0)
                self.assertEqual(filtered_falhas({"metric_mode": "complete"}).count(), 1)

    def test_each_blocked_modulo(self):
        for modulo in BLOCKED_FALHA_TIPO_MODULO_2:
            with self.subTest(modulo=modulo):
                QualidadeFalha.objects.all().delete()
                _fal(tipo_modulo_2=modulo)
                self.assertEqual(filtered_falhas({"metric_mode": "official"}).count(), 0)

    def test_each_blocked_tipo_falha(self):
        for tipo in BLOCKED_FALHA_TIPO_FALHA:
            with self.subTest(tipo=tipo):
                QualidadeFalha.objects.all().delete()
                _fal(tipo_falha=tipo)
                self.assertEqual(filtered_falhas({"metric_mode": "official"}).count(), 0)

    def test_one_dimension_enough(self):
        _fal(tipo_analise="Auditoria Compliance", tipo_modulo_2="Análises Avulsas", tipo_falha="Manual")
        self.assertEqual(filtered_falhas({"metric_mode": "official"}).count(), 0)

    def test_all_enter_on_or_after_cutover(self):
        _fal(
            data=OFFICIAL_METRIC_CUTOVER,
            data_analise=OFFICIAL_METRIC_CUTOVER,
            tipo_analise="Auditoria Redoc",
            tipo_modulo_2="Análise direcionada",
            tipo_falha="BIOMETRIA",
        )
        self.assertEqual(filtered_falhas({"metric_mode": "official"}).count(), 1)

    def test_null_data_excluded(self):
        _fal(data=None, data_analise=date(2026, 6, 15))
        self.assertEqual(
            filtered_falhas(
                {"metric_mode": "official", "date_axis": "analise", "start_date": "2026-06-01", "end_date": "2026-06-30"}
            ).count(),
            0,
        )

    def test_ataque_de_fraude_not_blocked_by_lists(self):
        """Ataque de fraude não está nas listas; entra antes do corte se demais dims ok."""
        _fal(tipo_analise="Ataque de fraude", tipo_falha="Manual", tipo_modulo_2="")
        self.assertEqual(filtered_falhas({"metric_mode": "official"}).count(), 1)

    def test_empty_values_not_auto_blocked(self):
        _fal(tipo_analise="", tipo_modulo_2="", tipo_falha="")
        self.assertEqual(filtered_falhas({"metric_mode": "official"}).count(), 1)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OfficialMetricAggregationTests(TestCase):
    def setUp(self):
        for i in range(10):
            _aud(
                data=date(2026, 6, 15),
                data_analise=date(2026, 6, 15),
                protocolo=f"OK-{i}",
                tipo_analise="Auditoria Compliance",
            )
        for i in range(5):
            _aud(
                data=date(2026, 6, 15),
                data_analise=date(2026, 6, 15),
                protocolo=f"RED-{i}",
                tipo_analise="Auditoria Redoc",
            )
        _fal(
            data=date(2026, 6, 15),
            data_analise=date(2026, 6, 15),
            protocolo="F-OK",
            tipo_falha="Manual",
            categoria_falha="Falha Crítica",
            nivel_dificuldade="Fácil",
            etapa="Análise Visual",
        )
        _fal(
            data=date(2026, 6, 15),
            data_analise=date(2026, 6, 15),
            protocolo="F-BIO",
            tipo_falha="BIOMETRIA",
            categoria_falha="Falha Crítica",
            nivel_dificuldade="Fácil",
            etapa="Análise Visual",
        )

    def test_grain_etapa_vs_protocolo(self):
        # Extra linha mesmo protocolo
        _aud(
            data=date(2026, 6, 15),
            protocolo="OK-0",
            tipo_analise="Auditoria Compliance",
        )
        _aud(
            data=date(2026, 6, 15),
            protocolo="",
            tipo_analise="Auditoria Compliance",
        )
        etapa = build_kpis(
            {
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "grain": "etapa",
                "metric_mode": "official",
                "date_axis": "auditoria",
            }
        )
        proto = build_kpis(
            {
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "grain": "protocolo",
                "metric_mode": "official",
                "date_axis": "auditoria",
            }
        )
        # 10 OK + 1 duplicate OK-0 + 1 blank protocolo = 12 linhas etapa oficiais
        self.assertEqual(etapa["auditados"], 12)
        self.assertEqual(etapa["falhas"], 1)
        # Protocolo: 10 distintos OK-* (blank excluído); falha 1
        self.assertEqual(proto["auditados"], 10)
        self.assertEqual(proto["falhas"], 1)

    def test_weighted_excludes_non_official_failure(self):
        official = build_kpis(
            {
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "grain": "etapa",
                "metric_mode": "official",
            }
        )
        complete = build_kpis(
            {
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "grain": "etapa",
                "metric_mode": "complete",
            }
        )
        self.assertEqual(official["falhas"], 1)
        self.assertEqual(complete["falhas"], 2)
        self.assertLess(official["impacto_ponderado"], complete["impacto_ponderado"])

    def test_params_with_preserves_metric_mode(self):
        prev = params_with(
            {"metric_mode": "complete", "grain": "etapa"},
            start_date="2026-05-01",
            end_date="2026-05-31",
        )
        self.assertEqual(prev["metric_mode"], "complete")
        self.assertEqual(resolve_metric_mode(prev), METRIC_MODE_COMPLETE)

    def test_july_unifies_populations(self):
        QualidadeAuditado.objects.all().delete()
        QualidadeFalha.objects.all().delete()
        _aud(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            tipo_analise="Auditoria Redoc",
            protocolo="J1",
        )
        _fal(
            data=date(2026, 7, 10),
            data_analise=date(2026, 7, 10),
            tipo_falha="BIOMETRIA",
            protocolo="J1",
        )
        params = {
            "start_date": "2026-07-01",
            "end_date": "2026-07-31",
            "grain": "etapa",
        }
        off = build_kpis({**params, "metric_mode": "official"})
        comp = build_kpis({**params, "metric_mode": "complete"})
        self.assertEqual(off["auditados"], comp["auditados"])
        self.assertEqual(off["falhas"], comp["falhas"])

    def test_m1_june_applies_historical_cut(self):
        QualidadeAuditado.objects.all().delete()
        QualidadeFalha.objects.all().delete()
        for i in range(10):
            _aud(
                data=date(2026, 7, 10),
                data_analise=date(2026, 7, 10),
                protocolo=f"JUL-{i}",
                tipo_analise="Auditoria Compliance",
            )
        for i in range(8):
            _aud(
                data=date(2026, 6, 15),
                data_analise=date(2026, 6, 15),
                protocolo=f"JUN-OK-{i}",
                tipo_analise="Auditoria Compliance",
            )
        for i in range(4):
            _aud(
                data=date(2026, 6, 15),
                data_analise=date(2026, 6, 15),
                protocolo=f"JUN-RED-{i}",
                tipo_analise="Auditoria Redoc",
            )
        kpis = build_kpis(
            {
                "start_date": "2026-07-01",
                "end_date": "2026-07-31",
                "grain": "etapa",
                "metric_mode": "official",
            }
        )
        self.assertEqual(kpis["auditados"], 10)
        self.assertEqual(kpis["previous"]["auditados"], 8)

    def test_kpis_payload_meta(self):
        kpis = build_kpis({"metric_mode": "official", "grain": "etapa"})
        self.assertEqual(kpis["metric_mode"], "official")
        self.assertTrue(kpis["official_metric"]["active"])
        self.assertEqual(kpis["official_metric"]["version"], OFFICIAL_METRIC_VERSION)
        self.assertEqual(kpis["official_metric"]["cutover"], "2026-07-01")
        self.assertEqual(kpis["official_metric"]["date_field"], "data")

    def test_resumo_dashboard_uses_filtered_population(self):
        dash = build_dashboard(
            {
                "module": "resumo",
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "grain": "etapa",
                "metric_mode": "official",
            }
        )
        self.assertEqual(dash["kpis"]["auditados"], 10)
        self.assertEqual(dash["kpis"]["falhas"], 1)

    def test_cache_keys_differ_by_metric_mode(self):
        self.assertIn("official-metric", _PAYLOAD_SCHEMA)
        a = cache_key_parts("kpis", {"metric_mode": "official", "grain": "etapa"})
        b = cache_key_parts("kpis", {"metric_mode": "complete", "grain": "etapa"})
        self.assertNotEqual(a, b)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OfficialMetricApiPropagationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="eo_official", password="x", email="eo_off@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        cache.clear()
        _aud(tipo_analise="Auditoria Redoc", protocolo="API-RED")
        _aud(tipo_analise="Auditoria Compliance", protocolo="API-OK")
        _fal(tipo_falha="BIOMETRIA", protocolo="API-BIO")
        _fal(tipo_falha="Manual", protocolo="API-OKF")

    def test_default_query_is_complete(self):
        resp = self.client.get(
            "/api/v1/qualidade/operacional/kpis/",
            {"start_date": "2026-06-01", "end_date": "2026-06-30", "grain": "etapa"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["metric_mode"], "complete")
        self.assertEqual(data["auditados"], 2)
        self.assertEqual(data["falhas"], 2)

    def test_complete_parity(self):
        resp = self.client.get(
            "/api/v1/qualidade/operacional/kpis/",
            {
                "start_date": "2026-06-01",
                "end_date": "2026-06-30",
                "grain": "etapa",
                "metric_mode": "complete",
            },
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["metric_mode"], "complete")
        self.assertEqual(data["auditados"], 2)
        self.assertEqual(data["falhas"], 2)

    def test_list_endpoints_respect_mode(self):
        off = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"start_date": "2026-06-01", "end_date": "2026-06-30", "metric_mode": "official"},
        )
        comp = self.client.get(
            "/api/v1/qualidade/operacional/auditados/",
            {"start_date": "2026-06-01", "end_date": "2026-06-30", "metric_mode": "complete"},
        )
        self.assertEqual(off.json()["count"], 1)
        self.assertEqual(comp.json()["count"], 2)

        off_f = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"start_date": "2026-06-01", "end_date": "2026-06-30", "metric_mode": "official"},
        )
        comp_f = self.client.get(
            "/api/v1/qualidade/operacional/falhas/",
            {"start_date": "2026-06-01", "end_date": "2026-06-30", "metric_mode": "complete"},
        )
        self.assertEqual(off_f.json()["count"], 1)
        self.assertEqual(comp_f.json()["count"], 2)

    def test_dashboard_modules_propagate(self):
        for module in ("resumo", "agentes", "contestacao"):
            with self.subTest(module=module):
                cache.clear()
                resp = self.client.get(
                    "/api/v1/qualidade/operacional/dashboard/",
                    {
                        "module": module,
                        "start_date": "2026-06-01",
                        "end_date": "2026-06-30",
                        "grain": "etapa",
                        "metric_mode": "official",
                    },
                )
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertEqual(data.get("kpis", {}).get("metric_mode"), "official")
