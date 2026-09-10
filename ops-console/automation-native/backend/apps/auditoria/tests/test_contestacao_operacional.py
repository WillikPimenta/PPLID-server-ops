from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_OP_GERENCIA,
    ROLE_OP_LIDER,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_FRAUD,
    ROLE_QUAL_GERENCIA,
    role_group_name,
)
from apps.access.resolve import ensure_role_groups_exist
from apps.auditoria.models import (
    AuditoriaCatalogItem,
    AuditoriaFalhaCadastro,
    AuditoriaMotivoFalha,
    ContestacaoOperacional,
    ContestacaoOperacionalHistorico,
    QualidadeAnaliseOrigem,
    QualidadePendenteAuditoriaCompliance,
)
from apps.auditoria.services import contestacao_operacional as svc
from apps.auditoria.services.qualidade_promocao import promover_pendente_auditoria_compliance
from apps.auditoria.services.contestacao_operacional_scoping import contestacao_scope_for_user
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True)
class ContestacaoOperacionalServiceTests(TestCase):
    def setUp(self):
        ensure_role_groups_exist()
        self.lider = User.objects.create_user("lider.op", email="lider.op@test.local", password="x")
        self.analista_fraud = User.objects.create_user(
            "analista.fraud", email="analista.fraud@test.local", password="x"
        )
        self.analista_comp = User.objects.create_user(
            "analista.comp", email="analista.comp@test.local", password="x"
        )
        self.auditor_original = User.objects.create_user(
            "auditor.original", email="auditor.original@test.local", password="x"
        )
        self.lider.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_LIDER)))
        self.analista_fraud.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_CONTESTACAO_FRAUD))
        )
        self.analista_comp.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_CONTESTACAO_COMPLIANCE))
        )
        # Recarrega para descartar caches de permissão eventualmente populados por signals.
        self.lider = User.objects.get(pk=self.lider.pk)
        self.analista_fraud = User.objects.get(pk=self.analista_fraud.pk)
        self.analista_comp = User.objects.get(pk=self.analista_comp.pk)

        AuditoriaMotivoFalha.objects.get_or_create(
            motivo="Cenário revisado",
            defaults={"criticidade": "Crítica", "segmentos": "Todos", "subsegmento": "Todos"},
        )
        AuditoriaCatalogItem.objects.get_or_create(
            catalog=AuditoriaCatalogItem.CATALOG_NIVEL_DIFICULDADE,
            value="N2",
            defaults={"label": "N2", "active": True},
        )
        AuditoriaCatalogItem.objects.get_or_create(
            catalog=AuditoriaCatalogItem.CATALOG_ETAPA_FALHA,
            value="Validação documental",
            defaults={"label": "Validação documental", "active": True},
        )

        self.leader_agent = Agent.objects.create(full_name="Lider", user_lan_id="lider.op")
        self.agent = Agent.objects.create(full_name="Agente A", user_lan_id="agente.a")
        self.other = Agent.objects.create(full_name="Agente B", user_lan_id="agente.b")
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader_agent,
            active=True,
            start_date=timezone.now().date(),
        )
        AgentHistory.objects.create(
            agent=self.other,
            leader=None,
            active=True,
            start_date=timezone.now().date(),
        )

        now = timezone.now()
        self.falha_team = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-TEAM-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            created_by=self.lider,
            auditor_responsavel=self.auditor_original,
        )
        self.falha_other = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-OTHER-1",
            tipo_falha="critica",
            usuario="agente.b",
            agente_ref=self.other,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            created_by=self.lider,
            auditor_responsavel=self.auditor_original,
        )
        self.falha_reinspecao = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-REIN-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            status="Improcedente",
            brflow_parsed={"situacao": "improcedente"},
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=now - timezone.timedelta(days=20),
            data_analise_intranet=now,
            analise_concluida_em=now,
            created_by=self.lider,
            auditor_responsavel=self.auditor_original,
        )

    def test_leader_sees_only_team_falhas_in_indicator(self):
        now = timezone.now()
        sem_falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-SEM-FALHA",
            tipo_falha="Sem Falha",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            created_by=self.lider,
        )
        contestacao_improcedente = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-CONT-IMPROCEDENTE",
            tipo_falha="Colaborador",
            usuario="agente.a",
            agente_ref=self.agent,
            status="improcedente",
            brflow_parsed={"situacao": "improcedente"},
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            created_by=self.lider,
        )
        reinspecao_procedente = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-REIN-PROCEDENTE",
            tipo_falha="reinspecao",
            usuario="agente.a",
            agente_ref=self.agent,
            status="Procedente",
            brflow_parsed={"situacao": "procedente"},
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=now,
            created_by=self.lider,
        )
        falha_retirada = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-RETIRADA",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            created_by=self.lider,
        )
        finalizada_sem_contestacao = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-FINAL-ORFA",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            created_by=self.lider,
        )
        sem_agente = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-SEM-AGENTE",
            tipo_falha="Manual",
            usuario="agente.a",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            created_by=self.lider,
        )
        AuditoriaFalhaCadastro.objects.filter(pk=falha_retirada.pk).update(
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA
        )

        qs = svc.falhas_queryset_for_user(self.lider)
        ids = set(qs.values_list("id", flat=True))
        self.assertIn(self.falha_team.id, ids)
        self.assertIn(self.falha_reinspecao.id, ids)
        self.assertNotIn(self.falha_other.id, ids)
        self.assertNotIn(sem_falha.id, ids)
        self.assertNotIn(contestacao_improcedente.id, ids)
        self.assertNotIn(reinspecao_procedente.id, ids)
        self.assertNotIn(falha_retirada.id, ids)
        self.assertNotIn(finalizada_sem_contestacao.id, ids)
        self.assertNotIn(sem_agente.id, ids)

    def test_gerencia_global_scope_sees_all_team_falhas(self):
        gerencia = User.objects.create_user(
            "gerencia.op", email="gerencia.op@test.local", password="x"
        )
        gerencia.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_GERENCIA)))
        gerencia = User.objects.get(pk=gerencia.pk)

        ids = set(svc.falhas_queryset_for_user(gerencia).values_list("id", flat=True))
        self.assertIn(self.falha_team.id, ids)
        self.assertIn(self.falha_other.id, ids)
        self.assertEqual(contestacao_scope_for_user(gerencia), "global")

    def test_filter_by_categoria_and_protocolo(self):
        qs = svc.filter_falhas(
            svc.falhas_queryset_for_user(self.lider),
            protocolo="TEAM",
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
        )
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().id, self.falha_team.id)

    def test_serialize_falha_exposes_data_auditoria_by_origin(self):
        auditoria = svc.serialize_falha(self.falha_team)
        reinspecao = svc.serialize_falha(self.falha_reinspecao)

        self.assertEqual(
            auditoria["data_auditoria"],
            self.falha_team.analise_concluida_em.isoformat(),
        )
        self.assertEqual(
            reinspecao["data_auditoria"],
            self.falha_reinspecao.data_analise_intranet.isoformat(),
        )

    def test_serialize_protocol_group_aggregates_irregularidades(self):
        now = timezone.now()
        first = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-TEAM-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            motivo_falha="Cenário A",
            brflow_parsed={"cenario": "Cenário A"},
            created_by=self.lider,
        )
        second = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-TEAM-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=now,
            motivo_falha="Cenário B",
            brflow_parsed={"cenario": "Cenário B"},
            created_by=self.lider,
        )

        serialized = svc.serialize_falha(first)
        self.assertEqual(serialized["irregularidades"], ["Cenário A", "Cenário B"])
        self.assertEqual(
            serialized["irregularidades_itens"],
            [
                {
                    "falha_id": first.id,
                    "texto": "Cenário A",
                    "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                },
                {
                    "falha_id": second.id,
                    "texto": "Cenário B",
                    "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                },
            ],
        )
        self.assertEqual(set(serialized["falha_ids"]), {first.id, second.id, self.falha_team.id})

        qs = svc.falhas_queryset_for_user(self.lider).filter(protocolo="PROT-TEAM-1")
        groups, total = svc.paginate_falha_protocol_groups(qs, page=1, page_size=25)
        self.assertEqual(total, 1)
        merged = svc.serialize_protocol_groups(groups)
        self.assertEqual(len(merged), 1)
        self.assertGreaterEqual(len(merged[0]["irregularidades"]), 2)

    def test_reinspecao_uses_completion_as_audit_date_fallback(self):
        self.falha_reinspecao.data_analise_intranet = None
        self.falha_reinspecao.save(update_fields=["data_analise_intranet"])

        serialized = svc.serialize_falha(self.falha_reinspecao)

        self.assertEqual(
            serialized["data_auditoria"],
            self.falha_reinspecao.analise_concluida_em.isoformat(),
        )

    def test_auditoria_compliance_is_isolated_from_fraud_and_infers_category(self):
        now = timezone.now()
        falha_compliance = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-COMP-1",
            tipo_falha="auditoria",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            status="Procedente",
            data_analise=now,
            analise_concluida_em=now - timezone.timedelta(days=30),
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.lider,
        )

        base_qs = svc.falhas_queryset_for_user(self.lider)
        fraud_ids = set(
            svc.filter_falhas(
                base_qs,
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
            ).values_list("id", flat=True)
        )
        compliance_ids = set(
            svc.filter_falhas(
                base_qs,
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
            ).values_list("id", flat=True)
        )

        self.assertNotIn(falha_compliance.id, fraud_ids)
        self.assertEqual(compliance_ids, {falha_compliance.id})
        serialized = svc.serialize_falha(falha_compliance)
        self.assertEqual(
            serialized["categoria"],
            ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
        )
        self.assertEqual(serialized["dominio"], ContestacaoOperacional.DOMINIO_COMPLIANCE)
        self.assertEqual(serialized["data_auditoria"], now.isoformat())
        periodo_ids = set(
            svc.filter_falhas(
                base_qs,
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
                data_inicio=now - timezone.timedelta(days=1),
                data_fim=now + timezone.timedelta(days=1),
            ).values_list("id", flat=True)
        )
        self.assertEqual(periodo_ids, {falha_compliance.id})

        contestacao = svc.criar_contestacao(
            user=self.lider,
            falha_id=falha_compliance.id,
            justificativa="Reavaliar falha de compliance",
        )
        self.assertEqual(
            contestacao.categoria,
            ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
        )
        self.assertEqual(contestacao.dominio, ContestacaoOperacional.DOMINIO_COMPLIANCE)

    def test_promoted_auditoria_compliance_procedente_appears_for_contestacao(self):
        now = timezone.now()
        pendente = QualidadePendenteAuditoriaCompliance.objects.create(
            protocolo="PROT-COMP-PROMOVIDO",
            usuario="agente.a",
            tipo_falha="Colaborador",
            descricao_irregularidades="Selfie ilegível",
            data_analise=now,
            status="Procedente",
            analise_concluida_em=now,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.lider,
        )
        falha = promover_pendente_auditoria_compliance(
            pendente,
            finalizador=self.lider,
        )

        self.assertEqual(falha.tipo_registro, AuditoriaFalhaCadastro.REGISTRO_AUDITORIA)
        self.assertEqual(falha.origem, AuditoriaFalhaCadastro.ORIGEM_AUDITORIA)
        self.assertEqual(
            falha.brflow_parsed.get("fila_contexto"),
            "auditoria_compliance",
        )

        base_qs = svc.falhas_queryset_for_user(self.lider)
        compliance_ids = set(
            svc.filter_falhas(
                base_qs,
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
            ).values_list("id", flat=True)
        )
        self.assertIn(falha.id, compliance_ids)

    def test_auditoria_compliance_serializes_irregularidades_from_motivo_falha(self):
        now = timezone.now()
        first = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-COMP-IRR",
            tipo_falha="Colaborador",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            status="Procedente",
            data_analise=now,
            analise_concluida_em=now,
            descricao_irregularidades="Reclassificação",
            motivo_falha="Selfie ilegível",
            etapa_falha="Apontada incorretamente",
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.lider,
        )
        second = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-COMP-IRR",
            tipo_falha="Colaborador",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            status="Procedente",
            data_analise=now,
            analise_concluida_em=now,
            descricao_irregularidades="Reclassificação",
            motivo_falha="Documento vencido",
            etapa_falha="Não apontada",
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.lider,
        )

        serialized = svc.serialize_falha(first)

        self.assertEqual(serialized["irregularidades"], ["Selfie ilegível", "Documento vencido"])
        self.assertEqual(
            serialized["irregularidades_itens"],
            [
                {
                    "falha_id": first.id,
                    "texto": "Selfie ilegível",
                    "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                    "status_irregularidade": "Apontada incorretamente",
                },
                {
                    "falha_id": second.id,
                    "texto": "Documento vencido",
                    "status_falha": AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
                    "status_irregularidade": "Não apontada",
                },
            ],
        )
        self.assertEqual(serialized["descricao_irregularidades"], "Reclassificação")

    def test_auditoria_compliance_procedente_com_tipo_legado_reinspecao_e_elegivel(self):
        """Regressao: o contexto estruturado prevalece sobre tipo_registro legado."""
        now = timezone.now()
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="39844417",
            tipo_falha="Documento de Identificacao",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            status="Procedente",
            data_analise=now,
            analise_concluida_em=now,
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            created_by=self.lider,
        )

        base_qs = svc.falhas_queryset_for_user(self.lider)
        compliance_ids = set(
            svc.filter_falhas(
                base_qs,
                protocolo="39844417",
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
            ).values_list("id", flat=True)
        )

        self.assertEqual(compliance_ids, {falha.id})
        self.assertEqual(
            svc.resolve_categoria_falha(falha),
            ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
        )

    def test_mesmo_protocolo_avalia_cada_linha_e_retorna_somente_com_falha(self):
        now = timezone.now()
        sem_falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="39846153",
            tipo_falha="Sem Falha",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            status="Procedente",
            data_analise_intranet=now,
            analise_concluida_em=now,
            brflow_parsed={
                "fila_contexto": "reinspecao",
                "homolog_import_source": "consolidado_contestacao_auditoria.xlsx",
                "homolog_import_row_id": "consolidado-row-6894",
            },
            created_by=self.lider,
        )
        com_falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="39846153",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            status="Improcedente",
            data_analise_intranet=now,
            analise_concluida_em=now,
            brflow_parsed={
                "fila_contexto": "reinspecao",
                "homolog_import_source": "consolidado_contestacao_auditoria.xlsx",
                "homolog_import_row_id": "consolidado-row-7303",
            },
            created_by=self.lider,
        )

        ids = set(
            svc.filter_falhas(
                svc.falhas_queryset_for_user(self.lider),
                protocolo="39846153",
            ).values_list("id", flat=True)
        )

        self.assertEqual(ids, {com_falha.id})
        self.assertNotIn(sem_falha.id, ids)
        contestacao = svc.criar_contestacao(
            user=self.lider,
            falha_id=com_falha.id,
            justificativa="Contestar a linha com falha",
        )
        self.assertEqual(contestacao.falha_id, com_falha.id)

    def test_auditoria_compliance_uses_analise_origem_for_category_date_and_period(self):
        now = timezone.now()
        origem = QualidadeAnaliseOrigem.objects.create(
            protocolo="PROT-COMP-ORIGEM",
            contexto={"fila_contexto": "auditoria_compliance"},
            conteudo_hash="contestacao-compliance-origem",
        )
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-COMP-ORIGEM",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_analise=now,
            analise_concluida_em=now - timezone.timedelta(days=30),
            brflow_parsed={},
            analise_origem=origem,
            created_by=self.lider,
        )

        base_qs = svc.falhas_queryset_for_user(self.lider)
        fraud_ids = set(
            svc.filter_falhas(
                base_qs,
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
            ).values_list("id", flat=True)
        )
        compliance_ids = set(
            svc.filter_falhas(
                base_qs,
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
                data_inicio=now - timezone.timedelta(days=1),
                data_fim=now + timezone.timedelta(days=1),
            ).values_list("id", flat=True)
        )

        self.assertNotIn(falha.id, fraud_ids)
        self.assertIn(falha.id, compliance_ids)
        self.assertEqual(svc.data_auditoria_for_falha(falha), now)
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=falha.id,
            justificativa="Reavaliar auditoria compliance",
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
        )
        self.assertEqual(item.dominio, ContestacaoOperacional.DOMINIO_COMPLIANCE)

    def test_criar_rejeita_categoria_informada_divergente_da_origem_canonica(self):
        with self.assertRaises(ValidationError) as ctx:
            svc.criar_contestacao(
                user=self.lider,
                falha_id=self.falha_reinspecao.id,
                justificativa="Tentar direcionar para Fraud",
                categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
            )

        self.assertEqual(
            ctx.exception.message_dict["categoria"],
            ["Categoria divergente da origem canônica da falha."],
        )

    def test_contextos_estruturados_divergentes_sao_rejeitados(self):
        origem = QualidadeAnaliseOrigem.objects.create(
            protocolo="PROT-CONTEXTO-DIVERGENTE",
            contexto={"fila_contexto": "reinspecao"},
            conteudo_hash="contestacao-contexto-divergente",
        )
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-CONTEXTO-DIVERGENTE",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_analise=timezone.now(),
            brflow_parsed={"fila_contexto": "auditoria_compliance"},
            analise_origem=origem,
            created_by=self.lider,
        )

        with self.assertRaises(ValidationError) as ctx:
            svc.criar_contestacao(
                user=self.lider,
                falha_id=falha.id,
                justificativa="Contexto inconsistente",
            )

        self.assertEqual(
            ctx.exception.message_dict["falha_id"],
            ["Falha com contextos estruturados divergentes."],
        )

        self.assertNotIn(
            falha.id,
            set(svc.falhas_queryset_for_user(self.lider).values_list("id", flat=True)),
        )
        self.assertNotIn(
            falha.id,
            set(
                svc.filter_falhas(
                    AuditoriaFalhaCadastro.objects.all(),
                    categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
                ).values_list("id", flat=True)
            ),
        )

        item = ContestacaoOperacional.objects.create(
            falha=falha,
            dominio=ContestacaoOperacional.DOMINIO_FRAUD,
            origem_tecnica=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
            justificativa_lider="Registro legado inconsistente",
            protocolo=falha.protocolo,
            agente_usuario=falha.usuario,
            atribuida_em=falha.data_analise,
            created_by=self.lider,
        )
        self.assertNotIn(
            item.id,
            set(
                svc.contestacoes_fila_queryset(
                    self.analista_fraud,
                    dominio=ContestacaoOperacional.DOMINIO_FRAUD,
                ).values_list("id", flat=True)
            ),
        )
        self.assertNotIn(
            item.id,
            set(
                svc.minhas_contestacoes_queryset(self.lider).values_list("id", flat=True)
            ),
        )

    def test_filter_by_agent_display_name(self):
        qs = svc.filter_falhas(
            svc.falhas_queryset_for_user(self.lider),
            agente="Agente A",
        )
        self.assertEqual(
            set(qs.values_list("id", flat=True)),
            {self.falha_team.id, self.falha_reinspecao.id},
        )

    def test_filter_atribuicao_uses_origem_field(self):
        qs = svc.filter_falhas(
            svc.falhas_queryset_for_user(self.lider),
            categoria=ContestacaoOperacional.CATEGORIA_REINSPECAO_COMPLIANCE,
            data_inicio=timezone.now() - timezone.timedelta(days=1),
            data_fim=timezone.now() + timezone.timedelta(days=1),
        )
        self.assertEqual(list(qs.values_list("id", flat=True)), [self.falha_reinspecao.id])

    def test_contestacao_fraud_uses_data_contestacao(self):
        now = timezone.now()
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-CONT-DATA",
            tipo_falha="Colaborador",
            usuario="agente.a",
            agente_ref=self.agent,
            status="Procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=now,
            analise_concluida_em=now - timezone.timedelta(days=30),
            created_by=self.lider,
        )
        qs = svc.filter_falhas(
            svc.falhas_queryset_for_user(self.lider),
            categoria=ContestacaoOperacional.CATEGORIA_CONTESTACAO_FRAUD,
            data_inicio=now - timezone.timedelta(days=1),
            data_fim=now + timezone.timedelta(days=1),
        )
        self.assertEqual(set(qs.values_list("id", flat=True)), {falha.id})
        self.assertEqual(svc.atribuicao_em_for_falha(falha), now)
        self.assertEqual(svc.data_auditoria_for_falha(falha), now)
        self.assertEqual(svc.serialize_falha(falha)["data_auditoria"], now.isoformat())

    def test_criar_e_bloquear_duplicata_ativa(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo do líder",
        )
        self.assertEqual(item.status, ContestacaoOperacional.STATUS_PENDENTE)
        self.assertEqual(item.dominio, ContestacaoOperacional.DOMINIO_FRAUD)
        with self.assertRaisesMessage(Exception, "Este protocolo já foi contestado"):
            svc.criar_contestacao(
                user=self.lider,
                falha_id=self.falha_team.id,
                justificativa="segunda",
            )

    def test_criar_rejeita_falha_sem_falha_por_id_direto(self):
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-POST-SEM-FALHA",
            tipo_falha="Sem Falha",
            usuario="agente.a",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            resultado_qualidade=AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=timezone.now(),
            created_by=self.lider,
        )

        with self.assertRaises(ValidationError) as ctx:
            svc.criar_contestacao(
                user=self.lider,
                falha_id=falha.id,
                justificativa="Tentativa por ID direto",
            )

        self.assertEqual(
            ctx.exception.message_dict["falha_id"],
            ["Falha nao elegivel para contestacao."],
        )
        self.assertFalse(ContestacaoOperacional.objects.filter(falha=falha).exists())

    def test_criar_rejeita_falha_fora_do_escopo_por_id_direto(self):
        with self.assertRaises(ValidationError) as ctx:
            svc.criar_contestacao(
                user=self.lider,
                falha_id=self.falha_other.id,
                justificativa="Tentativa fora da equipe",
            )

        self.assertEqual(
            ctx.exception.message_dict["falha_id"],
            ["Falha fora do escopo da sua equipe."],
        )
        self.assertFalse(
            ContestacaoOperacional.objects.filter(falha=self.falha_other).exists()
        )

    def test_bloqueia_nova_contestacao_apos_resposta(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo original",
        )
        item.status = ContestacaoOperacional.STATUS_PROCEDENTE
        item.save(update_fields=["status", "updated_at"])

        serialized = svc.serialize_falha(self.falha_team)
        self.assertEqual(serialized["contestacao_id"], item.id)
        self.assertEqual(serialized["contestacao_status"], item.status)
        self.assertIsNone(serialized["contestacao_ativa_id"])

        with self.assertRaisesMessage(Exception, "Este protocolo já foi contestado"):
            svc.criar_contestacao(
                user=self.lider,
                falha_id=self.falha_team.id,
                justificativa="Nova tentativa",
            )

    def test_procedente_retira_falha_sem_excluir(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)
        decided = svc.decidir_contestacao(
            user=self.analista_fraud,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="De acordo com o líder",
            destino_falha=ContestacaoOperacional.FALHA_STATUS_RETIRADA,
        )
        falha = AuditoriaFalhaCadastro.objects.get(pk=self.falha_team.id)
        self.assertTrue(AuditoriaFalhaCadastro.objects.filter(pk=falha.pk).exists())
        self.assertEqual(falha.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA)
        self.assertEqual(falha.auditor_responsavel_id, self.auditor_original.id)
        self.assertEqual(decided.created_by_id, self.lider.id)
        self.assertEqual(decided.analista_id, self.analista_fraud.id)
        self.assertEqual(decided.status, ContestacaoOperacional.STATUS_PROCEDENTE)
        self.assertEqual(
            decided.destino_falha,
            ContestacaoOperacional.FALHA_STATUS_RETIRADA,
        )

    def test_fraud_procedente_pode_manter_e_reclassificar_falha(self):
        self.falha_team.motivo_falha = "Cenário original"
        self.falha_team.nivel_dificuldade = "N1"
        self.falha_team.etapa_falha = "Triagem"
        self.falha_team.save(
            update_fields=["motivo_falha", "nivel_dificuldade", "etapa_falha", "updated_at"]
        )
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Reclassificar falha",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        decided = svc.decidir_contestacao(
            user=self.analista_fraud,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="Procedente, com novo enquadramento",
            destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
            cenario="Cenário revisado",
            nivel="N2",
            etapa="Validação documental",
        )

        falha = AuditoriaFalhaCadastro.objects.get(pk=self.falha_team.id)
        self.assertEqual(falha.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA)
        self.assertEqual(falha.motivo_falha, "Cenário revisado")
        self.assertEqual(falha.nivel_dificuldade, "N2")
        self.assertEqual(falha.etapa_falha, "Validação documental")
        self.assertEqual(decided.destino_falha, ContestacaoOperacional.FALHA_STATUS_MANTIDA)
        self.assertEqual(decided.cenario_original, "Cenário original")
        self.assertEqual(decided.nivel_original, "N1")
        self.assertEqual(decided.etapa_original, "Triagem")
        self.assertEqual(decided.cenario_decisao, "Cenário revisado")
        self.assertEqual(decided.nivel_decisao, "N2")
        self.assertEqual(decided.etapa_decisao, "Validação documental")

        serialized = svc.serialize_contestacao(decided)
        self.assertEqual(serialized["destino_falha"], ContestacaoOperacional.FALHA_STATUS_MANTIDA)
        self.assertEqual(serialized["cenario_original"], "Cenário original")
        self.assertEqual(serialized["nivel_original"], "N1")
        self.assertEqual(serialized["etapa_original"], "Triagem")
        self.assertEqual(serialized["cenario_decisao"], "Cenário revisado")
        self.assertEqual(serialized["nivel_decisao"], "N2")
        self.assertEqual(serialized["etapa_decisao"], "Validação documental")

    def test_fraud_procedente_mantida_exige_reclassificacao_completa(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Reclassificar falha",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=self.analista_fraud,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
                parecer="Campos incompletos",
                destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
                cenario="Cenário revisado",
            )

        self.assertEqual(set(ctx.exception.message_dict), {"nivel", "etapa"})
        item.refresh_from_db()
        self.assertEqual(item.status, ContestacaoOperacional.STATUS_EM_ANALISE)

    def test_fraud_reclassificacao_rejeita_valor_fora_do_catalogo(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Reclassificar falha",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=self.analista_fraud,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
                parecer="Payload fora do catálogo",
                destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
                cenario="Cenário inexistente",
                nivel="Nível inexistente",
                etapa="Etapa inexistente",
            )

        self.assertEqual(set(ctx.exception.message_dict), {"cenario", "nivel", "etapa"})
        item.refresh_from_db()
        self.assertEqual(item.status, ContestacaoOperacional.STATUS_EM_ANALISE)

    def test_fraud_reclassificacao_rejeita_nivel_acima_do_limite(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Reclassificar falha",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=self.analista_fraud,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
                parecer="Nível inválido",
                destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
                cenario="Cenário revisado",
                nivel="N" * 101,
                etapa="Validação documental",
            )

        self.assertEqual(set(ctx.exception.message_dict), {"nivel"})

    def test_procedente_sai_da_lista_nova_apos_retirada_no_indicador(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        item.status = ContestacaoOperacional.STATUS_PROCEDENTE
        item.save(update_fields=["status", "updated_at"])
        self.falha_team.status_falha = AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA
        self.falha_team.save(update_fields=["status_falha", "updated_at"])

        qs = svc.falhas_queryset_for_user(self.lider)
        self.assertNotIn(self.falha_team.id, set(qs.values_list("id", flat=True)))

        serialized = svc.serialize_falha(self.falha_team)
        self.assertEqual(serialized["contestacao_status"], ContestacaoOperacional.STATUS_PROCEDENTE)

    def test_auditor_pode_devolver_analise_para_fila(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        devolvida = svc.devolver_analise_fila(
            user=self.analista_fraud,
            contestacao_id=item.id,
        )

        self.assertEqual(devolvida.status, ContestacaoOperacional.STATUS_PENDENTE)
        self.assertIsNone(devolvida.analista_id)
        self.assertIsNone(devolvida.iniciada_em)
        self.assertTrue(
            devolvida.historico.filter(
                evento=ContestacaoOperacionalHistorico.EVENTO_DEVOLVIDA_FILA,
                status_anterior=ContestacaoOperacional.STATUS_EM_ANALISE,
                status_novo=ContestacaoOperacional.STATUS_PENDENTE,
            ).exists()
        )

    def test_decidir_exige_analise_iniciada(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=self.analista_fraud,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
                parecer="Sem iniciar",
            )
        self.assertEqual(
            ctx.exception.message_dict.get("status"),
            ["Inicie a análise antes de registrar a decisão."],
        )

    def test_outro_analista_autorizado_pode_decidir_item_em_analise(self):
        outro_analista = User.objects.create_user(
            "outro.analista.fraud",
            email="outro.analista.fraud@test.local",
            password="x",
        )
        outro_analista.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_CONTESTACAO_FRAUD))
        )
        # Recarrega para descartar caches de permissão populados pelos signals.
        outro_analista = User.objects.get(pk=outro_analista.pk)
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=outro_analista,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
                parecer="Continuidade operacional confirmada",
                destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
            )
        self.assertIn("confirmar_outro_analista", ctx.exception.message_dict)
        item.refresh_from_db()
        self.falha_team.refresh_from_db()
        self.assertEqual(item.status, ContestacaoOperacional.STATUS_EM_ANALISE)
        self.assertEqual(item.analista_id, self.analista_fraud.id)
        self.assertEqual(
            self.falha_team.status_falha,
            AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        )
        self.assertFalse(
            item.historico.filter(
                evento=ContestacaoOperacionalHistorico.EVENTO_DECIDIDA
            ).exists()
        )

        decided = svc.decidir_contestacao(
            user=outro_analista,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Continuidade operacional confirmada",
            destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
            confirmar_outro_analista=True,
        )

        self.assertEqual(decided.status, ContestacaoOperacional.STATUS_IMPROCEDENTE)
        self.assertEqual(decided.analista_id, outro_analista.id)
        inicio = decided.historico.get(
            evento=ContestacaoOperacionalHistorico.EVENTO_ANALISE_INICIADA
        )
        decisao = decided.historico.get(
            evento=ContestacaoOperacionalHistorico.EVENTO_DECIDIDA
        )
        self.assertEqual(inicio.actor_id, self.analista_fraud.id)
        self.assertEqual(inicio.status_novo, ContestacaoOperacional.STATUS_EM_ANALISE)
        self.assertEqual(decisao.actor_id, outro_analista.id)
        self.assertEqual(decisao.status_anterior, ContestacaoOperacional.STATUS_EM_ANALISE)
        self.assertEqual(decisao.status_novo, ContestacaoOperacional.STATUS_IMPROCEDENTE)
        self.assertEqual(decisao.parecer, "Continuidade operacional confirmada")

    def test_confirmacao_invalida_nao_libera_decisao_de_outro_analista(self):
        outro_analista = User.objects.create_user(
            "outro.analista.flag.invalida",
            email="outro.analista.flag.invalida@test.local",
            password="x",
        )
        outro_analista.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_CONTESTACAO_FRAUD))
        )
        outro_analista = User.objects.get(pk=outro_analista.pk)
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=outro_analista,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
                parecer="Flag textual não é confirmação booleana",
                destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
                confirmar_outro_analista="true",
            )

        self.assertIn("confirmar_outro_analista", ctx.exception.message_dict)
        item.refresh_from_db()
        self.assertEqual(item.status, ContestacaoOperacional.STATUS_EM_ANALISE)
        self.assertEqual(item.analista_id, self.analista_fraud.id)

    def test_analista_nulo_nao_exige_confirmacao_para_decidir(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        item.status = ContestacaoOperacional.STATUS_EM_ANALISE
        item.analista = None
        item.iniciada_em = timezone.now()
        item.save(
            update_fields=["status", "analista", "iniciada_em", "updated_at"]
        )

        decided = svc.decidir_contestacao(
            user=self.analista_fraud,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Assumida durante a resposta",
            destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
        )

        self.assertEqual(decided.status, ContestacaoOperacional.STATUS_IMPROCEDENTE)
        self.assertEqual(decided.analista_id, self.analista_fraud.id)

    def test_mesmo_analista_nao_exige_confirmacao_para_decidir(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        decided = svc.decidir_contestacao(
            user=self.analista_fraud,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Resposta pelo próprio analista",
            destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
        )

        self.assertEqual(decided.status, ContestacaoOperacional.STATUS_IMPROCEDENTE)
        self.assertEqual(decided.analista_id, self.analista_fraud.id)

    def test_usuario_sem_fraud_analyze_nao_pode_decidir_fraud(self):
        sem_permissao = User.objects.create_user(
            "sem.permissao.fraud",
            email="sem.permissao.fraud@test.local",
            password="x",
        )
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=sem_permissao,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
                parecer="Tentativa sem permissão",
                destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
            )

        self.assertIn("detail", ctx.exception.message_dict)
        item.refresh_from_db()
        self.assertEqual(item.status, ContestacaoOperacional.STATUS_EM_ANALISE)
        self.assertEqual(item.analista_id, self.analista_fraud.id)

    def test_analista_apenas_compliance_nao_pode_decidir_fraud(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_fraud, contestacao_id=item.id)

        with self.assertRaises(ValidationError) as ctx:
            svc.decidir_contestacao(
                user=self.analista_comp,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
                parecer="Tentativa com papel de outro domínio",
                destino_falha=ContestacaoOperacional.FALHA_STATUS_MANTIDA,
            )

        self.assertIn("detail", ctx.exception.message_dict)
        item.refresh_from_db()
        self.assertEqual(item.status, ContestacaoOperacional.STATUS_EM_ANALISE)
        self.assertEqual(item.analista_id, self.analista_fraud.id)

    def test_improcedente_mantem_falha(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_reinspecao.id,
            justificativa="Motivo",
        )
        self.assertEqual(item.dominio, ContestacaoOperacional.DOMINIO_COMPLIANCE)
        svc.iniciar_analise(user=self.analista_comp, contestacao_id=item.id)
        decided = svc.decidir_contestacao(
            user=self.analista_comp,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Mantém",
        )
        falha = AuditoriaFalhaCadastro.objects.get(pk=self.falha_reinspecao.id)
        self.assertEqual(falha.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA)
        self.assertEqual(falha.auditor_responsavel_id, self.auditor_original.id)
        self.assertEqual(decided.status, ContestacaoOperacional.STATUS_IMPROCEDENTE)

    def test_improcedente_compliance_retira_irregularidades_nao_mantidas(self):
        now = timezone.now()
        first = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-REIN-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            status="Improcedente",
            brflow_parsed={"situacao": "improcedente"},
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=now - timezone.timedelta(days=20),
            data_analise_intranet=now,
            analise_concluida_em=now,
            descricao_irregularidades="IC - 607 - Data da Assinatura ausente",
            created_by=self.lider,
            auditor_responsavel=self.auditor_original,
        )
        second = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-REIN-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            status="Improcedente",
            brflow_parsed={"situacao": "improcedente"},
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=now - timezone.timedelta(days=20),
            data_analise_intranet=now,
            analise_concluida_em=now,
            descricao_irregularidades="IC - 608 - Documento ilegivel",
            created_by=self.lider,
            auditor_responsavel=self.auditor_original,
        )
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=first.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_comp, contestacao_id=item.id)
        svc.decidir_contestacao(
            user=self.analista_comp,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Mantém somente a primeira",
            falhas_manter=[first.id],
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA)
        self.assertEqual(second.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA)

        serialized_second = svc.serialize_falha(second)
        self.assertTrue(serialized_second["protocolo_contestado"])
        self.assertEqual(serialized_second["contestacao_id"], item.id)
        self.assertEqual(serialized_second["contestacao_resumo"]["status"], ContestacaoOperacional.STATUS_IMPROCEDENTE)

        with self.assertRaisesMessage(Exception, "Este protocolo já foi contestado"):
            svc.criar_contestacao(
                user=self.lider,
                falha_id=second.id,
                justificativa="Tentar contestar irregularidade mantida",
            )

        merged = svc.serialize_protocol_groups([[first]])
        self.assertEqual(len(merged[0]["irregularidades_itens"]), 2)
        mantidas = [
            item["falha_id"]
            for item in merged[0]["irregularidades_itens"]
            if item["status_falha"] == AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA
        ]
        self.assertEqual(mantidas, [first.id])

    def test_separacao_dominio_fraud_compliance(self):
        fraud = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="f",
        )
        comp = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_reinspecao.id,
            justificativa="c",
        )
        fraud_ids = set(
            svc.contestacoes_fila_queryset(self.analista_fraud, dominio="fraud").values_list(
                "id", flat=True
            )
        )
        comp_ids = set(
            svc.contestacoes_fila_queryset(self.analista_comp, dominio="compliance").values_list(
                "id", flat=True
            )
        )
        self.assertIn(fraud.id, fraud_ids)
        self.assertNotIn(comp.id, fraud_ids)
        self.assertIn(comp.id, comp_ids)
        self.assertNotIn(fraud.id, comp_ids)

    def test_filas_exigem_dominio_e_categoria_compativeis(self):
        fraud = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_team.id,
            justificativa="f",
        )
        comp = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_reinspecao.id,
            justificativa="c",
        )
        ContestacaoOperacional.objects.filter(pk=fraud.pk).update(
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
        )
        ContestacaoOperacional.objects.filter(pk=comp.pk).update(
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_FRAUD,
        )

        fraud_ids = set(
            svc.contestacoes_fila_queryset(
                self.analista_fraud, dominio=ContestacaoOperacional.DOMINIO_FRAUD
            ).values_list("id", flat=True)
        )
        compliance_ids = set(
            svc.contestacoes_fila_queryset(
                self.analista_comp, dominio=ContestacaoOperacional.DOMINIO_COMPLIANCE
            ).values_list("id", flat=True)
        )

        self.assertNotIn(fraud.id, fraud_ids)
        self.assertNotIn(comp.id, compliance_ids)

    def test_revisar_compliance_altera_resultado_e_preserva_analista_decisao(self):
        ensure_role_groups_exist()
        gerencia = User.objects.create_user(
            "gerencia.qual", email="gerencia.qual@test.local", password="x"
        )
        gerencia.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_GERENCIA))
        )
        gerencia = User.objects.get(pk=gerencia.pk)

        now = timezone.now()
        first = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-REV-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            status="Improcedente",
            brflow_parsed={"situacao": "improcedente"},
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=now - timezone.timedelta(days=5),
            data_analise_intranet=now,
            analise_concluida_em=now,
            descricao_irregularidades="IC - 607 - Data da Assinatura ausente",
            created_by=self.lider,
            auditor_responsavel=self.auditor_original,
        )
        second = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-REV-1",
            tipo_falha="critica",
            usuario="agente.a",
            agente_ref=self.agent,
            status="Improcedente",
            brflow_parsed={"situacao": "improcedente"},
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=now - timezone.timedelta(days=5),
            data_analise_intranet=now,
            analise_concluida_em=now,
            descricao_irregularidades="IC - 608 - Documento ilegivel",
            created_by=self.lider,
            auditor_responsavel=self.auditor_original,
        )
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=first.id,
            justificativa="Revisar depois",
        )
        svc.iniciar_analise(user=self.analista_comp, contestacao_id=item.id)
        svc.decidir_contestacao(
            user=self.analista_comp,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Mantém somente a primeira",
            falhas_manter=[first.id],
        )

        revised = svc.revisar_resultado_contestacao(
            user=gerencia,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="Liderança alterou para procedente",
        )
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertEqual(revised.status, ContestacaoOperacional.STATUS_PROCEDENTE)
        self.assertEqual(revised.analista_decisao_id, self.analista_comp.id)
        self.assertEqual(revised.parecer_interno, "Mantém somente a primeira")
        self.assertEqual(revised.parecer_revisao, "Liderança alterou para procedente")
        self.assertEqual(revised.falhas_manter_ids, [])
        self.assertEqual(first.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA)
        self.assertEqual(second.status_falha, AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA)

        historico = revised.historico.filter(
            evento=ContestacaoOperacionalHistorico.EVENTO_RESULTADO_ALTERADO
        ).first()
        self.assertIsNotNone(historico)
        self.assertEqual(historico.status_anterior, ContestacaoOperacional.STATUS_IMPROCEDENTE)
        self.assertEqual(historico.status_novo, ContestacaoOperacional.STATUS_PROCEDENTE)
        self.assertEqual(historico.metadata.get("resultado_de"), ContestacaoOperacional.STATUS_IMPROCEDENTE)
        self.assertEqual(historico.metadata.get("resultado_para"), ContestacaoOperacional.STATUS_PROCEDENTE)
        self.assertEqual(
            historico.metadata.get("analista_decisao_id"), str(self.analista_comp.id)
        )
        serialized_revisao = svc.serialize_contestacao_revisao(historico)
        self.assertIn("IC - 607", serialized_revisao["falhas_manter_de_label"])
        self.assertEqual(serialized_revisao["falhas_manter_para_label"], "Nenhuma")

    def test_revisar_sem_alteracao_rejeita(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_reinspecao.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_comp, contestacao_id=item.id)
        svc.decidir_contestacao(
            user=self.analista_comp,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="Procedente",
        )
        ensure_role_groups_exist()
        gerencia = User.objects.create_user(
            "gerencia.qual2", email="gerencia.qual2@test.local", password="x"
        )
        gerencia.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_GERENCIA))
        )
        gerencia = User.objects.get(pk=gerencia.pk)

        with self.assertRaises(ValidationError):
            svc.revisar_resultado_contestacao(
                user=gerencia,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
                parecer="Sem mudança",
            )

    def test_lider_sem_permissao_revisar_rejeita(self):
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_reinspecao.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_comp, contestacao_id=item.id)
        svc.decidir_contestacao(
            user=self.analista_comp,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="Procedente",
        )

        with self.assertRaises(ValidationError):
            svc.revisar_resultado_contestacao(
                user=self.lider,
                contestacao_id=item.id,
                resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
                parecer="Tentativa sem permissão",
            )

    def test_build_erros_por_analista_contestacao_revisada(self):
        ensure_role_groups_exist()
        gerencia = User.objects.create_user(
            "gerencia.qual3", email="gerencia.qual3@test.local", password="x"
        )
        gerencia.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_GERENCIA))
        )
        gerencia = User.objects.get(pk=gerencia.pk)

        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha_reinspecao.id,
            justificativa="Motivo",
        )
        svc.iniciar_analise(user=self.analista_comp, contestacao_id=item.id)
        svc.decidir_contestacao(
            user=self.analista_comp,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="Procedente",
        )
        svc.revisar_resultado_contestacao(
            user=gerencia,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Revisado para improcedente",
        )

        rows = svc.build_erros_por_analista_contestacao_revisada()
        match = next(
            row for row in rows if row["analista_decisao_id"] == str(self.analista_comp.id)
        )
        self.assertEqual(match["erros_contestacao_revisada"], 1)


