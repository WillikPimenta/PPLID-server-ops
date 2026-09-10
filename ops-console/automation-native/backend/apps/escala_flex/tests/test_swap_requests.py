"""Testes do fluxo de solicitação de troca de escala."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.escala_flex.models import Escala, RequestType, Schedule, ScheduleRequest
from apps.escala_flex.services.swap_request_workflow import SWAP_REQUEST_TYPE_NAME
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class SwapRequestWorkflowTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        RequestType.objects.get_or_create(pk=1, defaults={"name": SWAP_REQUEST_TYPE_NAME, "active": True})

        self.leader = Agent.objects.create(user_lan_id="lider01", full_name="Líder Um", active=True)
        self.agent = Agent.objects.create(user_lan_id="agent01", full_name="Agente Um", active=True)
        self.partner = Agent.objects.create(user_lan_id="agent02", full_name="Agente Dois", active=True)
        today = timezone.localdate()
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title="Agente Backoffice I",
            job_activity="Atendimento",
            start_date=today - timedelta(days=30),
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.partner,
            leader=self.leader,
            location="Brasília",
            team="Operacional",
            job_title="Agente Backoffice I",
            job_activity="Atendimento",
            start_date=today - timedelta(days=30),
            active=True,
        )

        self.leader_user = User.objects.create_user(
            username="lider01", password="test12345", email="lider01@test.local"
        )
        UserProfile.objects.create(user=self.leader_user, agent=self.leader)
        self.agent_user = User.objects.create_user(
            username="agent01", password="test12345", email="agent01@test.local"
        )
        UserProfile.objects.create(user=self.agent_user, agent=self.agent)

        self.planner = User.objects.create_user(
            username="planejador",
            password="test12345",
            email="planejador@test.local",
            is_staff=True,
        )
        self.planner_agent = Agent.objects.create(
            user_lan_id="planejador",
            full_name="Planejador",
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.planner_agent,
            location="Brasília",
            team="Planejamento",
            job_title="Planejador",
            start_date=today - timedelta(days=30),
            active=True,
        )
        UserProfile.objects.create(user=self.planner, agent=self.planner_agent)
        self.today = today
        self.swap_date = today + timedelta(days=3)
        Escala.objects.create(
            agent=self.agent,
            leader=self.leader,
            data=self.swap_date,
            horario="08:00 - 17:00",
            dia_escala="08:00 - 17:00",
        )
        Escala.objects.create(
            agent=self.partner,
            leader=self.leader,
            data=self.swap_date,
            horario="14:00 - 20:00",
            dia_escala="14:00 - 20:00",
        )

    def _create_by_agent(self, **extra):
        payload = {
            "swap_kind": "shift_bh",
            "agent_lan_id": "agent01",
            "date_swap": str(self.swap_date),
            "description": "Troca solicitada pelo agente",
            **extra,
        }
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            payload,
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        return response.json()

    def _prepare_peer_schedules(self):
        start = self.swap_date - timedelta(days=3)
        for agent in (self.agent, self.partner):
            for offset in range(7):
                target = start + timedelta(days=offset)
                is_work = offset != 0
                Schedule.objects.update_or_create(
                    agent=agent,
                    date=target,
                    defaults={
                        "work_schedule": "08:00 - 14:00" if is_work else "",
                        "work_day": is_work,
                    },
                )
                if target != self.swap_date:
                    published_schedule = "08:00 - 14:00" if is_work else "FOLGA"
                    Escala.objects.update_or_create(
                        agent=agent,
                        data=target,
                        defaults={
                            "leader": self.leader,
                            "horario": published_schedule,
                            "dia_escala": published_schedule,
                        },
                    )
        Schedule.objects.update_or_create(
            agent=self.partner,
            date=self.swap_date,
            defaults={"work_schedule": "14:00 - 20:00", "work_day": True},
        )

    def _create_peer_by_agent(self):
        self._prepare_peer_schedules()
        return self._create_by_agent(
            swap_kind="peer",
            agent_lan_id_2="agent02",
            description="Troca entre colaboradores",
        )

    def test_stats_endpoint_returns_dashboard_payload(self):
        self._create_by_agent()

        self.client.force_authenticate(user=self.planner)
        response = self.client.get("/api/v1/escala-flex/swap-requests/stats/")
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertIn("summary", payload)
        self.assertGreaterEqual(payload["summary"]["total"], 1)
        self.assertIn("by_kind", payload)
        self.assertIn("by_month", payload)
        self.assertIn("by_weekday", payload)
        self.assertIn("by_activity", payload)

    def test_stats_endpoint_accepts_query_params(self):
        self._create_by_agent()

        self.client.force_authenticate(user=self.planner)
        response = self.client.get(
            "/api/v1/escala-flex/swap-requests/stats/",
            {"date_from": "2026-01-07", "date_to": str(self.swap_date)},
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIn("summary", response.json())

    def test_agent_request_starts_pending_leader(self):
        payload = self._create_by_agent()
        self.assertEqual(payload["workflow_status"], "pending_leader")
        self.assertIsNone(payload["approved_leader"])

    def test_leader_create_auto_approves_leader_step(self):
        self.client.force_authenticate(user=self.leader_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_bh",
                "agent_lan_id": "agent01",
                "date_swap": str(self.swap_date),
                "description": "Troca pelo líder",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        payload = response.json()
        self.assertEqual(payload["workflow_status"], "pending_plan")
        self.assertTrue(payload["approved_leader"])

    def test_leader_approval_moves_to_pending_plan(self):
        created = self._create_by_agent()
        self.client.force_authenticate(user=self.leader_user)
        response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-leader/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["workflow_status"], "pending_plan")

    def test_peer_with_three_criteria_is_applied_after_leader_approval(self):
        created = self._create_peer_by_agent()
        self.assertEqual(created["agent_activity"], "Atendimento")
        self.assertEqual(created["agent_activity_2"], "Atendimento")

        self.client.force_authenticate(user=self.leader_user)
        response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-leader/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        self.assertEqual(payload["workflow_status"], "approved")
        self.assertTrue(payload["leader_fast_tracked"])
        self.assertIsNone(payload["date_approve_plan"])
        self.assertEqual(
            Escala.objects.get(agent=self.agent, data=self.swap_date).dia_escala,
            "14:00 - 20:00",
        )
        self.assertEqual(
            Escala.objects.get(agent=self.partner, data=self.swap_date).dia_escala,
            "08:00 - 17:00",
        )

    def test_peer_created_by_leader_is_immediately_applied(self):
        self._prepare_peer_schedules()
        self.client.force_authenticate(user=self.leader_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "peer",
                "agent_lan_id": "agent01",
                "agent_lan_id_2": "agent02",
                "date_swap": str(self.swap_date),
                "description": "Troca solicitada pelo líder",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["workflow_status"], "approved")
        self.assertTrue(response.json()["leader_fast_tracked"])
        self.assertEqual(
            Escala.objects.get(agent=self.agent, data=self.swap_date).dia_escala,
            "14:00 - 20:00",
        )

    def test_peer_with_different_activity_goes_to_planning_before_application(self):
        partner_history = self.partner.history.get(active=True)
        partner_history.job_activity = "Backoffice"
        partner_history.save(update_fields=["job_activity"])
        created = self._create_peer_by_agent()

        self.client.force_authenticate(user=self.leader_user)
        leader_response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-leader/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(leader_response.status_code, 200, leader_response.content)
        self.assertEqual(leader_response.json()["workflow_status"], "pending_plan")
        self.assertEqual(
            Escala.objects.get(agent=self.agent, data=self.swap_date).dia_escala,
            "08:00 - 17:00",
        )

        self.client.force_authenticate(user=self.planner)
        plan_response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-plan/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(plan_response.status_code, 200, plan_response.content)
        self.assertEqual(plan_response.json()["workflow_status"], "approved")
        self.assertEqual(
            Escala.objects.get(agent=self.agent, data=self.swap_date).dia_escala,
            "14:00 - 20:00",
        )

    def test_plan_approval_finalizes_request(self):
        created = self._create_by_agent()
        swap = ScheduleRequest.objects.get(pk=created["id"])
        swap.approved_leader = True
        swap.save(update_fields=["approved_leader"])

        self.client.force_authenticate(user=self.planner)
        response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-plan/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["workflow_status"], "approved")
        self.assertEqual(
            Escala.objects.get(agent=self.agent, data=self.swap_date).dia_escala,
            "BH",
        )

    def test_shift_schedule_is_applied_only_after_planning_approval(self):
        created = self._create_by_agent(
            swap_kind="shift_schedule",
            new_journey="09:00 - 15:00",
        )
        self.client.force_authenticate(user=self.leader_user)
        leader_response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-leader/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(leader_response.status_code, 200, leader_response.content)
        self.assertEqual(leader_response.json()["workflow_status"], "pending_plan")
        self.assertEqual(
            Escala.objects.get(agent=self.agent, data=self.swap_date).dia_escala,
            "08:00 - 17:00",
        )

        self.client.force_authenticate(user=self.planner)
        plan_response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-plan/",
            {"approved": True},
            format="json",
        )
        self.assertEqual(plan_response.status_code, 200, plan_response.content)
        self.assertEqual(plan_response.json()["workflow_status"], "approved")
        self.assertEqual(
            Escala.objects.get(agent=self.agent, data=self.swap_date).dia_escala,
            "09:00 - 15:00",
        )

    def test_agent_cannot_create_for_other_agent(self):
        other = Agent.objects.create(user_lan_id="agent03", full_name="Agente Três", active=True)
        Escala.objects.create(
            agent=other,
            leader=self.leader,
            data=self.swap_date,
            horario="08:00 - 17:00",
            dia_escala="08:00 - 17:00",
        )
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.post(
            "/api/v1/escala-flex/swap-requests/",
            {
                "swap_kind": "shift_bh",
                "agent_lan_id": "agent03",
                "date_swap": str(self.swap_date),
                "description": "Tentativa indevida",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_leader_rejection_marks_rejected(self):
        created = self._create_by_agent()
        self.client.force_authenticate(user=self.leader_user)
        response = self.client.post(
            f"/api/v1/escala-flex/swap-requests/{created['id']}/approve-leader/",
            {"approved": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["workflow_status"], "rejected")
