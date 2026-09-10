from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaAtividade,
    AuditoriaAtividadeProtocolo,
    AuditoriaFalhaCadastro,
    ContestacaoOperacional,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.qualidade_promocao import promover_pendente_reinspecao
from apps.auditoria.services.serialization import serialize_falha_cadastro
from apps.auditoria.services.suporte_operacional_link import (
    lookup_answered_support_request,
    resolve_and_link,
    serialize_operational_support_link,
)
from apps.suporte_operacional.models import OperationalSupportRequest
from apps.workforce.models import Agent

User = get_user_model()


def _create_answered_support(
    *,
    protocol: str,
    workflow: str,
    agent: Agent,
    operation_origin: str,
    answered_by: User,
    answer: str = "Resposta de teste",
) -> OperationalSupportRequest:
    request = OperationalSupportRequest(
        agent=agent,
        requester=answered_by,
        requester_type=OperationalSupportRequest.RequesterType.AGENT,
        operation_origin=operation_origin,
        protocol=protocol,
        workflow=workflow,
        client="Claro",
        subject="Dúvida",
        category="duvida",
        description="Descrição de teste",
        status=OperationalSupportRequest.Status.ANSWERED,
        answer=answer,
        answer_option="Opção A",
        answered_by=answered_by,
        answered_at=timezone.now(),
    )
    request._allow_workflow_update = True
    request.save()
    return request