@override_settings(ACCESS_ENFORCEMENT=True)
class ContestacaoOperacionalApiTests(TestCase):
    def setUp(self):
        ensure_role_groups_exist()
        self.client = APIClient()
        self.lider = User.objects.create_user("lider.api", email="lider.api@test.local", password="x")
        self.lider.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_LIDER)))
        self.leader_agent = Agent.objects.create(
            full_name="Lider Nome Completo",
            user_lan_id="lider.api",
        )
        self.agent = Agent.objects.create(full_name="Agente", user_lan_id="ag.api")
        self.auditor = Agent.objects.create(
            full_name="Auditor Nome Completo",
            user_lan_id="auditor.api",
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader_agent,
            active=True,
            start_date=timezone.now().date(),
        )
        self.falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="API-1",
            tipo_falha="critica",
            usuario="ag.api",
            agente_ref=self.agent,
            motivo_falha="Documento divergente",
            etapa_falha="Validação documental",
            descricao_irregularidades="CPF/RG inválido",
            brflow_parsed={
                "workflow": "Onboarding Claro",
                "nivel_hierarquico": "N2",
                "cenario": "Valor legado não prioritário",
                "situacao": "procedente",
            },
            resultado_cliente="Resultado original",
            novo_resultado="Resultado corrigido",
            observacao="Observação técnica sem identificação do auditor",
            auditor="auditor.api",
            auditor_ref=self.auditor,
            status="procedente",
            origem=AuditoriaFalhaCadastro.ORIGEM_CONTESTACAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            data_contestacao=timezone.now(),
            analise_concluida_em=timezone.now(),
            created_by=self.lider,
        )
        self.client.force_authenticate(self.lider)

    def test_agent_options_use_names_from_leader_scope(self):
        response = self.client.get("/api/v1/qualidade/contestacao-operacional/agentes/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data["results"],
            [{"value": "ag.api", "label": "Agente"}],
        )

    def test_auditor_devolve_contestacao_assumida_para_fila(self):
        self.lider.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_CONTESTACAO_FRAUD))
        )
        criada = self.client.post(
            "/api/v1/qualidade/contestacao-operacional/criar/",
            {"falha_id": self.falha.id, "justificativa": "Reavaliar"},
            format="json",
        )
        self.assertEqual(criada.status_code, 201)

        iniciada = self.client.post(
            f"/api/v1/qualidade/contestacao-operacional/interna/{criada.data['id']}/iniciar/"
        )
        self.assertEqual(iniciada.status_code, 200)
        self.assertEqual(iniciada.data["status"], ContestacaoOperacional.STATUS_EM_ANALISE)

        devolvida = self.client.post(
            f"/api/v1/qualidade/contestacao-operacional/interna/{criada.data['id']}/devolver/"
        )
        self.assertEqual(devolvida.status_code, 200)
        self.assertEqual(devolvida.data["status"], ContestacaoOperacional.STATUS_PENDENTE)
        self.assertIsNone(devolvida.data["analista"])
        self.assertIsNone(devolvida.data["iniciada_em"])
        self.assertEqual(
            devolvida.data["historico"][0]["evento"],
            ContestacaoOperacionalHistorico.EVENTO_DEVOLVIDA_FILA,
        )

    def test_decidir_endpoint_propaga_confirmacao_de_outro_analista(self):
        fraud_group = Group.objects.get(
            name=role_group_name(ROLE_QUAL_CONTESTACAO_FRAUD)
        )
        analista_original = User.objects.create_user(
            "analista.original.api",
            email="analista.original.api@test.local",
            password="x",
        )
        analista_novo = User.objects.create_user(
            "analista.novo.api",
            email="analista.novo.api@test.local",
            password="x",
        )
        analista_original.groups.add(fraud_group)
        analista_novo.groups.add(fraud_group)
        analista_original = User.objects.get(pk=analista_original.pk)
        analista_novo = User.objects.get(pk=analista_novo.pk)
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha.id,
            justificativa="Reavaliar",
        )
        svc.iniciar_analise(user=analista_original, contestacao_id=item.id)
        self.client.force_authenticate(analista_novo)
        payload = {
            "resultado": ContestacaoOperacional.STATUS_IMPROCEDENTE,
            "parecer": "Continuidade operacional via API",
            "destino_falha": ContestacaoOperacional.FALHA_STATUS_MANTIDA,
        }

        sem_confirmacao = self.client.post(
            f"/api/v1/qualidade/contestacao-operacional/interna/{item.id}/decidir/",
            payload,
            format="json",
        )

        self.assertEqual(sem_confirmacao.status_code, 400, sem_confirmacao.data)
        self.assertIn("confirmar_outro_analista", sem_confirmacao.data)

        confirmado = self.client.post(
            f"/api/v1/qualidade/contestacao-operacional/interna/{item.id}/decidir/",
            {**payload, "confirmar_outro_analista": True},
            format="json",
        )

        self.assertEqual(confirmado.status_code, 200, confirmado.data)
        self.assertEqual(confirmado.data["analista"], analista_novo.id)
        self.assertEqual(
            confirmado.data["status"],
            ContestacaoOperacional.STATUS_IMPROCEDENTE,
        )

    def test_revisar_endpoint_altera_resultado_compliance(self):
        comp_group = Group.objects.get(
            name=role_group_name(ROLE_QUAL_CONTESTACAO_COMPLIANCE)
        )
        analista = User.objects.create_user(
            "analista.comp.api",
            email="analista.comp.api@test.local",
            password="x",
        )
        gerencia = User.objects.create_user(
            "gerencia.comp.api",
            email="gerencia.comp.api@test.local",
            password="x",
        )
        analista.groups.add(comp_group)
        gerencia.groups.add(Group.objects.get(name=role_group_name(ROLE_QUAL_GERENCIA)))
        analista = User.objects.get(pk=analista.pk)
        gerencia = User.objects.get(pk=gerencia.pk)

        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="API-REV-1",
            tipo_falha="critica",
            usuario="ag.api",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=timezone.now(),
            created_by=self.lider,
        )
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=falha.id,
            justificativa="Revisar via API",
        )
        svc.iniciar_analise(user=analista, contestacao_id=item.id)
        svc.decidir_contestacao(
            user=analista,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Improcedente inicial",
        )

        self.client.force_authenticate(gerencia)
        response = self.client.post(
            f"/api/v1/qualidade/contestacao-operacional/interna/{item.id}/revisar/",
            {
                "resultado": ContestacaoOperacional.STATUS_PROCEDENTE,
                "parecer": "Revisão da liderança",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], ContestacaoOperacional.STATUS_PROCEDENTE)
        self.assertTrue(response.data["can_revisar"])
        self.assertEqual(response.data["parecer_revisao"], "Revisão da liderança")
        self.assertEqual(
            response.data["historico"][0]["evento"],
            ContestacaoOperacionalHistorico.EVENTO_RESULTADO_ALTERADO,
        )

    def test_revisoes_endpoint_pagina_lista_sem_erro(self):
        comp_group = Group.objects.get(
            name=role_group_name(ROLE_QUAL_CONTESTACAO_COMPLIANCE)
        )
        gerencia = User.objects.create_user(
            "gerencia.revisoes.api",
            email="gerencia.revisoes.api@test.local",
            password="x",
        )
        gerencia.groups.add(Group.objects.get(name=role_group_name(ROLE_QUAL_GERENCIA)))
        gerencia = User.objects.get(pk=gerencia.pk)

        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="API-REV-LIST",
            tipo_falha="critica",
            usuario="ag.api",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=timezone.now(),
            created_by=self.lider,
        )
        item = svc.criar_contestacao(
            user=self.lider,
            falha_id=falha.id,
            justificativa="Revisar via API",
        )
        analista = User.objects.create_user(
            "analista.revisoes.api",
            email="analista.revisoes.api@test.local",
            password="x",
        )
        analista.groups.add(comp_group)
        analista = User.objects.get(pk=analista.pk)
        svc.iniciar_analise(user=analista, contestacao_id=item.id)
        svc.decidir_contestacao(
            user=analista,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_IMPROCEDENTE,
            parecer="Improcedente inicial",
        )
        svc.revisar_resultado_contestacao(
            user=gerencia,
            contestacao_id=item.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="Revisão da liderança",
        )

        self.client.force_authenticate(gerencia)
        response = self.client.get(
            "/api/v1/qualidade/contestacao-operacional/interna/compliance/revisoes/",
            {"page": 1, "page_size": 50},
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["protocolo"], "API-REV-LIST")

    def test_list_and_create_endpoints(self):
        list_resp = self.client.get("/api/v1/qualidade/contestacao-operacional/falhas/")
        self.assertEqual(list_resp.status_code, 200)
        self.assertGreaterEqual(list_resp.data["count"], 1)
        listed = next(item for item in list_resp.data["results"] if item["id"] == self.falha.id)
        self.assertEqual(listed["agente_nome"], "Agente")
        self.assertEqual(listed["lider_nome"], "Lider Nome Completo")
        self.assertEqual(listed["motivo_falha"], "Documento divergente")
        self.assertEqual(listed["etapa_falha"], "Validação documental")
        self.assertEqual(listed["irregularidade"], "CPF/RG inválido")
        self.assertEqual(listed["workflow"], "Onboarding Claro")
        self.assertEqual(listed["nivel_hierarquico"], "N2")
        self.assertEqual(listed["cenario"], "Documento divergente")
        self.assertEqual(
            listed["observacoes_auditores"],
            ["Observação técnica sem identificação do auditor"],
        )
        self.assertNotIn("auditor", listed)

        create_resp = self.client.post(
            "/api/v1/qualidade/contestacao-operacional/criar/",
            {"falha_id": self.falha.id, "justificativa": "Justificativa API"},
            format="json",
        )
        self.assertEqual(create_resp.status_code, 201)
        self.assertEqual(create_resp.data["status"], "pendente")
        self.assertEqual(create_resp.data["agente_nome"], "Agente")
        self.assertEqual(create_resp.data["lider_nome"], "Lider Nome Completo")
        self.assertEqual(create_resp.data["auditor_nome"], "Auditor Nome Completo")
        self.assertEqual(create_resp.data["irregularidade"], "CPF/RG inválido")
        self.assertEqual(create_resp.data["data_contestacao"], create_resp.data["created_at"])

        minhas = self.client.get("/api/v1/qualidade/contestacao-operacional/minhas/")
        self.assertEqual(minhas.status_code, 200)
        self.assertGreaterEqual(minhas.data["count"], 1)
        minha = next(item for item in minhas.data["results"] if item["id"] == create_resp.data["id"])
        self.assertEqual(minha["agente_nome"], "Agente")
        self.assertEqual(minha["data_contestacao"], minha["created_at"])
        self.assertEqual(minha["data_auditoria"], self.falha.data_contestacao.isoformat())
        self.assertNotIn("falha", minha)

        detail = self.client.get(
            f"/api/v1/qualidade/contestacao-operacional/interna/{create_resp.data['id']}/"
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["agente_nome"], "Agente")
        self.assertEqual(detail.data["falha"]["id"], self.falha.id)
        self.assertEqual(
            detail.data["falha"]["irregularidade"],
            self.falha.descricao_irregularidades,
        )

    def test_internal_answered_filter_returns_only_final_statuses(self):
        analyst = User.objects.create_user(
            "analista.api", email="analista.api@test.local", password="x"
        )
        analyst.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_CONTESTACAO_FRAUD))
        )
        answered = svc.criar_contestacao(
            user=self.lider,
            falha_id=self.falha.id,
            justificativa="Reavaliar",
        )
        svc.iniciar_analise(user=analyst, contestacao_id=answered.id)
        svc.decidir_contestacao(
            user=analyst,
            contestacao_id=answered.id,
            resultado=ContestacaoOperacional.STATUS_PROCEDENTE,
            parecer="Procedente",
            destino_falha=ContestacaoOperacional.FALHA_STATUS_RETIRADA,
        )
        pending_falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-PENDENTE-FILTRO",
            tipo_falha="critica",
            usuario="ag.api",
            agente_ref=self.agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=timezone.now(),
            created_by=self.lider,
        )
        pending = svc.criar_contestacao(
            user=self.lider,
            falha_id=pending_falha.id,
            justificativa="Nova reavaliacao",
        )

        self.client.force_authenticate(analyst)
        response = self.client.get(
            "/api/v1/qualidade/contestacao-operacional/interna/fraud/fila/",
            {"status": "respondidas"},
        )
        detail = self.client.get(
            f"/api/v1/qualidade/contestacao-operacional/interna/{answered.id}/"
        )

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.data["results"]}
        self.assertIn(answered.id, ids)
        self.assertNotIn(pending.id, ids)
        self.assertEqual(detail.data["falha"]["irregularidade"], "CPF/RG inválido")


