# -*- coding: utf-8 -*-
"""Cenário realista 110×7 — reconciliação, projeção e analytics EO."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import unittest

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaFalhaCadastro,
    AuditoriaMotivoFalha,
    QualidadePendenteAuditoria,
    QualidadePendenteAuditoriaFalha,
)
from apps.auditoria.services.atividade_falhas import finalizar_atividade_auditoria
from apps.auditoria.testing.qualidade_intranet_realistic_profile import (
    AGENTE_COM_MATRICULA,
    AUDITORIA_CANDIDATES,
    CLIENTE,
    CONTESTACAO_COUNT,
    EXPECTED_ANALISE_DATES,
    EXPECTED_AUDIT_DATES,
    EXPECTED_EO_ETAPA,
    EXPECTED_EO_PROTOCOLO,
    EXPECTED_IMPACTO_PONDERADO,
    EXPECTED_PROTOCOLOS_DISTINTOS,
    REINSPECAO_COUNT,
    SEED_PREFIX,
    TOTAL_ROWS,
    WORKFLOW,
    build_realistic_rows,
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
    failure_weight,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.intranet_source import (
    SyncReport,
    apply_source_mode_filter,
    build_cascade_reconciliation,
    eligible_sources_qs,
    sync_one,
    sync_queryset,
)
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()
TZ_SP = ZoneInfo("America/Sao_Paulo")


def _load_realistic_scenario(user) -> None:
    DimCliente.objects.get_or_create(id_cliente=9001, defaults={"nome": CLIENTE})
    DimWorkflow.objects.get_or_create(id_workflow=9001, defaults={"nome": WORKFLOW})
    Agent.objects.get_or_create(
        user_lan_id=AGENTE_COM_MATRICULA,
        defaults={"full_name": "Agente EO Simulado", "active": True},
    )
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
    for sample in build_realistic_rows():
        AuditoriaFalhaCadastro.objects.get_or_create(
            protocolo=sample["protocolo"],
            tipo_falha=sample["tipo_falha"],
            etapa_falha=sample.get("etapa_falha", ""),
            defaults={**sample, "created_by": user},
        )
    for candidate in AUDITORIA_CANDIDATES:
        for src in AuditoriaFalhaCadastro.objects.filter(protocolo=candidate["protocolo"]):
            if src.atividade_id:
                continue
            atividade = AuditoriaAtividade.objects.create(
                tipo=AuditoriaAtividade.TIPO_AUDITORIA,
                nome=f"Aud {src.protocolo}",
                status=AuditoriaAtividade.STATUS_CONCLUIDA,
                cliente=CLIENTE,
                workflow=WORKFLOW,
                encerrado_em=src.analise_concluida_em,
                created_by=user,
            )
            src.atividade = atividade
            src.save(update_fields=["atividade", "updated_at"])


def _params(**extra):
    base = {
        "start_date": "2026-07-30",
        "end_date": "2026-08-10",
        "metric_mode": "complete",
        "grain": GRAIN_ETAPA,
        "date_axis": "auditoria",
    }
    base.update(extra)
    return base


@unittest.skip("Obsoleto — substituído por test_intranet_acceptance_scenario (355×344).")
@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class IntranetRealisticScenarioTests(TestCase):
    """Perfil 110 registros → 7 elegíveis → 7 auditados + 5 falhas."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("realistic_qo", password="x")
        _load_realistic_scenario(cls.user)

    def setUp(self):
        QualidadeAuditado.objects.filter(source_file=INTRANET_SOURCE_FILE).delete()
        QualidadeFalha.objects.filter(source_file=INTRANET_SOURCE_FILE).delete()
        QualidadeIntranetProjection.objects.all().delete()
        sync_queryset(eligible_sources_qs().filter(protocolo__startswith=SEED_PREFIX))

    def test_01_cascade_reconcilia_entradas(self):
        cascade = build_cascade_reconciliation()
        self.assertEqual(cascade["total_concluido"], cascade["eligible_source_rows"])
        self.assertEqual(cascade["ignored_by_tipo_registro"], 0)
        self.assertEqual(cascade["unreconciled_difference"], 0)

    def test_02_todos_tipo_registro_elegiveis(self):
        cascade = build_cascade_reconciliation()
        self.assertGreaterEqual(cascade["eligible_source_rows"], 7)
        self.assertEqual(cascade["ignored_by_tipo_registro"], 0)
        self.assertEqual(cascade["ignored_by_origem"], 0)

    def test_03_sete_auditados_cinco_falhas(self):
        self.assertEqual(
            QualidadeAuditado.objects.filter(protocolo__startswith=SEED_PREFIX).count(), 7
        )
        self.assertEqual(
            QualidadeFalha.objects.filter(protocolo__startswith=SEED_PREFIX).count(), 5
        )

    def test_04_eo_etapa_286(self):
        params = _params(grain=GRAIN_ETAPA)
        aud = filtered_auditados(params)
        fal = filtered_falhas(params)
        auditados = _count_qs(aud, GRAIN_ETAPA)
        falhas = _count_qs(fal, GRAIN_ETAPA)
        self.assertEqual(auditados, 7)
        self.assertEqual(falhas, 5)
        self.assertEqual(_eo_pct(auditados, falhas), EXPECTED_EO_ETAPA)

    def test_05_eo_protocolo_0(self):
        params = _params(grain=GRAIN_PROTOCOLO)
        aud = filtered_auditados(params)
        fal = filtered_falhas(params)
        auditados = _count_qs(aud, GRAIN_PROTOCOLO)
        falhas = _count_qs(fal, GRAIN_PROTOCOLO)
        self.assertEqual(auditados, EXPECTED_PROTOCOLOS_DISTINTOS)
        self.assertEqual(falhas, EXPECTED_PROTOCOLOS_DISTINTOS)
        self.assertEqual(_eo_pct(auditados, falhas), EXPECTED_EO_PROTOCOLO)

    def test_06_impacto_ponderado_12(self):
        params = _params(grain=GRAIN_ETAPA)
        fal = filtered_falhas(params)
        impacto = _weighted_failure_total(fal, GRAIN_ETAPA)
        self.assertAlmostEqual(impacto, EXPECTED_IMPACTO_PONDERADO, places=1)
        self.assertEqual(_eo_pct(7, impacto), 0.0)

    def test_07_datas_eixo_auditoria(self):
        params = _params(date_axis="auditoria", grain=GRAIN_ETAPA)
        for day, expected in EXPECTED_AUDIT_DATES.items():
            day_params = _params(
                start_date=day.isoformat(),
                end_date=day.isoformat(),
                date_axis="auditoria",
            )
            aud = filtered_auditados(day_params)
            fal = filtered_falhas(day_params)
            self.assertEqual(_count_qs(aud, GRAIN_ETAPA), expected["auditados"], day)
            self.assertEqual(_count_qs(fal, GRAIN_ETAPA), expected["falhas"], day)

    def test_08_datas_eixo_analise(self):
        for day, expected in EXPECTED_ANALISE_DATES.items():
            day_params = _params(
                start_date=day.isoformat(),
                end_date=day.isoformat(),
                date_axis="analise",
            )
            aud = filtered_auditados(day_params)
            fal = filtered_falhas(day_params)
            self.assertEqual(_count_qs(aud, GRAIN_ETAPA), expected["auditados"], day)
            self.assertEqual(_count_qs(fal, GRAIN_ETAPA), expected["falhas"], day)

    def test_09_protocolo_duplicado(self):
        dup559 = QualidadeAuditado.objects.filter(
            protocolo__contains="55945472"
        ).count()
        dup152 = QualidadeAuditado.objects.filter(
            protocolo__contains="15203"
        ).count()
        self.assertEqual(dup559, 2)
        self.assertEqual(dup152, 2)
        params = _params(grain=GRAIN_PROTOCOLO)
        self.assertEqual(_count_qs(filtered_auditados(params), GRAIN_PROTOCOLO), 5)

    def test_10_sem_falha_somente_auditado(self):
        sem = AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX, tipo_falha="Sem Falha"
        )
        self.assertEqual(sem.count(), 2)
        for src in sem:
            proj = QualidadeIntranetProjection.objects.get(source=src)
            self.assertIsNotNone(proj.auditado_id)
            self.assertIsNone(proj.falha_id)

    def test_11_automatico_ativa_gera_falha(self):
        auto = AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX,
            tipo_falha="Automático",
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        )
        self.assertEqual(auto.count(), 5)
        for src in auto:
            self.assertTrue(
                QualidadeAuditado.objects.filter(protocolo=src.protocolo).exists()
            )
            self.assertTrue(
                QualidadeFalha.objects.filter(protocolo=src.protocolo).exists()
            )

    def test_12_matricula_vazia_total_geral_nao_agente(self):
        params = _params(grain=GRAIN_ETAPA)
        self.assertEqual(_count_qs(filtered_auditados(params), GRAIN_ETAPA), 7)
        params_agente = _params(grain=GRAIN_ETAPA, matricula=AGENTE_COM_MATRICULA)
        aud_agente = _count_qs(filtered_auditados(params_agente), GRAIN_ETAPA)
        self.assertEqual(aud_agente, 2)

    def test_13_atividade_aberta_nao_projeta(self):
        src = AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        ).first()
        atividade = src.atividade
        atividade.status = AuditoriaAtividade.STATUS_EM_ANDAMENTO
        atividade.save(update_fields=["status", "updated_at"])
        QualidadeIntranetProjection.objects.filter(source=src).delete()
        report = SyncReport()
        sync_one(src, report=report)
        self.assertIn("atividade_aberta", report.by_skip_reason)
        self.assertFalse(QualidadeIntranetProjection.objects.filter(source=src).exists())

    def test_14_reinspecao_contestacao_projetam(self):
        for origem in (
            AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
        ):
            self.assertTrue(
                QualidadeIntranetProjection.objects.filter(
                    source__origem=origem, source__protocolo__startswith=SEED_PREFIX
                ).exists()
                or QualidadeIntranetProjection.objects.filter(source__origem=origem).exists()
            )

    def test_15_legacy_oculta_intranet(self):
        qs = QualidadeAuditado.objects.filter(source_file=INTRANET_SOURCE_FILE)
        with override_settings(QUALIDADE_SOURCE_MODE="legacy"):
            filtered = apply_source_mode_filter(qs, date_field="data")
            self.assertEqual(filtered.count(), 0)

    def test_16_hybrid_corte_inclusivo_01_08(self):
        with override_settings(QUALIDADE_SOURCE_MODE="hybrid"):
            cascade = build_cascade_reconciliation(prefix=SEED_PREFIX)
        self.assertEqual(cascade["outside_cutover_rows"], 0)
        self.assertEqual(cascade["eligible_source_rows"], 7)

    def test_17_backfill_idempotente(self):
        report1 = sync_queryset(
            eligible_sources_qs().filter(protocolo__startswith=SEED_PREFIX)
        )
        report2 = sync_queryset(
            eligible_sources_qs().filter(protocolo__startswith=SEED_PREFIX)
        )
        self.assertEqual(QualidadeAuditado.objects.filter(protocolo__startswith=SEED_PREFIX).count(), 7)
        self.assertGreaterEqual(report2.unchanged, 7)

    def test_18_cache_invalidado_apos_sync(self):
        before = bump_quality_cache_version()
        src = AuditoriaFalhaCadastro.objects.filter(
            protocolo__startswith=SEED_PREFIX,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        ).first()
        after = sync_one(src, bump_cache=True)
        self.assertIsNotNone(after)
        self.assertGreater(bump_quality_cache_version(), before)

    def test_19_endpoint_kpis_totais(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        res = client.get(
            "/api/v1/qualidade/operacional/kpis/",
            _params(start_date="2026-08-01", end_date="2026-08-10"),
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["auditados"], 7)
        self.assertEqual(res.data["falhas"], 5)
        self.assertEqual(res.data["eo_pct"], EXPECTED_EO_ETAPA)

    def test_20_grain_date_axis_preservam_totais(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        base = _params(start_date="2026-07-30", end_date="2026-08-10")
        for grain in (GRAIN_ETAPA, GRAIN_PROTOCOLO):
            for axis in ("auditoria", "analise"):
                params = {**base, "grain": grain, "date_axis": axis}
                res = client.get("/api/v1/qualidade/operacional/kpis/", params)
                self.assertEqual(res.status_code, 200, params)
                if grain == GRAIN_ETAPA:
                    self.assertEqual(res.data["auditados"], 7, params)
                    self.assertEqual(res.data["falhas"], 5, params)
                else:
                    self.assertEqual(res.data["auditados"], 5, params)
                    self.assertEqual(res.data["falhas"], 5, params)

    def test_pesos_por_protocolo(self):
        weights = []
        for candidate in AUDITORIA_CANDIDATES:
            expected = candidate.get("_expected_weight")
            if expected is None:
                continue
            falha = QualidadeFalha.objects.filter(protocolo=candidate["protocolo"]).first()
            self.assertIsNotNone(falha, candidate["protocolo"])
            weights.append(failure_weight(falha))
        self.assertAlmostEqual(sum(weights), EXPECTED_IMPACTO_PONDERADO, places=1)


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
    QUALIDADE_PROJECTION_ASYNC_ENABLED=False,
)
class IntranetRealisticFinalizarFluxoTests(TransactionTestCase):
    """Finalização real → tratado → projeção pós-commit (sem sync_one manual)."""

    def setUp(self):
        self.user = User.objects.create_user("real_fin_qo", password="x")
        DimCliente.objects.create(id_cliente=9001, nome=CLIENTE)
        DimWorkflow.objects.create(id_workflow=9001, nome=WORKFLOW)
        for row in motivo_catalog_rows():
            AuditoriaMotivoFalha.objects.create(
                motivo=row["motivo"],
                criticidade=row["criticidade"],
                segmentos=row["segmentos"],
                subsegmento=row["subsegmento"],
                active=True,
            )

    def test_finalizar_atividade_sincroniza_pos_commit_sem_sync_manual(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Aud fluxo real",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            cliente=CLIENTE,
            workflow=WORKFLOW,
            brflow_parsed={"trilha_raw": "Trilha de análise preenchida"},
            created_by=self.user,
        )
        candidate = AUDITORIA_CANDIDATES[0]
        protocolo = candidate["protocolo"]
        brflow = candidate["brflow_parsed"]
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo=protocolo,
            usuario=candidate.get("usuario", ""),
            tipo_falha=candidate["tipo_falha"],
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            modulo=candidate["modulo"],
            motivo_falha=candidate.get("motivo_falha", ""),
            etapa_falha=candidate.get("etapa_falha", ""),
            nivel_dificuldade=candidate.get("nivel_dificuldade", ""),
            resultado_cliente=candidate.get("resultado_cliente", ""),
            novo_resultado=candidate.get("novo_resultado", ""),
            sinalizacao="Não",
            tipo_documento=candidate.get("tipo_documento", ""),
            uf_documento=candidate.get("uf_documento", ""),
            qualidade_imagem=candidate.get("qualidade_imagem", ""),
            brflow_parsed=brflow,
            created_by=self.user,
        )
        QualidadePendenteAuditoria.objects.create(
            atividade=atividade,
            protocolo=protocolo,
            status=QualidadePendenteAuditoria.STATUS_EM_ANDAMENTO,
            created_by=self.user,
        )
        AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade, protocolo=protocolo, excel_row=1
        )

        finalizar_atividade_auditoria(atividade, finalizador=self.user)
        tratado = AuditoriaFalhaCadastro.objects.get(protocolo=protocolo)
        self.assertTrue(
            QualidadeIntranetProjection.objects.filter(source=tratado).exists()
        )
        self.assertEqual(
            QualidadeAuditado.objects.filter(protocolo=protocolo).count(), 1
        )
