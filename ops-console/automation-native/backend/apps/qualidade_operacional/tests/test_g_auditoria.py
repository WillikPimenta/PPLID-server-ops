# -*- coding: utf-8 -*-
from datetime import date

from django.test import TestCase, override_settings

from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeGAuditoriaFailureReconciliation,
    QualidadeGAuditoriaProjection,
)
from apps.qualidade_operacional.services.analytics import (
    failure_weight,
)
from apps.qualidade_operacional.services.g_auditoria import (
    project_g_auditoria_records,
    reconcile_failures,
    rollback_g_auditoria_projection,
)
from apps.qualidade_operacional.services.source_config import (
    G_AUDITORIA_SOURCE_FILE,
    INTRANET_SOURCE_FILE,
)
from apps.rotina_bruto.models import RotinaGAuditoriaRecord


def _staging(**updates):
    payload = {
        "report_date": date(2026, 8, 2),
        "source_file": "brflow-gauditoria_tratado_20260802.parquet",
        "source_key": "a" * 64,
        "source_record_key": "origem-1",
        "origin_transaction_id": "tx-1",
        "origin_transaction_code": "code-1",
        "protocolo_origem": "000123",
        "protocolo_normalizado": "000123",
        "protocolo_destino": "aud-1",
        "cliente_origem": "Cliente A",
        "workflow_origem": "Workflow A",
        "workflow_destino": "G Auditoria",
        "matricula_agente": "c100a",
        "matricula_auditor": "c200a",
        "etapa": "Análise Visual",
        "etapa_normalizada": "analise visual",
        "data_analise": date(2026, 8, 1),
        "data_auditoria": date(2026, 8, 2),
        "resultado_origem": "OK",
        "resultado_destino": "Risco",
        "status_destino": "Concluído",
        "tipo_conclusao_destino": "Manual",
        "dias_prazo": 1,
        "prazo_status": RotinaGAuditoriaRecord.PRAZO_DENTRO,
        "content_hash": "b" * 64,
        "is_active": True,
    }
    payload.update(updates)
    return RotinaGAuditoriaRecord.objects.create(**payload)


