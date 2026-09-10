"""Testes da fila online de suporte operacional."""

from __future__ import annotations

from datetime import date, timedelta

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_LIDER, ROLE_QUAL_CAPACITACAO, role_group_name
from apps.dimensoes_processos.models import DimCliente, DimNivelHierarquico, DimWorkflow, ProjecaoSla
from apps.suporte_operacional.models import (
    OperationalSupportAgentPresence,
    OperationalSupportEvent,
    OperationalSupportRequest,
)
from apps.suporte_operacional.services.fila_online import (
    FILA_TIMEOUT_SECONDS,
    FilaError,
    assumir_protocolo,
    build_controle_operacoes,
    claim_next_for_agent,
    direcionar_solicitacao,
    expire_stale_assignments,
    list_minha_fila,
    priorizar_solicitacao,
    set_agent_status,
)
from apps.suporte_operacional.services.workflow import (
    Status,
    _save_request,
    answer_request,
    create_request,
    leader_decision,
)
from apps.suporte_operacional.tests.test_workflow_api import _user_with_role, User
from apps.workforce.models import Agent, AgentHistory

BASE = "/api/v1/operational-support"


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class OperationalSupportFilaOnlineTests(TestCase):
    def setUp(self):
        self.leader = _user_with_role("fila_leader", ROLE_OP_LIDER)
        self.cap = _user_with_role("fila_cap", ROLE_QUAL_CAPACITACAO)
        self.agent = Agent.objects.get(user_lan_id="fila_leader")
        client = DimCliente.objects.create(id_cliente=88001, nome="Cliente Fila")
        workflow = DimWorkflow.objects.create(id_workflow=88002, nome="Workflow Fila", ind_considerar=True)
        level = DimNivelHierarquico.objects.create(id_nh=88003, nome="NH Fila")
        ProjecaoSla.objects.create(
            cliente=client,
            workflow=workflow,
            nivel_hierarquico=level,
            data_inicio=date(2026, 1, 1),
            dias_semana="{0,1,2,3,4,5,6}",
            hora_inicio="00:00",
            hora_fim="23:59",
            sla_segundos=3600,
        )
        self.req = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Dúvida fila",
            category="sistema",
            description="Teste fila online",
            workflow=workflow.nome,
            client=client.nome,
            workflow_id=workflow.id_workflow,
            cliente_id=client.id_cliente,
        )
        self.assertEqual(self.req.status, Status.PENDING_SUPPORT)

    def test_online_agent_claims_from_queue_when_online(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        assigned = claim_next_for_agent(user=self.cap)
        self.assertIsNotNone(assigned)
        self.assertEqual(assigned.status, Status.IN_ANALYSIS)
        self.assertEqual(assigned.assignee_id, self.cap.id)
        self.assertEqual(assigned.queue_slot, 1)

    def test_offline_agent_does_not_receive_auto_claim(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_OFFLINE)
        assigned = claim_next_for_agent(user=self.cap)
        self.assertIsNone(assigned)
        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.PENDING_SUPPORT)

    def test_changing_status_releases_protocol_back_to_general_queue(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        claim_next_for_agent(user=self.cap)

        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_PRESENCIAL)

        self.req.refresh_from_db()
        self.assertEqual(self.req.status, Status.PENDING_SUPPORT)
        self.assertIsNone(self.req.assignee_id)
        self.assertIsNone(self.req.queue_slot)
        self.assertIsNone(self.req.queue_sla_started_at)

    def test_assumir_zeros_timeout_and_keeps_protocol_with_agent(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        assigned = claim_next_for_agent(user=self.cap)
        self.assertIsNotNone(assigned.queue_sla_started_at)

        assumed = assumir_protocolo(user=self.cap, request_id=assigned.pk)

        assumed.refresh_from_db()
        self.assertEqual(assumed.status, Status.IN_ANALYSIS)
        self.assertEqual(assumed.assignee_id, self.cap.id)
        self.assertIsNone(assumed.queue_sla_started_at)
        payload = list_minha_fila(user=self.cap)
        current = payload["slots"][0]["request"]
        self.assertEqual(current["id"], str(assigned.pk))
        self.assertEqual(current["timeout_restante_segundos"], 0)
        self.assertFalse(current["timeout_ativo"])
        self.assertIsNotNone(current["analysis_started_at"])

    def test_assumir_rejects_expired_protocol_and_returns_it_to_queue(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        assigned = claim_next_for_agent(user=self.cap)
        assigned.queue_sla_started_at = timezone.now() - timedelta(seconds=FILA_TIMEOUT_SECONDS + 1)
        _save_request(assigned, update_fields=["queue_sla_started_at", "updated_at"])

        with self.assertRaises(FilaError):
            assumir_protocolo(user=self.cap, request_id=assigned.pk)

        assigned.refresh_from_db()
        self.assertEqual(assigned.status, Status.PENDING_SUPPORT)
        self.assertIsNone(assigned.assignee_id)

    def test_minha_fila_includes_agent_daily_results_sla_and_analysis_time(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        assigned = claim_next_for_agent(user=self.cap)
        assumir_protocolo(user=self.cap, request_id=assigned.pk)
        answer_request(
            user=self.cap,
            request_id=assigned.pk,
            answer="Devolutiva de teste.",
            difficulty_level=OperationalSupportRequest.DifficultyLevel.EASY,
            document_uf="SP",
            document_type="RG",
        )

        payload = list_minha_fila(user=self.cap)

        self.assertEqual(payload["metricas_hoje"]["realizados"], 1)
        self.assertIsNotNone(payload["metricas_hoje"]["sla_medio_pct"])
        self.assertIsNotNone(payload["metricas_hoje"]["tempo_medio_analise_segundos"])

    def test_controle_queue_includes_agent_leader_and_office(self):
        manager = Agent.objects.create(
            user_lan_id="fila_manager",
            full_name="Gestora da Fila",
            active=True,
        )
        history = AgentHistory.objects.get(
            agent=self.agent,
            active=True,
            final_date__isnull=True,
        )
        history.leader = manager
        history.location = "Escritório Recife"
        history.save(update_fields=["leader", "location"])

        payload = build_controle_operacoes()
        row = next(item for item in payload["fila_geral"] if item["id"] == str(self.req.pk))

        self.assertEqual(row["agent_lan_id"], self.agent.user_lan_id)
        self.assertEqual(row["agent_name"], self.agent.full_name)
        self.assertEqual(row["leader_name"], "Gestora da Fila")
        self.assertEqual(row["office"], "Escritório Recife")

    def test_controle_agent_metrics_are_limited_to_today(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        assigned = claim_next_for_agent(user=self.cap)
        answer_request(
            user=self.cap,
            request_id=assigned.pk,
            answer="Devolutiva de hoje.",
            difficulty_level=OperationalSupportRequest.DifficultyLevel.EASY,
            document_uf="SP",
            document_type="RG",
        )
        timed_out = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Protocolo com timeout",
            category="sistema",
            description="Teste de timeout diário",
            workflow=self.req.workflow,
            client=self.req.client,
            workflow_id=self.req.workflow_id,
            cliente_id=self.req.cliente_id,
        )
        claimed_timeout = claim_next_for_agent(user=self.cap)
        self.assertEqual(claimed_timeout.pk, timed_out.pk)
        claimed_timeout.queue_sla_started_at = timezone.now() - timedelta(
            seconds=FILA_TIMEOUT_SECONDS + 1,
        )
        _save_request(claimed_timeout, update_fields=["queue_sla_started_at", "updated_at"])
        expire_stale_assignments(actor=self.cap)

        payload = build_controle_operacoes()
        agent = next(item for item in payload["agentes"] if item["user_id"] == self.cap.id)

        self.assertEqual(agent["respondidas_hoje"], 1)
        self.assertIsNotNone(agent["sla_medio_hoje_pct"])
        self.assertEqual(agent["timeouts_hoje"], 1)
        self.assertNotIn("answered_total", agent)
        self.assertIsNotNone(payload["kpis"]["sla_medio_hoje_pct"])

    def test_direcionar_fills_slot_two(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        claim_next_for_agent(user=self.cap)
        req2 = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Segunda dúvida",
            category="sistema",
            description="Slot 2",
            workflow=self.req.workflow,
            client=self.req.client,
            workflow_id=self.req.workflow_id,
            cliente_id=self.req.cliente_id,
        )
        directed = direcionar_solicitacao(
            actor=self.cap,
            agent_id=self.cap.id,
            request_id=req2.pk,
            justificativa="Prioridade operacional.",
        )
        self.assertEqual(directed.queue_slot, 2)
        payload = list_minha_fila(user=self.cap)
        self.assertEqual(len(payload["slots"]), 2)
        self.assertIsNotNone(payload["slots"][1]["request"])

    def test_priorizar_moves_protocol_to_next_and_priority_is_consumed(self):
        prioritized = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Prioridade da fila",
            category="sistema",
            description="Deve ser o próximo protocolo",
            workflow=self.req.workflow,
            client=self.req.client,
            workflow_id=self.req.workflow_id,
            cliente_id=self.req.cliente_id,
        )

        priorizar_solicitacao(actor=self.cap, request_id=prioritized.pk)

        payload = build_controle_operacoes()
        self.assertEqual(payload["fila_geral"][0]["id"], str(prioritized.pk))
        self.assertIsNotNone(payload["fila_geral"][0]["queue_priority_at"])
        self.assertTrue(
            OperationalSupportEvent.objects.filter(
                request=prioritized,
                event_type=OperationalSupportEvent.EventType.QUEUE_PRIORITIZED,
            ).exists()
        )

        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        assigned = claim_next_for_agent(user=self.cap)
        self.assertEqual(assigned.pk, prioritized.pk)
        assigned.refresh_from_db()
        self.assertIsNone(assigned.queue_priority_at)

    def test_priorizar_api_requires_and_uses_selected_protocol(self):
        prioritized = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Prioridade via API",
            category="sistema",
            description="Teste do botão Priorizar",
            workflow=self.req.workflow,
            client=self.req.client,
            workflow_id=self.req.workflow_id,
            cliente_id=self.req.cliente_id,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.cap)

        response = self.client.post(
            f"{BASE}/fila/priorizar/",
            {"request_id": str(prioritized.pk)},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["id"], str(prioritized.pk))
        prioritized.refresh_from_db()
        self.assertIsNotNone(prioritized.queue_priority_at)

    def test_operation_list_and_detail_show_current_queue_position(self):
        prioritized = create_request(
            user=self.leader,
            agent=self.agent,
            subject="Posição prioritária",
            category="sistema",
            description="Deve aparecer primeiro para o solicitante",
            workflow=self.req.workflow,
            client=self.req.client,
            workflow_id=self.req.workflow_id,
            cliente_id=self.req.cliente_id,
        )
        priorizar_solicitacao(actor=self.cap, request_id=prioritized.pk)
        self.client = APIClient()
        self.client.force_authenticate(user=self.leader)

        response = self.client.get(f"{BASE}/requests/")

        self.assertEqual(response.status_code, 200)
        rows = {row["id"]: row for row in response.data["results"]}
        self.assertEqual(rows[str(prioritized.pk)]["queue_position"], 1)
        self.assertEqual(rows[str(self.req.pk)]["queue_position"], 2)

        detail = self.client.get(f"{BASE}/requests/{self.req.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["queue_position"], 2)

    def test_direcionar_api_accepts_selected_agent_id(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.cap)

        response = self.client.post(
            f"{BASE}/fila/direcionar/",
            {
                "agent_id": self.cap.id,
                "request_id": str(self.req.pk),
                "justificativa": "Direcionamento pelo painel.",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["assignee_id"], self.cap.id)
        self.req.refresh_from_db()
        self.assertEqual(self.req.assignee_id, self.cap.id)

    def test_presence_api(self):
        self.client = APIClient()
        self.client.force_authenticate(user=self.cap)
        response = self.client.post(f"{BASE}/presence/me/", {"status": "online"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "online")

    def test_presencial_agents_are_scoped_to_viewer_location_and_hide_username(self):
        AgentHistory.objects.filter(agent=self.agent, final_date__isnull=True).update(
            location="São Carlos"
        )
        cap_agent = Agent.objects.get(user_lan_id=self.cap.username)
        AgentHistory.objects.filter(agent=cap_agent, final_date__isnull=True).update(
            location="São Carlos"
        )
        other = _user_with_role(
            "fila_cap_bsb",
            ROLE_QUAL_CAPACITACAO,
            full_name="Suporte Brasília",
        )
        other_agent = Agent.objects.get(user_lan_id=other.username)
        AgentHistory.objects.filter(agent=other_agent, final_date__isnull=True).update(
            location="Brasília"
        )
        set_agent_status(
            user=self.cap,
            status=OperationalSupportAgentPresence.STATUS_PRESENCIAL,
        )
        set_agent_status(
            user=other,
            status=OperationalSupportAgentPresence.STATUS_PRESENCIAL,
        )

        self.client = APIClient()
        self.client.force_authenticate(user=self.leader)
        response = self.client.get(f"{BASE}/presence/presencial/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["name"] for item in response.data["results"]], [cap_agent.full_name])
        self.assertNotIn("username", response.data["results"][0])

    def test_assumir_api_returns_current_protocol_with_zero_timeout(self):
        set_agent_status(user=self.cap, status=OperationalSupportAgentPresence.STATUS_ONLINE)
        assigned = claim_next_for_agent(user=self.cap)
        self.client = APIClient()
        self.client.force_authenticate(user=self.cap)

        response = self.client.post(f"{BASE}/fila/me/{assigned.pk}/assumir/", format="json")

        self.assertEqual(response.status_code, 200)
        current = response.data["slots"][0]["request"]
        self.assertEqual(current["id"], str(assigned.pk))
        self.assertEqual(current["timeout_restante_segundos"], 0)
        self.assertFalse(current["timeout_ativo"])

    def test_fila_timeout_constant(self):
        self.assertEqual(FILA_TIMEOUT_SECONDS, 60)
