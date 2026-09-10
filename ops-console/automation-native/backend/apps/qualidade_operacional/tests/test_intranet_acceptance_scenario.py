# -*- coding: utf-8 -*-
"""Cenário sintético dos quatro fluxos pelo resultado canônico."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.auditoria.models import AuditoriaFalhaCadastro, AuditoriaMotivoFalha
from apps.auditoria.testing.qualidade_intranet_acceptance_profile import (
    CLIENTE,
    EXPECTED_EO_PONDERADO_ETAPA,
    EXPECTED_EO_PONDERADO_PROTOCOLO,
    EXPECTED_ETAPA,
    EXPECTED_IMPACTO_ETAPA,
    EXPECTED_IMPACTO_PROTOCOLO,
    EXPECTED_PROTOCOLO,
    SEED_PREFIX,
    TOTAL_ROWS,
    WORKFLOW,
    build_acceptance_rows,
    motivo_catalog_rows,
)
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeIntranetProjection,
)
from apps.qualidade_operacional.services.analytics import (
    GRAIN_ETAPA,
    GRAIN_PROTOCOLO,
    _count_qs,
    _eo_pct,
    _weighted_failure_total,
    build_kpis,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.intranet_source import (
    build_cascade_reconciliation,
    classify_source_record,
    sync_one,
    sync_queryset,
)
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.queries import NAO_INFORMADO

User = get_user_model()


def _load_acceptance(user) -> None:
    DimCliente.objects.get_or_create(id_cliente=9100, defaults={"nome": CLIENTE})
    DimWorkflow.objects.get_or_create(id_workflow=9100, defaults={"nome": WORKFLOW})
    for row in motivo_catalog_rows():
        AuditoriaMotivoFalha.objects.get_or_create(
            motivo=row["motivo"],
            defaults={
                "criticidade": row["criticidade"],
                "segmentos": row["segmentos"],
                "subsegmento": row["subsegmento"],
                "active": True,
            },
        )
    AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=SEED_PREFIX).delete()
    QualidadeIntranetProjection.objects.filter(
        source__protocolo__startswith=SEED_PREFIX
    ).delete()
    for sample in build_acceptance_rows():
        AuditoriaFalhaCadastro.objects.create(**sample, created_by=user)


def _params(**extra):
    base = {
        "start_date": "2026-08-10",
        "end_date": "2026-08-10",
        "metric_mode": "complete",
        "grain": GRAIN_ETAPA,
        "date_axis": "auditoria",
    }
    base.update(extra)
    return base


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class IntranetAcceptanceScenarioTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = User.objects.create_user("eo_acc", password="x")
        _load_acceptance(cls.user)
        qs = AuditoriaFalhaCadastro.objects.filter(protocolo__startswith=SEED_PREFIX)
        sync_queryset(qs.select_related("atividade", "created_by"), force=True)
        bump_quality_cache_version()

    def test_01_fontes_e_reconciliacao(self):
        cascade = build_cascade_reconciliation(prefix=SEED_PREFIX)
        self.assertEqual(cascade["total_sources"], TOTAL_ROWS)
        self.assertEqual(cascade["expected_auditados"], EXPECTED_ETAPA["auditados"])
        self.assertEqual(cascade["expected_falhas"], EXPECTED_ETAPA["falhas"])
        self.assertEqual(cascade["reinspecao_improcedente_rows"], 9)
        self.assertEqual(cascade["unreconciled_difference"], 0)

    def test_02_eo_etapa_e_protocolo(self):
        aud = filtered_auditados(_params())
        fal = filtered_falhas(_params())
        self.assertEqual(_count_qs(aud, GRAIN_ETAPA), EXPECTED_ETAPA["auditados"])
        self.assertEqual(_count_qs(fal, GRAIN_ETAPA), EXPECTED_ETAPA["falhas"])
        self.assertEqual(_eo_pct(EXPECTED_ETAPA["auditados"], EXPECTED_ETAPA["falhas"]), EXPECTED_ETAPA["eo_pct"])

        p = _params(grain=GRAIN_PROTOCOLO)
        self.assertEqual(_count_qs(filtered_auditados(p), GRAIN_PROTOCOLO), EXPECTED_PROTOCOLO["auditados"])
        self.assertEqual(_count_qs(filtered_falhas(p), GRAIN_PROTOCOLO), EXPECTED_PROTOCOLO["falhas"])
        self.assertEqual(
            _eo_pct(EXPECTED_PROTOCOLO["auditados"], EXPECTED_PROTOCOLO["falhas"]),
            EXPECTED_PROTOCOLO["eo_pct"],
        )

    def test_03_reinspecao_improcedente_gera_falha(self):
        improc = AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX,
            tipo_falha="reinspecao",
            status="Improcedente",
        ).count()
        self.assertEqual(improc, 9)
        for src in AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX,
            tipo_falha="reinspecao",
            status="Improcedente",
        ):
            cls = classify_source_record(src)
            self.assertTrue(cls.is_auditado)
            self.assertTrue(cls.is_falha)

    def test_04_auditoria_compliance_respeita_resultado(self):
        rows = AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX,
            tipo_falha="auditoria",
        )
        self.assertEqual(rows.count(), 160)
        self.assertEqual(sum(classify_source_record(src).is_falha for src in rows), 1)

    def test_05_eixo_analise_dez_registros(self):
        p = _params(date_axis="analise", start_date="2026-08-04", end_date="2026-08-07")
        self.assertEqual(_count_qs(filtered_auditados(p), GRAIN_ETAPA), 10)
        self.assertEqual(_count_qs(filtered_falhas(p), GRAIN_ETAPA), 8)
        compliance = _params(date_axis="analise", start_date="2026-08-10", end_date="2026-08-10")
        self.assertEqual(_count_qs(filtered_auditados(compliance), GRAIN_ETAPA), 160)
        self.assertEqual(_count_qs(filtered_falhas(compliance), GRAIN_ETAPA), 1)

    def test_06_impacto_ponderado(self):
        fal = filtered_falhas(_params())
        impacto = _weighted_failure_total(fal, GRAIN_ETAPA)
        self.assertAlmostEqual(impacto, EXPECTED_IMPACTO_ETAPA, places=1)
        kpis = build_kpis(_params())
        self.assertAlmostEqual(kpis["impacto_ponderado"], EXPECTED_IMPACTO_ETAPA, places=1)
        self.assertEqual(kpis["eo_ponderado_pct"], EXPECTED_EO_PONDERADO_ETAPA)

        p = _params(grain=GRAIN_PROTOCOLO)
        impacto_p = _weighted_failure_total(filtered_falhas(p), GRAIN_PROTOCOLO)
        self.assertAlmostEqual(impacto_p, EXPECTED_IMPACTO_PROTOCOLO, places=1)
        kpis_p = build_kpis(p)
        self.assertEqual(kpis_p["eo_ponderado_pct"], EXPECTED_EO_PONDERADO_PROTOCOLO)

    def test_07_filtros_simetricos_tipo_registro(self):
        p = _params(tipo_registro="reinspecao")
        self.assertEqual(_count_qs(filtered_auditados(p), GRAIN_ETAPA), 327)
        self.assertEqual(_count_qs(filtered_falhas(p), GRAIN_ETAPA), 10)

    def test_08_filtro_procedencia_nao_informado(self):
        p = _params(procedencia=NAO_INFORMADO)
        self.assertEqual(_count_qs(filtered_auditados(p), GRAIN_ETAPA), 10)
        self.assertEqual(_count_qs(filtered_falhas(p), GRAIN_ETAPA), 8)

    def test_09_reclassificacao_reinspecao_atualiza_fato(self):
        src = AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX,
            tipo_falha="reinspecao",
            status="Procedente",
        ).first()
        sync_one(src, force=True)
        self.assertTrue(
            QualidadeIntranetProjection.objects.filter(source=src, falha_id__isnull=True).exists()
        )
        src.status = "Improcedente"
        src.save(update_fields=["status", "updated_at"])
        sync_one(src, force=True)
        proj = QualidadeIntranetProjection.objects.get(source=src)
        self.assertIsNotNone(proj.auditado_id)
        self.assertIsNotNone(proj.falha_id)

    def test_10_falha_sempre_com_auditado(self):
        for proj in QualidadeIntranetProjection.objects.filter(
            source__protocolo__startswith=SEED_PREFIX,
            falha_id__isnull=False,
        ):
            self.assertIsNotNone(proj.auditado_id)

    def test_11_catalogo_filtros_api(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        res = client.get("/api/v1/qualidade/operacional/filtros/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("tipos_registro", data)
        self.assertIn("origens_tratado", data)
        self.assertIn("tipos_falha_original", data)
        self.assertIn("procedencias", data)
        self.assertEqual(data.get("nao_informado_token"), NAO_INFORMADO)