@override_settings(ACCESS_ENFORCEMENT=False)
class SuporteOperacionalLinkTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="support_link_user",
            email="support_link@test.local",
            password="test12345",
        )
        self.agent = Agent.objects.create(
            user_lan_id="c99999a",
            full_name="Agente Teste",
            active=True,
            hire_date=date(2024, 1, 1),
        )
        self.other_agent = Agent.objects.create(
            user_lan_id="c88888a",
            full_name="Outro Agente",
            active=True,
            hire_date=date(2024, 1, 1),
        )

    def test_lookup_fraud_requires_protocol_workflow_and_matricula(self):
        _create_answered_support(
            protocol="PROT-001",
            workflow="WF Alpha",
            agent=self.agent,
            operation_origin=OperationalSupportRequest.OperationOrigin.FRAUD,
            answered_by=self.user,
        )
        match = lookup_answered_support_request(
            key_type="fraud",
            protocolo="PROT-001",
            workflow="WF Alpha",
            matricula="c99999a",
        )
        self.assertIsNotNone(match)
        self.assertIsNone(
            lookup_answered_support_request(
                key_type="fraud",
                protocolo="PROT-001",
                workflow="WF Alpha",
                matricula="c88888a",
            )
        )
        self.assertIsNone(
            lookup_answered_support_request(
                key_type="fraud",
                protocolo="PROT-001",
                workflow="WF Outro",
                matricula="c99999a",
            )
        )

    def test_lookup_compliance_uses_protocol_and_matricula_only(self):
        _create_answered_support(
            protocol="PROT-COMP",
            workflow="",
            agent=self.agent,
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
            answered_by=self.user,
        )
        match = lookup_answered_support_request(
            key_type="compliance",
            protocolo="PROT-COMP",
            matricula="c99999a",
        )
        self.assertIsNotNone(match)

    def test_lookup_ignores_non_answered_status(self):
        request = OperationalSupportRequest(
            agent=self.agent,
            requester=self.user,
            requester_type=OperationalSupportRequest.RequesterType.AGENT,
            operation_origin=OperationalSupportRequest.OperationOrigin.FRAUD,
            protocol="PROT-PEND",
            workflow="WF Alpha",
            subject="Dúvida",
            category="duvida",
            description="Pendente",
            status=OperationalSupportRequest.Status.PENDING_SUPPORT,
        )
        request._allow_workflow_update = True
        request.save()
        self.assertIsNone(
            lookup_answered_support_request(
                key_type="fraud",
                protocolo="PROT-PEND",
                workflow="WF Alpha",
                matricula="c99999a",
            )
        )

    def test_resolve_and_link_on_protocolo(self):
        support = _create_answered_support(
            protocol="PROT-FRAUD",
            workflow="WF Beta",
            agent=self.agent,
            operation_origin=OperationalSupportRequest.OperationOrigin.FRAUD,
            answered_by=self.user,
        )
        atividade = AuditoriaAtividade.objects.create(
            tipo=AuditoriaAtividade.TIPO_AUDITORIA,
            nome="Atividade",
            created_by=self.user,
        )
        protocolo = AuditoriaAtividadeProtocolo.objects.create(
            atividade=atividade,
            protocolo="PROT-FRAUD",
            workflow="WF Beta",
            agente="c99999a",
            excel_row=1,
        )
        linked = resolve_and_link(protocolo)
        protocolo.refresh_from_db()
        self.assertEqual(linked.pk, support.pk)
        self.assertEqual(protocolo.operational_support_request_id, support.pk)

    def test_promocao_reinspecao_copia_fk_para_tratado(self):
        support = _create_answered_support(
            protocol="PROT-REIN",
            workflow="",
            agent=self.agent,
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
            answered_by=self.user,
        )
        pendente = QualidadePendenteReinspecao.objects.create(
            protocolo="PROT-REIN",
            usuario="c99999a",
            descricao_irregularidades="Irregularidade",
            data_contestacao=timezone.now(),
            created_by=self.user,
        )
        resolve_and_link(pendente)
        falha = promover_pendente_reinspecao(pendente, user=self.user)
        self.assertEqual(falha.operational_support_request_id, support.pk)

    def test_serialize_falha_inclui_suporte_operacional(self):
        support = _create_answered_support(
            protocol="PROT-SER",
            workflow="",
            agent=self.agent,
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
            answered_by=self.user,
            answer="Resposta validada",
        )
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-SER",
            usuario="c99999a",
            tipo_falha="reinspecao",
            origem=AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_REINSPECAO,
            operational_support_request=support,
        )
        payload = serialize_falha_cadastro(falha)
        self.assertTrue(payload["suporte_operacional"]["vinculado"])
        self.assertEqual(payload["suporte_operacional"]["answer"], "Resposta validada")

    def test_serialize_operational_support_link_vazio(self):
        self.assertEqual(
            serialize_operational_support_link(None),
            {"vinculado": False},
        )

    def test_resolve_and_link_contestacao_operacional_compliance(self):
        support = _create_answered_support(
            protocol="PROT-CO",
            workflow="",
            agent=self.agent,
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
            answered_by=self.user,
        )
        falha = AuditoriaFalhaCadastro.objects.create(
            protocolo="PROT-CO",
            usuario="c99999a",
            tipo_falha="auditoria",
            origem=AuditoriaFalhaCadastro.ORIGEM_AUDITORIA,
            tipo_registro=AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
        )
        contestacao = ContestacaoOperacional.objects.create(
            falha=falha,
            dominio=ContestacaoOperacional.DOMINIO_COMPLIANCE,
            categoria=ContestacaoOperacional.CATEGORIA_AUDITORIA_COMPLIANCE,
            protocolo="PROT-CO",
            agente_usuario="c99999a",
            atribuida_em=timezone.now() - timedelta(days=1),
            justificativa_lider="Teste",
            created_by=self.user,
        )
        linked = resolve_and_link(contestacao)
        contestacao.refresh_from_db()
        self.assertEqual(linked.pk, support.pk)
        self.assertEqual(contestacao.operational_support_request_id, support.pk)

    def test_resolve_and_link_compliance_pendente(self):
        support = _create_answered_support(
            protocol="PROT-AC",
            workflow="",
            agent=self.agent,
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
            answered_by=self.user,
        )
        pendente = QualidadePendenteAuditoriaCompliance.objects.create(
            protocolo="PROT-AC",
            usuario="c99999a",
            descricao_irregularidades="Item",
            data_contestacao=timezone.now(),
            created_by=self.user,
        )
        linked = resolve_and_link(pendente)
        pendente.refresh_from_db()
        self.assertEqual(linked.pk, support.pk)