@override_settings(
    QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=True,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_SOURCE_MODE="intranet",
)
class GAuditoriaProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        DimCliente.objects.create(id_cliente=10, nome="Cliente A")
        DimWorkflow.objects.create(id_workflow=20, nome="Workflow A")

    def test_parquet_cria_auditado_sem_criar_falha(self):
        staging = _staging()
        metrics = project_g_auditoria_records(
            [staging], report_date=staging.report_date
        )
        self.assertEqual(metrics["rows_projected_created"], 1)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeFalha.objects.count(), 0)
        auditado = QualidadeAuditado.objects.get()
        self.assertEqual(auditado.data_analise, date(2026, 8, 1))
        self.assertIsNone(auditado.data_analise_intranet)
        self.assertEqual(auditado.data_analise_origem, date(2026, 8, 1))
        self.assertIsNone(auditado.data_encerramento_atividade_intranet)
        self.assertEqual(auditado.data, date(2026, 8, 2))
        self.assertEqual(auditado.id_cliente, 10)
        self.assertEqual(auditado.id_workflow, 20)
        self.assertEqual(auditado.tipo_analise, "G Auditoria")
        self.assertEqual(auditado.source_file, G_AUDITORIA_SOURCE_FILE)

    def test_reconciliacao_unica_atualiza_datas_e_preserva_falha(self):
        staging = _staging()
        project_g_auditoria_records([staging], report_date=staging.report_date)
        falha = QualidadeFalha.objects.create(
            protocolo="000123",
            etapa="ANÁLISE  VISUAL!",
            matricula="C100A",
            data_analise=date(2026, 7, 31),
            data=date(2026, 8, 3),
            source_file=INTRANET_SOURCE_FILE,
        )
        metrics = reconcile_failures(failure_ids=[falha.pk])
        falha.refresh_from_db()
        reconciliation = falha.g_auditoria_reconciliation
        self.assertEqual(metrics["failure_matches"], 1)
        self.assertEqual(falha.data_analise, date(2026, 8, 1))
        self.assertEqual(falha.data, date(2026, 8, 2))
        self.assertEqual(reconciliation.status, "matched")
        self.assertIn("data_analise", reconciliation.differences)
        self.assertEqual(QualidadeFalha.objects.count(), 1)

    def test_rollback_logico_restaura_intranet_e_desativa_projecao(self):
        staging = _staging()
        project_g_auditoria_records([staging], report_date=staging.report_date)
        falha = QualidadeFalha.objects.create(
            protocolo="000123",
            etapa="Análise Visual",
            matricula="agente-original",
            data_analise=date(2026, 7, 30),
            data=date(2026, 8, 3),
            source_file=INTRANET_SOURCE_FILE,
        )
        reconcile_failures(failure_ids=[falha.pk])

        metrics = rollback_g_auditoria_projection(bump_cache=False)

        falha.refresh_from_db()
        self.assertEqual(falha.data_analise, date(2026, 7, 30))
        self.assertEqual(falha.data, date(2026, 8, 3))
        self.assertEqual(falha.matricula, "agente-original")
        self.assertEqual(metrics["failures_restored"], 1)
        self.assertFalse(QualidadeGAuditoriaProjection.objects.get().is_active)
        self.assertEqual(QualidadeFalha.objects.count(), 1)

    def test_sem_match_e_ambiguo_continuam_contabilizados(self):
        staging1 = _staging()
        staging2 = _staging(
            source_key="c" * 64,
            source_record_key="origem-2",
            origin_transaction_id="tx-2",
            origin_transaction_code="code-2",
            protocolo_destino="aud-2",
            content_hash="d" * 64,
        )
        project_g_auditoria_records(
            [staging1, staging2], report_date=staging1.report_date
        )
        unmatched = QualidadeFalha.objects.create(
            protocolo="sem-match",
            etapa="Outra",
            source_file=INTRANET_SOURCE_FILE,
        )
        ambiguous = QualidadeFalha.objects.create(
            protocolo="000123",
            etapa="Análise Visual",
            source_file=INTRANET_SOURCE_FILE,
        )
        metrics = reconcile_failures(failure_ids=[unmatched.pk, ambiguous.pk])
        self.assertEqual(metrics["failure_unmatched"], 1)
        self.assertEqual(metrics["failure_ambiguous"], 1)
        self.assertEqual(QualidadeFalha.objects.count(), 2)
        self.assertEqual(
            QualidadeGAuditoriaFailureReconciliation.objects.get(
                falha=unmatched
            ).observation,
            "falha_nao_localizada_no_parquet",
        )

class EvaluativeScenarioWeightTests(TestCase):
    def test_corte_retroativo_em_quatro_de_janeiro(self):
        base = {"nivel_dificuldade": "Cenários Avaliativos", "id_cliente": 1}
        self.assertEqual(
            failure_weight({**base, "data": date(2026, 1, 3)}), 1.0
        )
        self.assertEqual(
            failure_weight({**base, "data": date(2026, 1, 4)}), 0.0
        )
        self.assertEqual(
            failure_weight(
                {
                    **base,
                    "nivel_dificuldade_confer": "CENÁRIOS AVALIATIVOS",
                    "data": date(2026, 7, 1),
                }
            ),
            0.0,
        )

    def test_outras_etapas_preservam_corte_geral(self):
        self.assertEqual(
            failure_weight(
                {
                    "etapa": "Outra",
                    "data": date(2026, 7, 1),
                    "categoria_falha": "Crítica",
                    "nivel_dificuldade": "Fácil",
                    "id_cliente": 1,
                }
            ),
            1.0,
        )
        self.assertEqual(
            failure_weight(
                {
                    "etapa": "Outra",
                    "data": date(2026, 8, 1),
                    "categoria_falha": "Crítica",
                    "nivel_dificuldade": "Fácil",
                    "id_cliente": 1,
                }
            ),
            3.5,
        )
