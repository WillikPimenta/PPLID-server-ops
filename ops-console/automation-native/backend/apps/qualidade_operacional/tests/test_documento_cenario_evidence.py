# -*- coding: utf-8 -*-
"""Testes dos painéis Resumo: tipo de documento e cenários em evidência (volume)."""
from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_PLAN_ANALISTA, role_group_name
from apps.qualidade_operacional.models import QualidadeFalha
from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.queries import apply_falha_filters
from apps.qualidade_operacional.services.resumo_extensions import (
    NAO_INFORMADO_KEY,
    NAO_INFORMADO_LABEL,
    build_falhas_documento_e_cenarios,
)

User = get_user_model()

_IMPACT_KEYS = (
    "total_impacto",
    "impacto_ponderado",
    "impacto_share_pct",
)


def _falha(**kwargs):
    defaults = {
        "data": date(2026, 7, 10),
        "data_analise": date(2026, 7, 10),
        "id_cliente": 10,
        "id_workflow": 1,
        "tipo_analise": "Auditoria Compliance",
        "matricula": "m1",
        "protocolo": "P1",
        "etapa": "Validação",
        "tipo_falha": "Manual",
        "categoria_falha": "Crítica",
        "nivel_dificuldade": "Difícil",
        "source_file": "test",
    }
    defaults.update(kwargs)
    return QualidadeFalha.objects.create(**defaults)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class DocumentoCenarioEvidenceTests(TestCase):
    def setUp(self):
        bump_quality_cache_version()
        self.user = User.objects.create_user(
            username="eo_doc_cen", password="x", email="eo_dc@example.com"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.api = APIClient()
        self.api.force_authenticate(user=self.user)
        self.params = {
            "module": "resumo",
            "start_date": "2026-07-01",
            "end_date": "2026-07-24",
            "date_axis": "auditoria",
            "grain": "etapa",
            "dim": "id_cliente",
            "metric": "quantidade",
        }

        for i in range(3):
            _falha(
                protocolo=f"CNH-{i}",
                matricula=f"cnh{i}",
                tipo_documento="CNH",
                cenario="Documento ilegível",
                localidade="Brasília",
                localidade_documento="SP",
            )
        for i in range(2):
            _falha(
                protocolo=f"RG-{i}",
                matricula=f"rg{i}",
                tipo_documento="RG",
                cenario="Dados divergentes",
                localidade="Fortaleza",
                localidade_documento="CE",
            )
        _falha(
            protocolo="BLANK-1",
            matricula="blank1",
            tipo_documento="",
            cenario="",
        )
        long_cenario = (
            "Sobreposição inconsistente com foto cadastral e metadados "
            "divergentes entre frente e verso do documento apresentado"
        )
        _falha(
            protocolo="LONG-1",
            matricula="long1",
            tipo_documento="Selfie",
            cenario=long_cenario,
        )
        self.long_cenario = long_cenario

        _falha(
            data=date(2026, 6, 10),
            data_analise=date(2026, 6, 10),
            protocolo="PREV-CNH",
            matricula="pcnh",
            tipo_documento="CNH",
            cenario="Documento ilegível",
        )
        for i in range(3):
            _falha(
                data=date(2026, 6, 10),
                data_analise=date(2026, 6, 10),
                protocolo=f"PREV-RG-{i}",
                matricula=f"prg{i}",
                tipo_documento="RG",
                cenario="Dados divergentes",
            )

    def _assert_no_impact(self, block: dict):
        for key in _IMPACT_KEYS:
            self.assertNotIn(key, block)
        for row in block.get("rows") or []:
            for key in _IMPACT_KEYS:
                self.assertNotIn(key, row)
        self.assertEqual(block.get("default_order"), "falhas")
        self.assertNotIn("impacto", (block.get("description") or "").casefold())

    def test_shared_blocks_in_resumo_contract(self):
        payload = build_dashboard(self.params)
        self.assertIn("falhas_por_tipo_documento", payload)
        self.assertIn("cenarios_em_evidencia", payload)
        self.assertIn("falhas_por_localidade", payload)
        self.assertIn("prioridades_etapa", payload)
        # Prioridades continua com impacto (não tocado)
        self.assertIn("impacto_ponderado", payload["prioridades_etapa"]["rows"][0])
        docs = payload["falhas_por_tipo_documento"]
        scenes = payload["cenarios_em_evidencia"]
        self._assert_no_impact(docs)
        self._assert_no_impact(scenes)
        self.assertEqual(docs["preview_limit"], 8)
        self.assertIn("volume", docs["description"].casefold())
        self.assertIn("volume", scenes["description"].casefold())

    def test_aggregation_and_ordering(self):
        blocks = build_falhas_documento_e_cenarios(self.params)
        docs = blocks["falhas_por_tipo_documento"]["rows"]
        self.assertEqual(docs[0]["label"], "CNH")
        self.assertEqual(docs[0]["falhas"], 3)
        # Desempate: falhas desc, participação desc, nome asc
        for left, right in zip(docs, docs[1:]):
            self.assertGreaterEqual(left["falhas"], right["falhas"])
            if left["falhas"] == right["falhas"]:
                self.assertGreaterEqual(
                    left["participacao_pct"] or -1,
                    right["participacao_pct"] or -1,
                )

        scenes = blocks["cenarios_em_evidencia"]["rows"]
        self.assertGreaterEqual(scenes[0]["falhas"], scenes[1]["falhas"])
        long_row = next(r for r in scenes if r["label"] == self.long_cenario)
        self.assertEqual(long_row["key"], self.long_cenario)
        self.assertIn(NAO_INFORMADO_LABEL, [r["label"] for r in docs])

    def test_localidade_block(self):
        blocks = build_falhas_documento_e_cenarios(self.params)
        loc = blocks["falhas_por_localidade"]
        self._assert_no_impact(loc)
        self.assertEqual(loc["dimension"], "localidade")
        self.assertEqual(loc["title"], "Falhas por localidade")
        rows = {r["key"]: r for r in loc["rows"]}
        self.assertEqual(rows["SP"]["falhas"], 3)
        self.assertEqual(rows["CE"]["falhas"], 2)
        self.assertEqual(rows["SP"]["drill"], {"localidade": "SP", "lista": "falhas"})

    def test_participation_and_previous(self):
        blocks = build_falhas_documento_e_cenarios(self.params)
        docs = {r["key"]: r for r in blocks["falhas_por_tipo_documento"]["rows"]}
        cnh = docs["CNH"]
        self.assertEqual(cnh["falhas_anterior"], 1)
        self.assertEqual(cnh["delta_falhas"], 2)
        self.assertEqual(cnh["tendencia"], "aumentou")
        self.assertIsNotNone(cnh["participacao_pct"])
        self.assertNotIn("impacto_ponderado", cnh)

        rg = docs["RG"]
        self.assertEqual(rg["falhas_anterior"], 3)
        self.assertEqual(rg["tendencia"], "diminuiu")

        selfie = docs["Selfie"]
        self.assertEqual(selfie["falhas_anterior"], 0)
        self.assertEqual(selfie["tendencia"], "novo")
        self.assertIsNone(selfie["delta_falhas_pct"])

        blank = docs[NAO_INFORMADO_KEY]
        self.assertEqual(blank["label"], NAO_INFORMADO_LABEL)

    def test_division_by_zero_and_empty_universe(self):
        QualidadeFalha.objects.all().delete()
        blocks = build_falhas_documento_e_cenarios(self.params)
        docs = blocks["falhas_por_tipo_documento"]
        self.assertEqual(docs["total_falhas"], 0)
        self.assertEqual(docs["rows"], [])
        self.assertNotIn("total_impacto", docs)

    def test_filters_and_date_axis(self):
        filtered = build_falhas_documento_e_cenarios(
            {**self.params, "tipo_documento": "CNH"}
        )
        docs = filtered["falhas_por_tipo_documento"]["rows"]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["key"], "CNH")

        axis = build_falhas_documento_e_cenarios(
            {**self.params, "date_axis": "analise"}
        )
        self.assertGreater(axis["falhas_por_tipo_documento"]["total_falhas"], 0)

    def test_grain_protocolo_dedupes(self):
        QualidadeFalha.objects.all().delete()
        _falha(protocolo="SAME", tipo_documento="CNH", cenario="A")
        _falha(
            protocolo="SAME",
            matricula="m2",
            tipo_documento="CNH",
            cenario="A",
            categoria_falha="Procedimento",
        )
        blocks = build_falhas_documento_e_cenarios(
            {**self.params, "grain": "protocolo"}
        )
        docs = blocks["falhas_por_tipo_documento"]["rows"]
        self.assertEqual(docs[0]["falhas"], 1)
        self.assertNotIn("impacto_ponderado", docs[0])

    def test_drill_exact_match_counts(self):
        blocks = build_falhas_documento_e_cenarios(self.params)
        cnh = next(
            r
            for r in blocks["falhas_por_tipo_documento"]["rows"]
            if r["key"] == "CNH"
        )
        qs = apply_falha_filters(
            QualidadeFalha.objects.all(),
            {**self.params, "tipo_documento": "CNH"},
        )
        self.assertEqual(qs.count(), cnh["falhas"])

        blank = next(
            r
            for r in blocks["falhas_por_tipo_documento"]["rows"]
            if r["key"] == NAO_INFORMADO_KEY
        )
        qs_blank = apply_falha_filters(
            QualidadeFalha.objects.all(),
            {**self.params, "tipo_documento": NAO_INFORMADO_KEY},
        )
        self.assertEqual(qs_blank.count(), blank["falhas"])

        long_row = next(
            r
            for r in blocks["cenarios_em_evidencia"]["rows"]
            if r["key"] == self.long_cenario
        )
        qs_long = apply_falha_filters(
            QualidadeFalha.objects.all(),
            {**self.params, "cenario": self.long_cenario},
        )
        self.assertEqual(qs_long.count(), long_row["falhas"])
        qs_partial = apply_falha_filters(
            QualidadeFalha.objects.all(),
            {**self.params, "cenario": "Sobreposição inconsistente"},
        )
        self.assertEqual(qs_partial.count(), 0)

    def test_api_resumo_includes_blocks(self):
        resp = self.api.get(
            "/api/v1/qualidade/operacional/dashboard/", self.params
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("ok"))
        self._assert_no_impact(data["falhas_por_tipo_documento"])
        self._assert_no_impact(data["cenarios_em_evidencia"])
        self._assert_no_impact(data["falhas_por_localidade"])
        for key in (
            "kpis",
            "insights",
            "serie",
            "breakdown",
            "prioridades_etapa",
            "contestacao_leitura",
            "clientes_melhorias",
        ):
            self.assertIn(key, data)
