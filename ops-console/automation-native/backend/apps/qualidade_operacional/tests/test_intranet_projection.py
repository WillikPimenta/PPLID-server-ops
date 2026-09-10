# -*- coding: utf-8 -*-
"""Projeção Intranet → Qualidade Operacional (contrato EO)."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
from apps.auditoria.services.analise_origem import get_or_create_analise_origem
from apps.common.bot_db_sync_queue import claim_next_job, process_job
from apps.common.models import BotDbSyncJob
from apps.dimensoes_processos.models import DimCliente, DimWorkflow
from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeIntranetProjection,
)
from apps.qualidade_operacional.services.analytics import (
    GRAIN_ETAPA,
    IMPACT_WEIGHT_CUTOVER,
    _count_qs,
    _eo_pct,
    failure_weight,
    filtered_auditados,
    filtered_falhas,
)
from apps.qualidade_operacional.services.enrichment import (
    build_source_meta_for_auditados,
    serialize_auditado,
)
from apps.qualidade_operacional.services.case_key import (
    SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA,
    build_case_key_str,
)
from apps.qualidade_operacional.services.intranet_source import (
    SyncReport,
    apply_source_mode_filter,
    is_falha_efetiva,
    is_sem_falha,
    parse_flexible_date,
    resolve_audit_date,
    sync_one,
    to_sp_date,
)
from apps.qualidade_operacional.services.official_metric import OFFICIAL_METRIC_CUTOVER
from apps.qualidade_operacional.services.performance_cache import (
    _PAYLOAD_SCHEMA,
    bump_quality_cache_version,
)
from apps.qualidade_operacional.services.source_config import (
    INTRANET_SOURCE_FILE,
    clear_source_config_cache,
    tsv_competencia_blocked,
    tsv_date_blocked,
)
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()
TZ_SP = ZoneInfo("America/Sao_Paulo")


def _source(**kwargs) -> AuditoriaFalhaCadastro:
    defaults = {
        "protocolo": "PROT-INTRA-1",
        "tipo_falha": "Colaborador",
        "usuario": "c10001a",
        "origem": AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
        "tipo_registro": AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        "analise_status": AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
        "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        "analise_concluida_em": timezone.now(),
        "modulo": "G Auditoria",
        "motivo_falha": "Documento ilegível",
        "etapa_falha": "Análise",
        "brflow_parsed": {
            "data_analise": "2026-08-05",
            "cliente": "Cliente Alfa",
            "workflow": "WF Alfa",
            "tipo_conclusao": "Manual",
            "resultado_analise": "Aprovado",
        },
        "cliente": "Cliente Alfa",
        "uf_documento": "SP",
    }
    defaults.update(kwargs)
    return AuditoriaFalhaCadastro.objects.create(**defaults)


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=False,
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class IntranetProjectionCoreTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("intra_qo", password="x")
        DimCliente.objects.create(id_cliente=10, nome="Cliente Alfa")
        DimWorkflow.objects.create(id_workflow=20, nome="WF Alfa")
        AuditoriaMotivoFalha.objects.create(
            motivo="Documento ilegível",
            criticidade="Crítica",
            segmentos="Docs",
            subsegmento="Imagem",
        )
        self.agent = Agent.objects.create(
            full_name="Agente Um", user_lan_id="c10001a", active=True
        )
        self.leader = Agent.objects.create(
            full_name="Líder Um", user_lan_id="c20001a", active=True
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Site SP",
            active=True,
            start_date=date(2026, 1, 1),
        )

    def test_01_sem_falha_somente_auditado(self):
        src = _source(tipo_falha="Sem Falha", created_by=self.user)
        sync_one(src, force=True)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeFalha.objects.count(), 0)
        aud = QualidadeAuditado.objects.get()
        self.assertEqual(aud.source_file, INTRANET_SOURCE_FILE)
        self.assertEqual(aud.cadastrado_anteriormente, "Intranet")

    def test_02_colaborador_manual(self):
        src = _source(tipo_falha="Colaborador", created_by=self.user)
        sync_one(src, force=True)
        falha = QualidadeFalha.objects.get()
        self.assertEqual(falha.tipo_falha, "Manual")
        self.assertEqual(falha.tipo_falha_oficial, "Colaborador")

    def test_auditor_do_indicador_prioriza_agente_efetivamente_selecionado(self):
        auditor = User.objects.create_user(
            "c90001a", email="c90001a@test.local", password="x"
        )
        Agent.objects.create(full_name="Auditor Original", user_lan_id="c90001a", active=True)
        auditor_selecionado = Agent.objects.create(
            full_name="Auditor Selecionado", user_lan_id="c90002a", active=True
        )
        src = _source(
            protocolo="AUDITOR-FK-1",
            auditor="legado-incorreto",
            auditor_responsavel=auditor,
            auditor_ref=auditor_selecionado,
            created_by=self.user,
        )

        sync_one(src, force=True)

        self.assertEqual(
            QualidadeAuditado.objects.get(protocolo=src.protocolo).matricula_auditor,
            "c90002a",
        )
        self.assertEqual(
            QualidadeFalha.objects.get(protocolo=src.protocolo).usuario_auditor,
            "c90002a",
        )

    def test_duplicate_protocolo_matricula_skips_second_falha(self):
        first = _source(protocolo="DUP-1", usuario="c10001a", created_by=self.user)
        sync_one(first, force=True)
        self.assertEqual(QualidadeFalha.objects.count(), 1)
        self.assertEqual(
            QualidadeIntranetProjection.objects.get(source=first).sync_status,
            QualidadeIntranetProjection.STATUS_OK,
        )

        second = _source(
            protocolo="DUP-1",
            usuario="c10001a",
            created_by=self.user,
            motivo_falha="Outro motivo",
        )
        report = SyncReport()
        sync_one(second, force=True, report=report)
        self.assertEqual(QualidadeFalha.objects.count(), 1)
        projection = QualidadeIntranetProjection.objects.get(source=second)
        self.assertIsNone(projection.falha_id)
        self.assertEqual(projection.sync_status, QualidadeIntranetProjection.STATUS_SKIPPED)
        self.assertIn(SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA, projection.sync_error)
        self.assertEqual(
            report.by_skip_reason.get(SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA),
            1,
        )
        self.assertEqual(QualidadeAuditado.objects.filter(protocolo="DUP-1").count(), 2)

    def test_intranet_replaces_when_incoming_is_older(self):
        QualidadeFalha.objects.create(
            protocolo="DUP-3",
            matricula="c10001a",
            case_key="dup-3|c10001a",
            data=date(2026, 8, 15),
            source_file="newer.tsv",
        )
        src = _source(
            protocolo="DUP-3",
            usuario="c10001a",
            analise_concluida_em=timezone.make_aware(datetime(2026, 7, 1, 10, 0)),
            brflow_parsed={
                "data_analise": "2026-07-01",
                "cliente": "Cliente Alfa",
                "workflow": "WF Alfa",
                "tipo_conclusao": "Manual",
                "resultado_analise": "Aprovado",
            },
        )
        report = SyncReport()
        sync_one(src, force=True, report=report)
        self.assertEqual(QualidadeFalha.objects.filter(case_key="dup-3|c10001a").count(), 1)
        kept = QualidadeFalha.objects.get(case_key="dup-3|c10001a")
        self.assertEqual(kept.data, date(2026, 7, 1))
        self.assertEqual(report.falhas_replaced, 1)
        projection = QualidadeIntranetProjection.objects.get(source=src)
        self.assertEqual(projection.falha_id, kept.pk)
        self.assertEqual(projection.sync_status, QualidadeIntranetProjection.STATUS_OK)

    def test_intranet_skips_when_tsv_falha_is_older_and_keeps_auditado(self):
        tsv_falha = QualidadeFalha.objects.create(
            protocolo="DUP-TSV-INTRA",
            matricula="c10001a",
            case_key="dup-tsv-intra|c10001a",
            data=date(2026, 7, 1),
            source_file="falhas_julho.tsv",
        )
        src = _source(
            protocolo="DUP-TSV-INTRA",
            usuario="c10001a",
            analise_concluida_em=timezone.make_aware(datetime(2026, 8, 5, 10, 0)),
            brflow_parsed={
                "data_analise": "2026-08-05",
                "cliente": "Cliente Alfa",
                "workflow": "WF Alfa",
                "tipo_conclusao": "Manual",
                "resultado_analise": "Aprovado",
            },
        )
        report = SyncReport()

        sync_one(src, force=True, report=report)

        self.assertEqual(QualidadeFalha.objects.filter(case_key=tsv_falha.case_key).count(), 1)
        self.assertTrue(QualidadeFalha.objects.filter(pk=tsv_falha.pk).exists())
        self.assertEqual(QualidadeAuditado.objects.filter(protocolo="DUP-TSV-INTRA").count(), 1)
        projection = QualidadeIntranetProjection.objects.get(source=src)
        self.assertIsNone(projection.falha_id)
        self.assertEqual(projection.sync_status, QualidadeIntranetProjection.STATUS_SKIPPED)
        self.assertEqual(report.falhas_skipped_duplicate, 1)
        self.assertEqual(
            report.by_skip_reason.get(SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA),
            1,
        )

    def test_same_protocolo_different_operador_creates_two_falhas(self):
        first = _source(protocolo="DUP-2", usuario="c10001a", created_by=self.user)
        second = _source(protocolo="DUP-2", usuario="c20002a", created_by=self.user)
        sync_one(first, force=True)
        sync_one(second, force=True)
        self.assertEqual(QualidadeFalha.objects.count(), 2)
        keys = {
            build_case_key_str(row.protocolo, row.matricula)
            for row in QualidadeFalha.objects.all()
        }
        self.assertEqual(len(keys), 2)

    def test_resync_same_source_does_not_false_duplicate(self):
        src = _source(protocolo="DUP-3", usuario="c10001a", created_by=self.user)
        sync_one(src, force=True)
        falha_id = QualidadeFalha.objects.get().pk
        sync_one(src, force=True)
        self.assertEqual(QualidadeFalha.objects.get().pk, falha_id)
        self.assertEqual(
            QualidadeIntranetProjection.objects.get(source=src).sync_status,
            QualidadeIntranetProjection.STATUS_OK,
        )

    def test_sem_falha_nao_aplica_chave_composta(self):
        src = _source(tipo_falha="Sem Falha", created_by=self.user)
        sync_one(src, force=True)
        self.assertEqual(QualidadeFalha.objects.count(), 0)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)

    def test_03_automatico(self):
        src = _source(tipo_falha="Automático", created_by=self.user)
        sync_one(src, force=True)
        self.assertEqual(QualidadeFalha.objects.get().tipo_falha, "Automático")

    def test_04_processual(self):
        src = _source(tipo_falha="Processual", created_by=self.user)
        sync_one(src, force=True)
        falha = QualidadeFalha.objects.get()
        self.assertEqual(falha.tipo_falha, "Processual")
        self.assertEqual(falha.matricula, "")
        self.assertEqual(falha.lider, "")

    def test_05_mapeamento_sistema_automatico(self):
        for label in ("Mapeamento", "Sistema"):
            QualidadeAuditado.objects.all().delete()
            QualidadeFalha.objects.all().delete()
            QualidadeIntranetProjection.objects.all().delete()
            src = _source(protocolo=f"P-{label}", tipo_falha=label, created_by=self.user)
            sync_one(src, force=True)
            self.assertEqual(QualidadeFalha.objects.get().tipo_falha, "Automático")

    def test_06_07_ativa_e_mantida(self):
        QualidadeAuditado.objects.all().delete()
        QualidadeFalha.objects.all().delete()
        QualidadeIntranetProjection.objects.all().delete()
        src_ativa = _source(protocolo="ST-ativa", status_falha="ativa", created_by=self.user)
        sync_one(src_ativa, force=True)
        self.assertEqual(QualidadeFalha.objects.count(), 1)
        self.assertTrue(
            is_falha_efetiva(
                resultado_qualidade=src_ativa.resultado_qualidade,
                status_falha=src_ativa.status_falha,
            )
        )

        QualidadeAuditado.objects.all().delete()
        QualidadeFalha.objects.all().delete()
        QualidadeIntranetProjection.objects.all().delete()
        src_mantida = _source(protocolo="ST-mantida", status_falha="mantida", created_by=self.user)
        sync_one(src_mantida, force=True)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeFalha.objects.count(), 1)
        self.assertTrue(
            is_falha_efetiva(
                resultado_qualidade=src_mantida.resultado_qualidade,
                status_falha=src_mantida.status_falha,
            )
        )

    def test_08_retirada_remove_falha_mantem_auditado(self):
        src = _source(created_by=self.user)
        sync_one(src, force=True)
        self.assertEqual(QualidadeFalha.objects.count(), 1)
        AuditoriaFalhaCadastro.objects.filter(pk=src.pk).update(status_falha="retirada")
        src.refresh_from_db()
        self.assertEqual(
            src.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
        )
        sync_one(src)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeFalha.objects.count(), 0)

    def test_08b_status_vazio_remove_falha_mantem_auditado(self):
        src = _source(
            protocolo="ST-vazio",
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA,
            created_by=self.user,
        )
        sync_one(src, force=True)
        self.assertEqual(QualidadeFalha.objects.count(), 1)

        AuditoriaFalhaCadastro.objects.filter(pk=src.pk).update(status_falha="")
        src.refresh_from_db()
        self.assertEqual(
            src.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
        )
        projection = sync_one(src)

        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeFalha.objects.count(), 0)
        self.assertIn("status_falha_nao_contabilizavel", projection.warnings)

    def test_09_retirada_para_mantida_recria_falha(self):
        src = _source(status_falha="retirada", created_by=self.user)
        sync_one(src, force=True)
        self.assertEqual(QualidadeFalha.objects.count(), 0)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        aud_id = QualidadeAuditado.objects.get().pk
        src.status_falha = AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA
        src.save(update_fields=["status_falha", "updated_at"])
        sync_one(src, force=True)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeAuditado.objects.get().pk, aud_id)
        self.assertEqual(QualidadeFalha.objects.count(), 1)

    def test_10_reprocessamento_sem_duplicata(self):
        src = _source(created_by=self.user)
        sync_one(src, force=True)
        sync_one(src, force=True)
        report = SyncReport()
        sync_one(src, force=False, report=report)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeFalha.objects.count(), 1)
        self.assertEqual(report.unchanged, 1)

    def test_11_alteracao_atualiza_projecao(self):
        src = _source(created_by=self.user)
        sync_one(src, force=True)
        src.etapa_falha = "Conferência"
        src.save(update_fields=["etapa_falha", "updated_at"])
        sync_one(src, force=True)
        self.assertEqual(QualidadeAuditado.objects.get().etapa, "Conferência")
        self.assertEqual(QualidadeFalha.objects.get().etapa, "Conferência")

    def test_12_exclusao_origem_remove_projecoes(self):
        src = _source(created_by=self.user)
        sync_one(src, force=True)
        src_id = src.pk
        src.delete()
        self.assertFalse(
            QualidadeIntranetProjection.objects.filter(source_id=src_id).exists()
        )
        self.assertEqual(QualidadeAuditado.objects.count(), 0)
        self.assertEqual(QualidadeFalha.objects.count(), 0)

    def test_13_contestacao_reinspecao_projetam(self):
        for origem, tipo in (
            (AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO, AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO),
            (AuditoriaFalhaCadastro.ORIGEM_REINSPECAO, AuditoriaFalhaCadastro.REGISTRO_REINSPECAO),
        ):
            QualidadeAuditado.objects.all().delete()
            QualidadeFalha.objects.all().delete()
            QualidadeIntranetProjection.objects.all().delete()
            src = _source(
                protocolo=f"X-{origem}",
                origem=origem,
                tipo_registro=tipo,
                data_contestacao=timezone.now(),
                created_by=self.user,
            )
            sync_one(src, force=True)
            self.assertEqual(QualidadeAuditado.objects.count(), 1)

    def test_reinspecao_categoria_sempre_procedimento(self):
        QualidadeAuditado.objects.all().delete()
        QualidadeFalha.objects.all().delete()
        QualidadeIntranetProjection.objects.all().delete()
        src = _source(
            protocolo="REINS-CAT-1",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            tipo_falha="reinspecao",
            status="Improcedente",
            motivo_falha="Documento ilegível",
            descricao_irregularidades="IC - 650 - IMEI em inconformidade",
            data_contestacao=timezone.now(),
            created_by=self.user,
        )
        sync_one(src, force=True)
        falha = QualidadeFalha.objects.get(protocolo="REINS-CAT-1")
        self.assertEqual(falha.tipo_registro, "reinspecao")
        self.assertEqual(falha.categoria_falha, "Procedimento")

    def test_14_atividade_aberta_nao_bloqueia_projecao(self):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Aberta",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            created_by=self.user,
        )
        src = _source(atividade=atividade, created_by=self.user)
        report = SyncReport()
        sync_one(src, force=True, report=report)
        self.assertNotIn("atividade_aberta", report.by_skip_reason)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)

    def test_15_16_cliente_workflow_resolvido_e_ambiguo(self):
        src = _source(created_by=self.user)
        sync_one(src, force=True)
        aud = QualidadeAuditado.objects.get()
        self.assertEqual(aud.id_cliente, 10)
        self.assertEqual(aud.id_workflow, 20)

        DimCliente.objects.create(id_cliente=11, nome="Cliente Alfa")
        src2 = _source(protocolo="AMB-1", created_by=self.user)
        sync_one(src2, force=True)
        aud2 = QualidadeAuditado.objects.get(protocolo="AMB-1")
        self.assertIsNone(aud2.id_cliente)
        proj = QualidadeIntranetProjection.objects.get(auditado=aud2)
        self.assertTrue(any("cliente_ambiguo" in w for w in proj.warnings))

    def test_analise_origem_tem_prioridade_nos_campos_do_indicador(self):
        DimCliente.objects.create(id_cliente=30, nome="Cliente Origem")
        DimWorkflow.objects.create(id_workflow=40, nome="WF Origem")
        origem = get_or_create_analise_origem(
            protocolo="ORIGEM-CANONICA",
            brflow_parsed={
                "cliente": "Cliente Origem",
                "workflow": "WF Origem",
                "data_analise": "2026-08-07",
                "resultado_analise": "Resultado da origem",
                "tipo_conclusao": "Automático",
                "qualidade_imagem": "Boa",
            },
        )
        src = _source(
            protocolo="ORIGEM-CANONICA",
            analise_origem=origem,
            cliente="Cliente Alfa",
            resultado_cliente="Resultado legado",
            data_analise=timezone.make_aware(datetime(2026, 8, 9, 10, 0)),
            data_analise_intranet=timezone.make_aware(datetime(2026, 8, 10, 10, 0)),
            brflow_parsed={
                "cliente": "Cliente Alfa",
                "workflow": "WF Alfa",
                "data_analise": "2026-08-09",
                "resultado_analise": "Resultado legado",
                "tipo_conclusao": "Manual",
            },
            created_by=self.user,
        )

        sync_one(src, force=True)

        auditado = QualidadeAuditado.objects.get(protocolo="ORIGEM-CANONICA")
        falha = QualidadeFalha.objects.get(protocolo="ORIGEM-CANONICA")
        self.assertEqual(auditado.id_cliente, 30)
        self.assertEqual(auditado.id_workflow, 40)
        self.assertEqual(auditado.data_analise, date(2026, 8, 7))
        self.assertEqual(auditado.data_analise_intranet, date(2026, 8, 10))
        self.assertEqual(auditado.data_analise_origem, date(2026, 8, 7))
        self.assertEqual(auditado.resultado_origem, "Resultado da origem")
        self.assertEqual(auditado.tipo_conclusao, "Automático")
        self.assertEqual(falha.resultado_analise, "Resultado da origem")
        self.assertEqual(falha.qualidade_imagem, "Boa")

    def test_claro_confer_forca_dimensoes_em_auditoria_e_reinspecao(self):
        compliance_origin = get_or_create_analise_origem(
            protocolo="CLARO-AC",
            brflow_parsed={
                "cliente": "Claro",
                "fila_contexto": "auditoria_compliance",
                "data_analise": "2026-08-08",
            },
        )
        reinspecao_origin = get_or_create_analise_origem(
            protocolo="CLARO-R",
            brflow_parsed={
                "cliente": "Claro",
                "fila_contexto": "reinspecao",
            },
        )
        compliance = _source(
            protocolo="CLARO-AC",
            analise_origem=compliance_origin,
            cliente="Claro",
            tipo_falha="auditoria",
            status="Procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            data_analise=None,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.user,
        )
        reinspecao = _source(
            protocolo="CLARO-R",
            analise_origem=reinspecao_origin,
            cliente="Claro",
            tipo_falha="reinspecao",
            status="Improcedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            data_contestacao=timezone.now(),
            brflow_parsed={"fila_contexto": "reinspecao"},
            created_by=self.user,
        )

        sync_one(compliance, force=True)
        sync_one(reinspecao, force=True)

        auditados = QualidadeAuditado.objects.filter(
            protocolo__in=["CLARO-AC", "CLARO-R"]
        )
        self.assertEqual(auditados.count(), 2)
        self.assertFalse(
            auditados.exclude(
                id_cliente=83,
                id_workflow=450,
            ).exists()
        )
        for projection in QualidadeIntranetProjection.objects.filter(
            source__in=[compliance, reinspecao]
        ):
            self.assertFalse(
                any("cliente_nao_encontrado" in item for item in projection.warnings)
            )

    def test_17_18_motivo_e_desconhecido(self):
        src = _source(created_by=self.user)
        sync_one(src, force=True)
        falha = QualidadeFalha.objects.get()
        self.assertEqual(falha.categoria_falha, "Crítica")
        self.assertEqual(falha.segmento, "Docs")
        self.assertEqual(falha.sub_segmento, "Imagem")

        src2 = _source(protocolo="MOT-X", motivo_falha="Motivo inventado", created_by=self.user)
        sync_one(src2, force=True)
        falha2 = QualidadeFalha.objects.get(protocolo="MOT-X")
        self.assertEqual(falha2.categoria_falha, "")
        proj = QualidadeIntranetProjection.objects.get(falha=falha2)
        self.assertIn("motivo_desconhecido", proj.warnings)

    def test_19_agent_history_temporal(self):
        AgentHistory.objects.all().delete()
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Site Antigo",
            active=True,
            start_date=date(2025, 1, 1),
            final_date=date(2026, 6, 30),
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Site Novo",
            active=True,
            start_date=date(2026, 7, 1),
        )
        src = _source(
            analise_concluida_em=timezone.make_aware(datetime(2026, 8, 10, 12, 0)),
            created_by=self.user,
        )
        sync_one(src, force=True)
        falha = QualidadeFalha.objects.get()
        self.assertEqual(falha.localidade, "Site Novo")
        self.assertEqual(falha.localidade_documento, "SP")
        self.assertEqual(falha.lider, "Líder Um")
        auditado = QualidadeAuditado.objects.get()
        self.assertEqual(auditado.localidade_documento, "SP")

    def test_20_automatico_sem_matricula(self):
        src = _source(tipo_falha="Automático", usuario="", created_by=self.user)
        sync_one(src, force=True)
        aud = QualidadeAuditado.objects.get()
        self.assertEqual(aud.matricula, "")
        self.assertEqual(QualidadeFalha.objects.count(), 1)

    def test_21_22_23_datas(self):
        self.assertEqual(parse_flexible_date("2026-08-01"), date(2026, 8, 1))
        self.assertEqual(parse_flexible_date("01/08/2026"), date(2026, 8, 1))
        self.assertEqual(parse_flexible_date("2026-08-01T15:30:00"), date(2026, 8, 1))
        midnight_utc = datetime(2026, 8, 2, 3, 0, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(to_sp_date(midnight_utc), date(2026, 8, 2))
        src = _source(
            brflow_parsed={"data_analise": "não-é-data"},
            created_by=self.user,
        )
        sync_one(src, force=True)
        aud = QualidadeAuditado.objects.get()
        self.assertIsNotNone(aud.data_analise_intranet)
        self.assertIsNone(aud.data_analise)
        self.assertIsNone(aud.data_analise_origem)
        proj = QualidadeIntranetProjection.objects.get(auditado=aud)
        self.assertIn("data_analise_origem_invalida", proj.warnings)

    def test_preserva_todas_as_datas_sem_sobrescrever_analise_intranet(self):
        origem = get_or_create_analise_origem(
            protocolo="DATAS-INDEPENDENTES",
            brflow_raw="payload original preservado",
            brflow_parsed={
                "data_criacao": "01/08/2026 08:00",
                "data_analise": "03/08/2026 09:00",
                "data_conclusao": "04/08/2026 10:00",
                "cliente": "Cliente Alfa",
                "workflow": "WF Alfa",
            },
        )
        src = _source(
            protocolo="DATAS-INDEPENDENTES",
            analise_origem=origem,
            data_analise_intranet=timezone.make_aware(datetime(2026, 8, 8, 11, 0)),
            data_contestacao=timezone.make_aware(datetime(2026, 8, 5, 12, 0)),
            data_recepcao_contestacao=timezone.make_aware(datetime(2026, 8, 6, 12, 0)),
            data_encerramento_atividade_intranet=timezone.make_aware(
                datetime(2026, 8, 9, 13, 0)
            ),
            created_by=self.user,
        )

        sync_one(src, force=True)
        auditado = QualidadeAuditado.objects.get(protocolo="DATAS-INDEPENDENTES")

        self.assertEqual(auditado.data_analise, date(2026, 8, 3))
        self.assertEqual(auditado.data_analise_intranet, date(2026, 8, 8))
        self.assertEqual(auditado.data_analise_origem, date(2026, 8, 3))
        self.assertEqual(auditado.data_criacao_origem, date(2026, 8, 1))
        self.assertEqual(auditado.data_conclusao_origem, date(2026, 8, 4))
        self.assertEqual(auditado.data_recepcao_contestacao, date(2026, 8, 6))
        self.assertEqual(
            auditado.data_encerramento_atividade_intranet, date(2026, 8, 9)
        )
        origem.refresh_from_db()
        self.assertEqual(origem.brflow_raw, "payload original preservado")

    def test_datas_canonicas_por_origem(self):
        concluida = timezone.make_aware(datetime(2026, 8, 11, 10, 0))
        data_analise = timezone.make_aware(datetime(2026, 8, 8, 10, 0))
        data_contestacao = timezone.make_aware(datetime(2026, 8, 9, 10, 0))
        compliance = _source(
            protocolo="DATA-COMP",
            tipo_falha="auditoria",
            status="Improcedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            data_analise=data_analise,
            analise_concluida_em=concluida,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.user,
        )
        reinspecao = _source(
            protocolo="DATA-REIN",
            tipo_falha="reinspecao",
            status="Improcedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            data_contestacao=data_contestacao,
            analise_concluida_em=concluida,
            created_by=self.user,
        )
        self.assertEqual(resolve_audit_date(compliance), date(2026, 8, 11))
        self.assertEqual(resolve_audit_date(reinspecao), date(2026, 8, 11))

    def test_reinspecao_usa_analise_concluida_quando_contestacao_anterior(self):
        concluida = timezone.make_aware(datetime(2026, 8, 12, 6, 18))
        contestacao = timezone.make_aware(datetime(2026, 8, 11, 15, 0))
        src = _source(
            protocolo="REIN-CUTOVER",
            tipo_falha="reinspecao",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            data_contestacao=contestacao,
            analise_concluida_em=concluida,
            created_by=self.user,
        )
        self.assertEqual(resolve_audit_date(src), date(2026, 8, 12))

    def test_encerramento_da_atividade_nao_substitui_data_de_auditoria(self):
        encerramento = timezone.make_aware(datetime(2026, 8, 13, 18, 0))
        src = _source(
            protocolo="SEM-DATA-AUDITORIA",
            data_analise_intranet=None,
            analise_concluida_em=None,
            data_resposta=None,
            data_encerramento_atividade_intranet=encerramento,
            created_by=self.user,
        )

        self.assertIsNone(resolve_audit_date(src))

    def test_contestacao_data_analise_vem_do_brflow_raw_da_origem(self):
        origem = get_or_create_analise_origem(
            protocolo="CONTEST-RAW-DATE",
            brflow_raw=(
                "Protocolo: CONTEST-RAW-DATE Data de Criação: 02/04/2026 "
                "Data de Conclusão: 13/08/2026"
            ),
            brflow_parsed={"data_analise": "2026-08-13"},
        )
        contestacao = _source(
            protocolo="CONTEST-RAW-DATE",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            analise_origem=origem,
            data_analise=timezone.make_aware(datetime(2026, 8, 13, 12, 0)),
            data_contestacao=timezone.make_aware(datetime(2026, 8, 5, 12, 0)),
            created_by=self.user,
        )

        self.assertEqual(resolve_audit_date(contestacao), date(2026, 8, 5))
        sync_one(contestacao, force=True)
        self.assertEqual(
            QualidadeAuditado.objects.get(protocolo="CONTEST-RAW-DATE").data_analise,
            date(2026, 4, 2),
        )
        self.assertEqual(
            QualidadeFalha.objects.get(protocolo="CONTEST-RAW-DATE").data_analise,
            date(2026, 4, 2),
        )

    def test_contestacao_sem_data_no_brflow_raw_nao_usa_fallback(self):
        origem = get_or_create_analise_origem(
            protocolo="CONTEST-NO-RAW-DATE",
            brflow_raw="Protocolo: CONTEST-NO-RAW-DATE sem data válida",
            brflow_parsed={"data_analise": "2026-08-13"},
        )
        contestacao = _source(
            protocolo="CONTEST-NO-RAW-DATE",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            analise_origem=origem,
            data_analise=timezone.make_aware(datetime(2026, 8, 13, 12, 0)),
            data_contestacao=timezone.make_aware(datetime(2026, 8, 5, 12, 0)),
            created_by=self.user,
        )

        sync_one(contestacao, force=True)
        self.assertIsNone(
            QualidadeAuditado.objects.get(protocolo="CONTEST-NO-RAW-DATE").data_analise
        )

    def test_nao_classificado_nao_projeta_e_expoe_motivo(self):
        src = _source(
            protocolo="NAO-CLASSIFICADO",
            tipo_falha="auditoria",
            status="",
            created_by=self.user,
        )
        self.assertEqual(
            src.resultado_qualidade,
            AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO,
        )
        report = SyncReport()
        sync_one(src, force=True, report=report)
        self.assertIn("resultado_qualidade_nao_classificado", report.by_skip_reason)
        self.assertFalse(QualidadeIntranetProjection.objects.filter(source=src).exists())

    def test_sem_falha_normalizacao(self):
        self.assertTrue(is_sem_falha("  sem falha "))
        self.assertTrue(is_sem_falha("SEM FALHA"))
        self.assertFalse(is_sem_falha("Colaborador"))


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=False,
    QUALIDADE_SOURCE_MODE="hybrid",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class IntranetCutoverAndApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("cut_qo", "c@t.local", "x")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        DimCliente.objects.create(id_cliente=10, nome="Cliente Alfa")
        DimWorkflow.objects.create(id_workflow=20, nome="WF Alfa")

    def test_24_corte_temporal_sem_sobreposicao(self):
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 31),
            protocolo="TSV-31",
            source_file="tsv.tsv",
            tipo_conclusao="Manual",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 8, 1),
            protocolo="TSV-01",
            source_file="tsv.tsv",
            tipo_conclusao="Manual",
        )
        src = _source(
            protocolo="INTRA-01",
            analise_concluida_em=timezone.make_aware(datetime(2026, 8, 1, 10, 0)),
            created_by=self.user,
            tipo_falha="Sem Falha",
        )
        sync_one(src, force=True)
        # TSV de 01/08 não conta; Intranet de 01/08 conta; TSV 31/07 conta
        qs = apply_source_mode_filter(QualidadeAuditado.objects.all(), date_field="data")
        protocols = set(qs.values_list("protocolo", flat=True))
        self.assertIn("TSV-31", protocols)
        self.assertIn("INTRA-01", protocols)
        self.assertNotIn("TSV-01", protocols)
        self.assertTrue(tsv_date_blocked(date(2026, 8, 1)))
        self.assertFalse(tsv_date_blocked(date(2026, 7, 31)))
        # Mês do cutover (01/08): gate mensal aberto; linhas >= cutover bloqueadas por data.
        self.assertFalse(tsv_competencia_blocked(date(2026, 8, 1)))
        self.assertFalse(tsv_competencia_blocked(date(2026, 7, 1)))
        self.assertTrue(tsv_competencia_blocked(date(2026, 9, 1)))

    def test_24b_corte_parcial_no_mes_do_cutover(self):
        with override_settings(QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-08"):
            clear_source_config_cache()
            self.assertFalse(tsv_competencia_blocked(date(2026, 8, 1)))
            self.assertFalse(tsv_date_blocked(date(2026, 8, 7)))
            self.assertTrue(tsv_date_blocked(date(2026, 8, 8)))
            self.assertTrue(tsv_competencia_blocked(date(2026, 9, 1)))
            clear_source_config_cache()

    def test_25_26_grain_etapa_protocolo(self):
        for i in range(3):
            QualidadeAuditado.objects.create(
                data=date(2026, 8, 2),
                protocolo="MESMO",
                source_file=INTRANET_SOURCE_FILE,
                tipo_conclusao="Manual",
            )
        qs = QualidadeAuditado.objects.filter(protocolo="MESMO")
        self.assertEqual(_count_qs(qs, "etapa"), 3)
        self.assertEqual(_count_qs(qs, "protocolo"), 1)

    def test_27_28_29_regras_oficiais_preservadas(self):
        self.assertEqual(OFFICIAL_METRIC_CUTOVER, date(2026, 7, 1))
        self.assertEqual(IMPACT_WEIGHT_CUTOVER, date(2026, 8, 1))
        self.assertEqual(failure_weight({"data": date(2026, 7, 31)}), 1.0)

    def test_30_cache_schema_bump(self):
        self.assertIn("intranet", _PAYLOAD_SCHEMA)
        self.assertIn("analise-origem", _PAYLOAD_SCHEMA)
        v1 = bump_quality_cache_version()
        v2 = bump_quality_cache_version()
        self.assertNotEqual(v1, v2)

    def test_31_dry_run_sem_escrita(self):
        src = _source(tipo_falha="Sem Falha", created_by=self.user)
        sync_one(src, force=True, dry_run=True)
        self.assertEqual(QualidadeAuditado.objects.count(), 0)
        self.assertEqual(QualidadeIntranetProjection.objects.count(), 0)

    def test_32_backfill_idempotente(self):
        src = _source(tipo_falha="Sem Falha", created_by=self.user)
        sync_one(src, force=True)
        sync_one(src, force=True)
        self.assertEqual(QualidadeAuditado.objects.count(), 1)
        self.assertEqual(QualidadeIntranetProjection.objects.count(), 1)

    def test_33_sem_pii_no_payload(self):
        src = _source(
            tipo_falha="Sem Falha",
            brflow_parsed={
                "data_analise": "2026-08-05",
                "cliente": "Cliente Alfa",
                "workflow": "WF Alfa",
                "cpf": "12345678900",
                "brflow_raw_should_not_leak": "secret",
            },
            created_by=self.user,
        )
        sync_one(src, force=True)
        aud = QualidadeAuditado.objects.get()
        meta = build_source_meta_for_auditados([aud])
        payload = serialize_auditado(aud, {10: "Cliente Alfa"}, {20: "WF Alfa"}, {}, source_meta=meta[aud.pk])
        blob = str(payload)
        self.assertNotIn("12345678900", blob)
        self.assertNotIn("brflow", blob.lower())
        self.assertNotIn("secret", blob)
        self.assertEqual(payload["source_kind"], "intranet")
        self.assertEqual(payload["source_label"], "Intranet")

    def test_34_origem_api_meta(self):
        src = _source(tipo_falha="Sem Falha", created_by=self.user)
        sync_one(src, force=True)
        res = self.client.get("/api/v1/qualidade/operacional/meta/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("source", res.data)
        self.assertEqual(res.data["source"]["source_mode"], "hybrid")
        res2 = self.client.get("/api/v1/qualidade/operacional/auditados/?start_date=2026-08-01")
        self.assertEqual(res2.status_code, 200)
        row = res2.data["results"][0]
        self.assertEqual(row["source_kind"], "intranet")
        self.assertEqual(row["source_label"], "Intranet")

    def test_35_tsv_posterior_bloqueado(self):
        self.assertTrue(tsv_date_blocked(date(2026, 8, 15)))

    def test_36_restore_tsv_nao_toca_intranet(self):
        from apps.qualidade_operacional.services.monthly_import import _legacy_month_qs

        QualidadeAuditado.objects.create(
            data=date(2026, 7, 15),
            protocolo="TSV",
            source_file="a.tsv",
        )
        QualidadeAuditado.objects.create(
            data=date(2026, 7, 15),
            protocolo="INTRA",
            source_file=INTRANET_SOURCE_FILE,
        )
        qs = _legacy_month_qs(QualidadeAuditado, date(2026, 7, 1), date(2026, 8, 1))
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.get().protocolo, "TSV")


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=False,
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class IntranetStatusFalhaQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("status_qo", password="x")
        DimCliente.objects.create(id_cliente=10, nome="Cliente Alfa")
        DimWorkflow.objects.create(id_workflow=20, nome="WF Alfa")

    def _process_projection_job(self):
        claimed = claim_next_job(lane=BotDbSyncJob.LANE_MID)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.domain, BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION)
        process_job(claimed)

    def _metric_counts(self) -> tuple[int, int, float | None]:
        params = {"start_date": "2026-08-01", "end_date": "2026-08-31"}
        auditados = _count_qs(filtered_auditados(params), GRAIN_ETAPA)
        falhas = _count_qs(filtered_falhas(params), GRAIN_ETAPA)
        return auditados, falhas, _eo_pct(auditados, falhas)

    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker", return_value=True)
    def test_source_change_starts_worker_for_projection_lane(self, spawn_worker):
        with self.captureOnCommitCallbacks(execute=True):
            _source(protocolo="STATUS-LANE-1", created_by=self.user)

        spawn_worker.assert_called_once_with(lane=BotDbSyncJob.LANE_MID)

    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker", return_value=True)
    def test_status_falha_reprocessa_fila_e_corrige_indicador(self, _spawn):
        src = _source(protocolo="STATUS-EO-1", created_by=self.user)
        self._process_projection_job()
        projection = QualidadeIntranetProjection.objects.get(source=src)
        auditado_id = projection.auditado_id
        falha_id = projection.falha_id
        self.assertIsNotNone(falha_id)
        self.assertEqual(self._metric_counts(), (1, 1, 0.0))

        src.status_falha = ""
        src.save(update_fields=["status_falha", "updated_at"])
        self.assertEqual(src.resultado_qualidade, AuditoriaFalhaCadastro.RESULTADO_COM_FALHA)
        self._process_projection_job()
        projection.refresh_from_db()
        self.assertEqual(projection.auditado_id, auditado_id)
        self.assertIsNone(projection.falha_id)
        self.assertFalse(QualidadeFalha.objects.filter(pk=falha_id).exists())
        self.assertEqual(self._metric_counts(), (1, 0, 100.0))

        src.status_falha = AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA
        src.save(update_fields=["status_falha", "updated_at"])
        self._process_projection_job()
        projection.refresh_from_db()
        self.assertEqual(projection.auditado_id, auditado_id)
        self.assertIsNotNone(projection.falha_id)
        self.assertEqual(self._metric_counts(), (1, 1, 0.0))

        falha_mantida_id = projection.falha_id
        src.status_falha = AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA
        src.save(update_fields=["status_falha", "updated_at"])
        self._process_projection_job()
        projection.refresh_from_db()
        self.assertEqual(projection.auditado_id, auditado_id)
        self.assertIsNone(projection.falha_id)
        self.assertFalse(QualidadeFalha.objects.filter(pk=falha_mantida_id).exists())
        self.assertEqual(self._metric_counts(), (1, 0, 100.0))


@override_settings(
    ACCESS_ENFORCEMENT=False,
    QUALIDADE_INTRANET_SOURCE_ENABLED=True,
    QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED=False,
    QUALIDADE_SOURCE_MODE="intranet",
    QUALIDADE_INTRANET_CUTOVER_DATE="2026-08-01",
)
class IntranetFinalizarFluxoTests(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user("fin_qo", password="x")
        DimCliente.objects.create(id_cliente=10, nome="Cliente Alfa")
        DimWorkflow.objects.create(id_workflow=20, nome="WF Alfa")

    @patch("apps.common.bot_db_sync_queue.spawn_drain_worker", return_value=True)
    def test_finalizar_atividade_enfileira_e_projeta_fora_da_requisicao(self, _spawn):
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Aud EO",
            status=AuditoriaAtividade.STATUS_EM_ANDAMENTO,
            cliente="Cliente Alfa",
            workflow="WF Alfa",
            brflow_parsed={"trilha_raw": "Trilha de análise preenchida"},
            created_by=self.user,
        )
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo="FIN-1",
            usuario="c10001a",
            tipo_falha="Sem Falha",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            modulo="G Auditoria",
            brflow_parsed={"data_analise": "05/08/2026", "cliente": "Cliente Alfa", "workflow": "WF Alfa"},
            created_by=self.user,
        )
        QualidadePendenteAuditoriaFalha.objects.create(
            atividade=atividade,
            protocolo="FIN-2",
            usuario="c10002a",
            tipo_falha="Sem Falha",
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            modulo="G Auditoria",
            brflow_parsed={
                "data_analise": "05/08/2026",
                "cliente": "Cliente Alfa",
                "workflow": "WF Alfa",
            },
            created_by=self.user,
        )
        QualidadePendenteAuditoria.objects.create(
            atividade=atividade,
            protocolo="FIN-1",
            status=QualidadePendenteAuditoria.STATUS_EM_ANDAMENTO,
            created_by=self.user,
        )
        AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade, protocolo="FIN-1", excel_row=1
        )
        AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade, protocolo="FIN-2", excel_row=2
        )

        finalizar_atividade_auditoria(atividade, finalizador=self.user)
        tratado = AuditoriaFalhaCadastro.objects.get(protocolo="FIN-1")
        self.assertFalse(
            QualidadeIntranetProjection.objects.filter(source=tratado).exists()
        )
        job = BotDbSyncJob.objects.get(
            domain=BotDbSyncJob.DOMAIN_QUALIDADE_PROJECTION,
        )
        self.assertEqual(job.status, BotDbSyncJob.STATUS_PENDING)
        source_ids = {int(value) for value in job.source_path.split(",")}
        self.assertEqual(
            source_ids,
            set(atividade.tratados.values_list("pk", flat=True)),
        )

        claimed = claim_next_job(lane=BotDbSyncJob.LANE_MID)
        self.assertIsNotNone(claimed)
        process_job(claimed)

        self.assertTrue(
            QualidadeIntranetProjection.objects.filter(source=tratado).exists()
        )
        self.assertEqual(
            QualidadeIntranetProjection.objects.filter(
                source__atividade=atividade
            ).count(),
            2,
        )
        self.assertEqual(
            QualidadeAuditado.objects.filter(protocolo="FIN-1").count(), 1
        )
        self.assertEqual(QualidadeFalha.objects.filter(protocolo="FIN-1").count(), 0)
