"""Testes de workflow e API de suporte operacional."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_OP_AGENTE,
    ROLE_OP_LIDER,
    ROLE_QUAL_CAPACITACAO,
    role_group_name,
)
from apps.auditoria.models import AuditoriaCatalogItem
from apps.dimensoes_processos.models import DimCliente, DimNivelHierarquico, DimWorkflow, ProjecaoSla
from apps.suporte_operacional.models import OperationalSupportEvent, OperationalSupportRequest
from apps.suporte_operacional.services.fila_online import direcionar_solicitacao, set_agent_status
from apps.suporte_operacional.services.workflow import (
    Status,
    WorkflowError,
    answer_request,
    assign_request,
    cancel_request,
    create_request,
    leader_decision,
)
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()
BASE = "/api/v1/operational-support"


def _assign_online_for_test(*, actor, assignee, request_id):
    return direcionar_solicitacao(
        actor=actor,
        agent_id=assignee.id,
        request_id=request_id,
        justificativa="Atribuição em teste.",
    )


def _user_with_role(username: str, role: str, *, full_name: str | None = None) -> User:
    user = User.objects.create_user(username, email=f"{username}@test.local", password="x")
    agent = Agent.objects.create(
        user_lan_id=username,
        full_name=full_name or username,
        active=True,
        hire_date=date(2024, 1, 1),
    )
    AgentHistory.objects.create(
        agent=agent,
        team="Operacional/Alpha",
        job_title="Agente Backoffice I",
        start_date=date(2024, 1, 1),
        active=True,
    )
    Group.objects.get_or_create(name=role_group_name(role))
    user.groups.add(Group.objects.get(name=role_group_name(role)))
    return user


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OperationalSupportWorkflowTests(TestCase):
    def setUp(self):
        self.agent_user = _user_with_role("so_agent1", ROLE_OP_AGENTE, full_name="Agente Um")
        self.agent2_user = _user_with_role("so_agent2", ROLE_OP_AGENTE, full_name="Agente Dois")
        self.leader = _user_with_role("so_leader1", ROLE_OP_LIDER, full_name="Lider Um")
        self.leader2 = _user_with_role("so_leader2", ROLE_OP_LIDER, full_name="Lider Dois")
        self.cap = _user_with_role("so_cap1", ROLE_QUAL_CAPACITACAO, full_name="Cap Um")
        self.agent = Agent.objects.get(user_lan_id="so_agent1")
        self.agent2 = Agent.objects.get(user_lan_id="so_agent2")

    def test_agent_opens_for_self(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="Dúvida A",
            category="procedimento",
            description="Como faço X?",
        )
        self.assertEqual(req.status, Status.PENDING_LEADER)
        self.assertEqual(req.requester_type, "agent")
        self.assertEqual(
            req.operation_origin,
            OperationalSupportRequest.OperationOrigin.FRAUD,
        )
        self.assertEqual(req.request_type, OperationalSupportRequest.RequestType.ONLINE)
        self.assertFalse(req.auto_approved)
        self.assertTrue(req.events.filter(event_type="created").exists())

    def test_agent_cannot_open_for_third(self):
        with self.assertRaises(WorkflowError) as ctx:
            create_request(
                user=self.agent_user,
                agent=self.agent2,
                subject="X",
                category="sistema",
                description="Y",
            )
        self.assertEqual(ctx.exception.status_code, 403)

    def test_leader_opens_any_agent_auto_approved(self):
        req = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Ajuda",
            category="escala",
            description="Detalhe",
        )
        self.assertEqual(req.status, Status.PENDING_SUPPORT)
        self.assertTrue(req.auto_approved)
        self.assertEqual(req.leader_decider_id, self.leader.id)
        self.assertTrue(req.events.filter(event_type="auto_approved").exists())

    def test_offline_request_uses_separate_path_and_never_enters_online_queue(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="Dúvida sem urgência",
            category="procedimento",
            description="Pode ser respondida posteriormente.",
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )
        self.assertEqual(req.status, Status.PENDING_LEADER)
        self.assertEqual(req.request_type, OperationalSupportRequest.RequestType.OFFLINE)

        approved = leader_decision(user=self.leader, request_id=req.pk, approved=True)
        self.assertEqual(approved.status, Status.PENDING_OFFLINE)

        assigned = assign_request(user=self.cap, request_id=approved.pk)
        self.assertEqual(assigned.status, Status.IN_ANALYSIS)
        self.assertEqual(assigned.assignee_id, self.cap.id)

    def test_presencial_request_assigns_support_user_after_leader_approval(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="Dúvida presencial",
            category="procedimento",
            description="Atendimento feito presencialmente.",
            request_type=OperationalSupportRequest.RequestType.PRESENCIAL,
            presencial_support_user=self.cap,
        )
        self.assertEqual(req.status, Status.PENDING_LEADER)
        self.assertEqual(req.presencial_support_user_id, self.cap.id)
        self.assertIsNone(req.assignee_id)

        approved = leader_decision(user=self.leader, request_id=req.pk, approved=True)
        self.assertEqual(approved.status, Status.IN_ANALYSIS)
        self.assertEqual(approved.assignee_id, self.cap.id)
        self.assertTrue(approved.events.filter(event_type="assigned").exists())

    def test_leader_presencial_auto_approved_goes_to_support_assignee(self):
        req = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Presencial do líder",
            category="outros",
            description="Registro presencial.",
            request_type=OperationalSupportRequest.RequestType.PRESENCIAL,
            presencial_support_user=self.cap,
        )
        self.assertTrue(req.auto_approved)
        self.assertEqual(req.status, Status.IN_ANALYSIS)
        self.assertEqual(req.assignee_id, self.cap.id)

    def test_presencial_requires_support_user(self):
        with self.assertRaises(WorkflowError):
            create_request(
                user=self.agent_user,
                agent=self.agent,
                subject="Sem agente",
                category="procedimento",
                description="Falta suporte.",
                request_type=OperationalSupportRequest.RequestType.PRESENCIAL,
            )

    def test_offline_request_cancel_after_assign_blocked(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="Dúvida sem urgência",
            category="procedimento",
            description="Pode ser respondida posteriormente.",
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )
        approved = leader_decision(user=self.leader, request_id=req.pk, approved=True)
        assign_request(user=self.cap, request_id=approved.pk)

        with self.assertRaises(WorkflowError):
            cancel_request(
                user=self.agent_user,
                request_id=approved.pk,
                reason="Não preciso mais",
            )

    def test_offline_leader_auto_approved(self):
        opened_by_leader = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Dúvida offline do líder",
            category="outros",
            description="Sem atendimento imediato.",
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )
        self.assertTrue(opened_by_leader.auto_approved)
        self.assertEqual(opened_by_leader.status, Status.PENDING_OFFLINE)

    def test_confer_origin_forces_canonical_values_and_preserves_offline_path(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="Valor enviado pelo cliente",
            category="procedimento",
            description="Dúvida da operação Confer.",
            workflow="Valor enviado pelo cliente",
            client="Outro cliente",
            workflow_id=123,
            cliente_id=456,
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )

        self.assertEqual(req.operation_origin, "confer")
        self.assertEqual(req.workflow, "Confer Web")
        self.assertEqual(req.client, "Claro")
        self.assertEqual(req.subject, "Confer")
        self.assertIsNone(req.workflow_id)
        self.assertIsNone(req.cliente_id)
        self.assertEqual(req.status, Status.PENDING_LEADER)
        self.assertEqual(req.request_type, OperationalSupportRequest.RequestType.OFFLINE)

        approved = leader_decision(user=self.leader, request_id=req.pk, approved=True)
        self.assertEqual(approved.status, Status.PENDING_OFFLINE)
        self.assertEqual(
            approved.events.get(sequence=1).snapshot["request"]["operation_origin"],
            "confer",
        )

    def test_any_leader_can_approve_and_reject(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="A",
            category="outros",
            description="B",
        )
        approved = leader_decision(user=self.leader2, request_id=req.pk, approved=True)
        self.assertEqual(approved.status, Status.PENDING_SUPPORT)

        req2 = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="C",
            category="outros",
            description="D",
        )
        with self.assertRaises(WorkflowError):
            leader_decision(user=self.leader, request_id=req2.pk, approved=False, justification="")
        rejected = leader_decision(
            user=self.leader, request_id=req2.pk, approved=False, justification="Fora de escopo"
        )
        self.assertEqual(rejected.status, Status.REJECTED_LEADER)

    def test_cancel_rules_and_events(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="A",
            category="sistema",
            description="B",
        )
        cancelled = cancel_request(user=self.agent_user, request_id=req.pk, reason="Desisti")
        self.assertEqual(cancelled.status, Status.CANCELLED)
        self.assertTrue(OperationalSupportRequest.objects.filter(pk=req.pk).exists())
        self.assertTrue(cancelled.events.filter(event_type="cancelled").exists())

        req2 = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="C",
            category="sistema",
            description="D",
        )
        leader_decision(user=self.leader, request_id=req2.pk, approved=True)
        cancel_request(user=self.agent_user, request_id=req2.pk, reason="Não preciso mais")

        req3 = create_request(
            user=self.agent2_user,
            agent=self.agent2,
            subject="E",
            category="sistema",
            description="F",
        )
        with self.assertRaises(WorkflowError) as ctx:
            cancel_request(user=self.agent_user, request_id=req3.pk, reason="Hack")
        self.assertEqual(ctx.exception.status_code, 403)

        req4 = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="G",
            category="sistema",
            description="H",
        )
        leader_decision(user=self.leader, request_id=req4.pk, approved=True)
        _assign_online_for_test(actor=self.cap, assignee=self.cap, request_id=req4.pk)
        with self.assertRaises(WorkflowError):
            cancel_request(user=self.leader, request_id=req4.pk, reason="Tarde demais")

    def test_support_assign_answer_and_blocks(self):
        pending = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="A",
            category="ferramenta",
            description="B",
        )
        with self.assertRaises(WorkflowError):
            assign_request(user=self.cap, request_id=pending.pk)

        leader_decision(user=self.leader, request_id=pending.pk, approved=True)
        with self.assertRaises(WorkflowError):
            assign_request(user=self.cap, request_id=pending.pk)
        assigned = _assign_online_for_test(
            actor=self.cap,
            assignee=self.cap,
            request_id=pending.pk,
        )
        self.assertEqual(assigned.status, Status.IN_ANALYSIS)

        answered = answer_request(
            user=self.cap,
            request_id=assigned.pk,
            answer="Faça assim.",
            difficulty_level="Médio",
            document_uf="SP",
            document_type="RG",
        )
        self.assertEqual(answered.status, Status.ANSWERED)
        self.assertEqual(answered.difficulty_level, "Médio")
        self.assertEqual(answered.document_uf, "SP")
        self.assertEqual(answered.document_type, "RG")
        with self.assertRaises(WorkflowError):
            answer_request(
                user=self.cap,
                request_id=answered.pk,
                answer="Outra",
                difficulty_level="Fácil",
                document_uf="RJ",
                document_type="CNH",
            )
        with self.assertRaises(WorkflowError):
            leader_decision(user=self.leader, request_id=answered.pk, approved=True)

    def test_complete_history_is_ordered_and_contains_frozen_snapshots(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="DOCUMENTO ILEGÍVEL",
            category="outros",
            description="Documento sem leitura.",
            protocol="PROTO-HISTORY-1",
            workflow="Documentoscopia",
            client="Cliente Histórico",
        )
        leader_decision(
            user=self.leader,
            request_id=req.pk,
            approved=True,
            justification="Seguir para análise.",
        )
        _assign_online_for_test(actor=self.cap, assignee=self.cap, request_id=req.pk)
        answer_request(
            user=self.cap,
            request_id=req.pk,
            answer="Solicitar novo documento.",
            difficulty_level="Médio",
            document_uf="SP",
            document_type="RG",
        )

        events = list(req.events.order_by("sequence"))
        self.assertEqual([event.sequence for event in events], [1, 2, 3, 4])
        self.assertEqual(
            [event.event_type for event in events],
            ["created", "approved", "queue_directed", "answered"],
        )
        self.assertEqual(
            [event.to_status for event in events],
            [Status.PENDING_LEADER, Status.PENDING_SUPPORT, Status.IN_ANALYSIS, Status.ANSWERED],
        )
        self.assertEqual(
            [event.snapshot["request"]["status"] for event in events],
            [Status.PENDING_LEADER, Status.PENDING_SUPPORT, Status.IN_ANALYSIS, Status.ANSWERED],
        )
        self.assertEqual(events[0].snapshot["request"]["protocol"], "PROTO-HISTORY-1")
        self.assertEqual(events[0].snapshot["request"]["request_type"], "online")
        self.assertEqual(events[0].snapshot["request"]["operation_origin"], "fraud")
        self.assertEqual(events[0].snapshot["request"]["agent"]["name"], "Agente Um")
        answer_snapshot = events[-1].snapshot["request"]["answer"]
        self.assertEqual(answer_snapshot["text"], "Solicitar novo documento.")
        self.assertEqual(answer_snapshot["difficulty_level"], "Médio")
        self.assertEqual(answer_snapshot["document_uf"], "SP")
        self.assertEqual(answer_snapshot["document_type"], "RG")
        self.assertEqual(answer_snapshot["answered_by"]["id"], str(self.cap.pk))
        self.assertIsNotNone(answer_snapshot["answered_at"])
        self.assertEqual(events[-1].actor_username, self.cap.username)
        self.assertEqual(events[-1].actor_name, self.cap.username)
        self.assertIn(ROLE_QUAL_CAPACITACAO, events[-1].actor_roles)
        self.assertEqual(events[-1].event_version, 1)

    def test_history_is_append_only_and_protects_parent_request(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="Histórico imutável",
            category="outros",
            description="Teste",
        )
        event = req.events.get(sequence=1)

        event.note = "Alteração indevida"
        with self.assertRaises(ValidationError):
            event.save(update_fields=["note"])
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            req.events.filter(pk=event.pk).update(note="Alteração indevida")
        with self.assertRaises(ValidationError):
            req.events.filter(pk=event.pk).delete()
        req.status = Status.ANSWERED
        with self.assertRaises(ValidationError):
            req.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            OperationalSupportRequest.objects.filter(pk=req.pk).update(status=Status.ANSWERED)
        with self.assertRaises(ProtectedError):
            req.delete()


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OperationalSupportApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agent_user = _user_with_role("api_agent1", ROLE_OP_AGENTE)
        self.agent2_user = _user_with_role("api_agent2", ROLE_OP_AGENTE)
        self.leader = _user_with_role("api_leader1", ROLE_OP_LIDER)
        self.cap = _user_with_role("api_cap1", ROLE_QUAL_CAPACITACAO)
        self.agent = Agent.objects.get(user_lan_id="api_agent1")
        self.agent2 = Agent.objects.get(user_lan_id="api_agent2")

    def test_no_delete_endpoint(self):
        self.client.force_authenticate(user=self.leader)
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="A",
            category="outros",
            description="B",
        )
        response = self.client.delete(f"{BASE}/requests/{req.pk}/")
        self.assertEqual(response.status_code, 405)

    def test_scopes_and_support_queue_hides_pending_leader(self):
        pending = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="Pendente líder",
            category="sistema",
            description="X",
        )
        approved = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Na fila",
            category="sistema",
            description="Y",
        )
        self.assertEqual(approved.status, Status.PENDING_SUPPORT)
        offline = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Fora da fila online",
            category="sistema",
            description="Atendimento offline",
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )
        self.assertEqual(offline.status, Status.PENDING_OFFLINE)

        self.client.force_authenticate(user=self.cap)
        queue = self.client.get(f"{BASE}/requests/", {"view": "support_queue"})
        self.assertEqual(queue.status_code, 200)
        ids = {row["id"] for row in queue.data["results"]}
        self.assertIn(str(approved.pk), ids)
        self.assertNotIn(str(pending.pk), ids)
        self.assertNotIn(str(offline.pk), ids)

        self.client.force_authenticate(user=self.agent_user)
        denied = self.client.post(f"{BASE}/requests/{approved.pk}/assign/")
        self.assertEqual(denied.status_code, 403)

    def test_history_includes_online_and_offline_requests(self):
        online = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Histórico online",
            category="sistema",
            description="Atendimento online concluído",
        )
        _assign_online_for_test(actor=self.cap, assignee=self.cap, request_id=online.pk)
        answer_request(
            user=self.cap,
            request_id=online.pk,
            answer="Resposta online",
            difficulty_level=OperationalSupportRequest.DifficultyLevel.EASY,
            document_uf="DF",
            document_type="RG",
        )

        offline = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Histórico offline",
            category="procedimento",
            description="Atendimento offline concluído",
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )
        assign_request(user=self.cap, request_id=offline.pk)
        answer_request(
            user=self.cap,
            request_id=offline.pk,
            answer="Resposta offline",
            difficulty_level=OperationalSupportRequest.DifficultyLevel.EASY,
            document_uf="SP",
            document_type="CNH",
        )

        self.client.force_authenticate(user=self.cap)
        response = self.client.get(f"{BASE}/requests/", {"view": "history"})

        self.assertEqual(response.status_code, 200)
        request_types = {
            row["id"]: row["request_type"]
            for row in response.data["results"]
            if row["id"] in {str(online.pk), str(offline.pk)}
        }
        self.assertEqual(
            request_types,
            {
                str(online.pk): OperationalSupportRequest.RequestType.ONLINE,
                str(offline.pk): OperationalSupportRequest.RequestType.OFFLINE,
            },
        )

    def test_list_includes_agent_leader_and_office(self):
        leader_agent = Agent.objects.get(user_lan_id=self.leader.username)
        AgentHistory.objects.filter(
            agent=self.agent2,
            active=True,
            final_date__isnull=True,
        ).update(leader=leader_agent, location="São Paulo")
        req = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Dúvida operacional",
            category="sistema",
            description="Detalhe",
        )

        self.client.force_authenticate(user=self.cap)
        response = self.client.get(f"{BASE}/requests/", {"view": "support_queue"})

        self.assertEqual(response.status_code, 200)
        row = next(item for item in response.data["results"] if item["id"] == str(req.pk))
        self.assertEqual(row["agent_leader_name"], leader_agent.full_name)
        self.assertEqual(row["agent_office"], "São Paulo")

    def test_list_includes_today_average_queue_time(self):
        started_at = timezone.make_aware(
            datetime.combine(timezone.localdate(), time(hour=8)),
            timezone.get_current_timezone(),
        )
        assigned_at = started_at + timedelta(minutes=2, seconds=3)
        answered_at = assigned_at + timedelta(hours=1)

        with patch("apps.suporte_operacional.services.workflow.timezone.now", return_value=started_at):
            req = create_request(
                user=self.leader,
                agent=self.agent2,
                subject="SLA médio",
                category="sistema",
                description="Medição",
            )

        with patch(
            "apps.suporte_operacional.services.fila_online.timezone.now",
            return_value=assigned_at,
        ):
            _assign_online_for_test(actor=self.cap, assignee=self.cap, request_id=req.pk)
        with patch("apps.suporte_operacional.services.workflow.timezone.now", return_value=answered_at):
            answer_request(
                user=self.cap,
                request_id=req.pk,
                answer="Resposta concluída",
                difficulty_level=OperationalSupportRequest.DifficultyLevel.EASY,
                document_uf="SP",
                document_type="RG",
            )

        self.client.force_authenticate(user=self.cap)
        response = self.client.get(f"{BASE}/requests/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["queue_average_seconds"], 123)

    def test_metadata_and_create_use_configured_question_and_projection_sla(self):
        client = DimCliente.objects.create(id_cliente=99001, nome="Cliente Suporte")
        workflow = DimWorkflow.objects.create(
            id_workflow=99002,
            nome="Workflow Suporte",
            ind_considerar=True,
        )
        level = DimNivelHierarquico.objects.create(id_nh=99003, nome="Nível Suporte")
        ProjecaoSla.objects.create(
            cliente=client,
            workflow=workflow,
            nivel_hierarquico=level,
            data_inicio=date(2026, 1, 1),
            dias_semana="{0,1,2,3,4}",
        )
        question = "DOCUMENTO ADULTERADO"

        self.client.force_authenticate(user=self.agent_user)
        metadata = self.client.get(f"{BASE}/metadata/")

        self.assertEqual(metadata.status_code, 200)
        self.assertFalse(metadata.data["support_available"])
        self.assertEqual(metadata.data["online_support_agents"], 0)
        self.assertIn(question, metadata.data["questions"])
        option = next(
            item
            for item in metadata.data["workflow_clients"]
            if item["workflow_id"] == workflow.id_workflow
            and item["client_id"] == client.id_cliente
        )

        response = self.client.post(
            f"{BASE}/requests/",
            {
                "agent_lan_id": self.agent.user_lan_id,
                "protocol": "PROTO-123",
                "workflow_client_key": option["key"],
                "question": question,
                "description": "Descrição da dúvida",
                "request_type": "offline",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["protocol"], "PROTO-123")
        self.assertEqual(response.data["workflow"], workflow.nome)
        self.assertEqual(response.data["client"], client.nome)
        self.assertEqual(response.data["subject"].casefold(), question.casefold())
        self.assertEqual(response.data["category"], "outros")
        self.assertEqual(response.data["reference"], "")
        self.assertEqual(response.data["request_type"], "offline")
        self.assertEqual(response.data["operation_origin"], "fraud")
        self.assertEqual(response.data["status"], Status.PENDING_LEADER)
        self.assertFalse(metadata.data["support_available"])
        self.assertTrue(
            AuditoriaCatalogItem.objects.filter(
                catalog=AuditoriaCatalogItem.CATALOG_DUVIDA_SUPORTE_OPERACIONAL,
                value=question,
                active=True,
            ).exists()
        )

    def test_create_presencial_request_via_api(self):
        client = DimCliente.objects.create(id_cliente=99011, nome="Cliente Presencial")
        workflow = DimWorkflow.objects.create(
            id_workflow=99012,
            nome="Workflow Presencial",
            ind_considerar=True,
        )
        level = DimNivelHierarquico.objects.create(id_nh=99013, nome="Nível Presencial")
        ProjecaoSla.objects.create(
            cliente=client,
            workflow=workflow,
            nivel_hierarquico=level,
            data_inicio=date(2026, 1, 1),
            dias_semana="{0,1,2,3,4}",
        )
        question = "DOCUMENTO ADULTERADO"

        self.client.force_authenticate(user=self.agent_user)
        metadata = self.client.get(f"{BASE}/metadata/")
        option = next(
            item
            for item in metadata.data["workflow_clients"]
            if item["workflow_id"] == workflow.id_workflow
            and item["client_id"] == client.id_cliente
        )

        response = self.client.post(
            f"{BASE}/requests/",
            {
                "agent_lan_id": self.agent.user_lan_id,
                "protocol": "PRES-API-1",
                "workflow_client_key": option["key"],
                "question": question,
                "description": "Atendimento presencial registrado.",
                "request_type": "presencial",
                "presencial_support_user_id": self.cap.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["request_type"], "presencial")
        self.assertEqual(response.data["status"], Status.PENDING_LEADER)
        self.assertEqual(response.data["presencial_support_user"], str(self.cap.id))

        req = OperationalSupportRequest.objects.get(pk=response.data["id"])
        self.assertEqual(req.presencial_support_user_id, self.cap.id)

    def test_create_confer_forces_canonical_values_without_projection_ids(self):
        self.client.force_authenticate(user=self.agent_user)

        response = self.client.post(
            f"{BASE}/requests/",
            {
                "agent_lan_id": self.agent.user_lan_id,
                "protocol": "CONFER-123",
                "operation_origin": "confer",
                "description": "Dúvida enviada pela operação Confer.",
                "request_type": "offline",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["operation_origin"], "confer")
        self.assertEqual(response.data["workflow"], "Confer Web")
        self.assertEqual(response.data["client"], "Claro")
        self.assertEqual(response.data["subject"], "Confer")
        self.assertEqual(response.data["request_type"], "offline")
        self.assertEqual(response.data["status"], Status.PENDING_LEADER)

        req = OperationalSupportRequest.objects.get(pk=response.data["id"])
        self.assertIsNone(req.workflow_id)
        self.assertIsNone(req.cliente_id)
        self.assertEqual(
            req.events.get(sequence=1).snapshot["request"]["operation_origin"],
            "confer",
        )

    def test_list_filters_and_validates_operation_origin(self):
        fraud = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Fraud",
            category="outros",
            description="Solicitacao Fraud",
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )
        confer = create_request(
            user=self.leader,
            agent=self.agent2,
            subject="Ignorado",
            category="outros",
            description="Solicitacao Confer",
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )
        self.client.force_authenticate(user=self.cap)

        response = self.client.get(
            f"{BASE}/requests/",
            {"operation_origin": "confer"},
        )
        invalid = self.client.get(
            f"{BASE}/requests/",
            {"operation_origin": "outra"},
        )

        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(str(confer.pk), ids)
        self.assertNotIn(str(fraud.pk), ids)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(invalid.data["detail"], "Origem da operação inválida.")

    def test_create_api_blocks_when_no_support_agent_is_online(self):
        self.client.force_authenticate(user=self.agent_user)

        response = self.client.post(f"{BASE}/requests/", {}, format="json")

        self.assertEqual(response.status_code, 409)
        self.assertIn("Nenhum agente de suporte", response.data["detail"])
        self.assertEqual(OperationalSupportRequest.objects.count(), 0)

    def test_answer_visible_to_agent_and_leader(self):
        req = create_request(
            user=self.agent_user,
            agent=self.agent,
            subject="A",
            category="procedimento",
            description="B",
        )
        leader_decision(user=self.leader, request_id=req.pk, approved=True)
        _assign_online_for_test(actor=self.cap, assignee=self.cap, request_id=req.pk)

        self.client.force_authenticate(user=self.cap)
        incomplete = self.client.post(
            f"{BASE}/requests/{req.pk}/answer/",
            {"answer": "Resposta incompleta"},
            format="json",
        )
        self.assertEqual(incomplete.status_code, 400)

        response = self.client.post(
            f"{BASE}/requests/{req.pk}/answer/",
            {
                "answer": "O documento apresentado não atende aos critérios definidos.",
                "answer_option": "Resposta final",
                "difficulty_level": "Difícil",
                "document_uf": "MG",
                "document_type": "Contrato social",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            [event["event_type"] for event in response.data["events"]],
            ["created", "approved", "queue_directed", "answered"],
        )
        self.assertEqual(
            [event["sequence"] for event in response.data["events"]],
            [1, 2, 3, 4],
        )
        answered_event = response.data["events"][-1]
        self.assertEqual(answered_event["actor_username"], self.cap.username)
        self.assertEqual(
            answered_event["snapshot"]["request"]["answer"]["document_type"],
            "Contrato social",
        )
        self.assertEqual(
            answered_event["snapshot"]["request"]["answer"]["option"],
            "Resposta final",
        )

        for user in (self.agent_user, self.leader):
            self.client.force_authenticate(user=user)
            detail = self.client.get(f"{BASE}/requests/{req.pk}/")
            self.assertEqual(detail.status_code, 200)
            self.assertEqual(
                detail.data["answer"],
                "O documento apresentado não atende aos critérios definidos.",
            )
            self.assertEqual(detail.data["answer_option"], "Resposta final")
            self.assertEqual(detail.data["difficulty_level"], "Difícil")
            self.assertEqual(detail.data["document_uf"], "MG")
            self.assertEqual(detail.data["document_type"], "Contrato social")
            self.assertEqual(detail.data["status"], Status.ANSWERED)

    def test_categories_endpoint(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.get(f"{BASE}/categories/")
        self.assertEqual(response.status_code, 200)
        codes = {row["code"] for row in response.data["results"]}
        self.assertIn("procedimento", codes)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OperationalSupportRaceTests(TransactionTestCase):
    def setUp(self):
        self.agent_user = _user_with_role("race_agent1", ROLE_OP_AGENTE)
        self.leader = _user_with_role("race_leader1", ROLE_OP_LIDER)
        self.cap = _user_with_role("race_cap1", ROLE_QUAL_CAPACITACAO)
        self.agent = Agent.objects.get(user_lan_id="race_agent1")
        self.req = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Corrida",
            category="sistema",
            description="Teste",
            request_type=OperationalSupportRequest.RequestType.OFFLINE,
        )

    def test_cancel_vs_assign_race(self):
        if connection.vendor == "sqlite":
            # SQLite em testes não serializa bem select_for_update entre threads.
            # Validamos exclusão mútua sequencial sob lock.
            assign_request(user=self.cap, request_id=self.req.pk)
            with self.assertRaises(WorkflowError):
                cancel_request(user=self.leader, request_id=self.req.pk, reason="tarde")
            self.req.refresh_from_db()
            self.assertEqual(self.req.status, Status.IN_ANALYSIS)
            return

        results: list[str] = []

        def do_cancel():
            close_old_connections()
            try:
                cancel_request(user=self.leader, request_id=self.req.pk, reason="cancel race")
                results.append("cancel")
            except WorkflowError:
                results.append("cancel_fail")
            finally:
                close_old_connections()

        def do_assign():
            close_old_connections()
            try:
                assign_request(user=self.cap, request_id=self.req.pk)
                results.append("assign")
            except WorkflowError:
                results.append("assign_fail")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(do_cancel)
            f2 = pool.submit(do_assign)
            f1.result()
            f2.result()

        self.req.refresh_from_db()
        self.assertIn(self.req.status, {Status.CANCELLED, Status.IN_ANALYSIS})
        self.assertEqual(len([r for r in results if r in {"cancel", "assign"}]), 1)