@override_settings(ACCESS_ENFORCEMENT=True)
class ContestacaoOperacionalConcurrencyTests(TransactionTestCase):
    def setUp(self):
        ensure_role_groups_exist()
        self.analista = User.objects.create_user(
            "analista.conc", email="analista.conc@test.local", password="x"
        )
        self.analista.groups.add(
            Group.objects.get(name=role_group_name(ROLE_QUAL_CONTESTACAO_FRAUD))
        )
        self.lider = User.objects.create_user("lider.conc", email="lider.conc@test.local", password="x")
        self.lider.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_LIDER)))
        leader_agent = Agent.objects.create(full_name="L", user_lan_id="lider.conc")
        agent = Agent.objects.create(full_name="A", user_lan_id="ag.conc")
        AgentHistory.objects.create(
            agent=agent,
            leader=leader_agent,
            active=True,
            start_date=timezone.now().date(),
        )
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="CONC-1",
            tipo_falha="critica",
            usuario="ag.conc",
            agente_ref=agent,
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
            analise_status=AuditoriaFalhaCadastro.ANALISE_CONCLUIDO,
            analise_concluida_em=timezone.now(),
            created_by=self.lider,
        )
        self.item = svc.criar_contestacao(
            user=self.lider,
            falha_id=falha.id,
            justificativa="conc",
        )
        svc.iniciar_analise(user=self.analista, contestacao_id=self.item.id)

    def test_concurrent_decide_keeps_single_outcome(self):
        def decide(resultado: str):
            try:
                with transaction.atomic():
                    return svc.decidir_contestacao(
                        user=self.analista,
                        contestacao_id=self.item.id,
                        resultado=resultado,
                        parecer=f"parecer {resultado}",
                        destino_falha=(
                            ContestacaoOperacional.FALHA_STATUS_RETIRADA
                            if resultado == ContestacaoOperacional.STATUS_PROCEDENTE
                            else ContestacaoOperacional.FALHA_STATUS_MANTIDA
                        ),
                    )
            except Exception as exc:  # noqa: BLE001
                return exc

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(decide, ContestacaoOperacional.STATUS_PROCEDENTE),
                pool.submit(decide, ContestacaoOperacional.STATUS_IMPROCEDENTE),
            ]
            results = [f.result() for f in futures]

        successes = [r for r in results if isinstance(r, ContestacaoOperacional)]
        self.assertEqual(len(successes), 1)
        refreshed = ContestacaoOperacional.objects.get(pk=self.item.id)
        self.assertIn(
            refreshed.status,
            (
                ContestacaoOperacional.STATUS_PROCEDENTE,
                ContestacaoOperacional.STATUS_IMPROCEDENTE,
            ),
        )
        connection.close()
