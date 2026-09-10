from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PLAN_ANALISTA, role_group_name
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


class ApplyCycleChangeApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.leader = Agent.objects.create(
            user_lan_id="c95001a",
            full_name="Leader One",
            active=True,
            hire_date=date(2019, 1, 1),
        )
        self.new_leader = Agent.objects.create(
            user_lan_id="c95002a",
            full_name="Leader Two",
            active=True,
            hire_date=date(2018, 1, 1),
        )
        self.agent = Agent.objects.create(
            user_lan_id="c95003a",
            full_name="Cycle Agent",
            email="cycle@example.com",
            active=True,
            hire_date=date(2020, 1, 1),
            time_tracking_id="111",
            oracle_id="222",
        )
        self.open_history = AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Operacional/Compliance",
            job_title="Agente Backoffice I",
            job_activity="Análise",
            location="São Carlos",
            journey="14:00 - 20:00",
            band="B1",
            pcd=False,
            start_date=date(2024, 5, 31),
            final_date=None,
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Operacional/Compliance",
            job_title="Agente Backoffice I",
            location="São Carlos",
            start_date=date(2023, 1, 1),
            final_date=date(2024, 5, 30),
            active=False,
        )

        self.plan_user = User.objects.create_user(
            "plan.cycle", email="plan.cycle@test.local", password="test123"
        )
        self.op_user = User.objects.create_user(
            "op.cycle", email="op.cycle@test.local", password="test123"
        )
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        self.plan_user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))
        self.op_user.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))
        self.client.force_authenticate(user=self.plan_user)

    def _url(self, agent_id=None):
        return f"/api/v1/agents/{agent_id or self.agent.id}/apply-cycle-change/"

    def test_leader_change_closes_and_opens_cycle(self):
        response = self.client.post(
            self._url(),
            data={
                "action": "update",
                "movement_date": "2026-07-15",
                "cycle": {
                    "team": "Operacional/Compliance",
                    "job_title": "Agente Backoffice I",
                    "location": "São Carlos",
                    "leader": str(self.new_leader.id),
                    "facilitator": None,
                    "journey": "14:00 - 20:00",
                    "band": "B1",
                    "pcd": False,
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["active"])
        self.assertEqual(body["current_history"]["leader"], str(self.new_leader.id))
        self.assertEqual(body["current_history"]["start_date"], "2026-07-15")

        self.open_history.refresh_from_db()
        self.assertFalse(self.open_history.active)
        self.assertEqual(self.open_history.final_date, date(2026, 7, 15))
        self.assertEqual(self.open_history.leader_id, self.leader.id)

        open_count = AgentHistory.objects.filter(
            agent=self.agent, active=True, final_date__isnull=True
        ).count()
        self.assertEqual(open_count, 1)
        self.assertEqual(AgentHistory.objects.filter(agent=self.agent).count(), 3)

    def test_terminate_closes_only_and_deactivates_agent(self):
        response = self.client.post(
            self._url(),
            data={
                "action": "terminate",
                "movement_date": "2026-07-10",
                "external_movement_type": "Desligamento Voluntário",
                "agent": {"email": "ex@example.com"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["active"])
        self.assertIsNone(body.get("current_history"))

        self.agent.refresh_from_db()
        self.assertFalse(self.agent.active)
        self.assertEqual(self.agent.email, "ex@example.com")

        self.open_history.refresh_from_db()
        self.assertFalse(self.open_history.active)
        self.assertEqual(self.open_history.final_date, date(2026, 7, 10))
        self.assertEqual(self.open_history.external_movement_type, "Desligamento Voluntário")

        open_count = AgentHistory.objects.filter(
            agent=self.agent, active=True, final_date__isnull=True
        ).count()
        self.assertEqual(open_count, 0)

    def test_cadastral_only_keeps_history(self):
        response = self.client.post(
            self._url(),
            data={
                "action": "update",
                "agent": {
                    "email": "novo@example.com",
                    "time_tracking_id": "999",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["email"], "novo@example.com")
        self.assertEqual(body["time_tracking_id"], "999")
        self.assertEqual(body["current_history"]["id"], str(self.open_history.id))

        self.open_history.refresh_from_db()
        self.assertTrue(self.open_history.active)
        self.assertIsNone(self.open_history.final_date)
        self.assertEqual(AgentHistory.objects.filter(agent=self.agent).count(), 2)

    def test_movement_date_before_start_returns_400(self):
        response = self.client.post(
            self._url(),
            data={
                "action": "update",
                "movement_date": "2024-01-01",
                "cycle": {"leader": str(self.new_leader.id)},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("movement_date", response.json())

    def test_identical_cycle_without_date_is_noop(self):
        response = self.client.post(
            self._url(),
            data={
                "action": "update",
                "cycle": {
                    "team": "Operacional/Compliance",
                    "job_title": "Agente Backoffice I",
                    "location": "São Carlos",
                    "leader": str(self.leader.id),
                    "journey": "14:00 - 20:00",
                    "band": "B1",
                    "pcd": False,
                },
                "agent": {"email": "same-cycle@example.com"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["email"], "same-cycle@example.com")
        self.open_history.refresh_from_db()
        self.assertTrue(self.open_history.active)
        self.assertIsNone(self.open_history.final_date)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_op_agente_denied_apply_cycle_change(self):
        self.client.force_authenticate(user=self.op_user)
        response = self.client.post(
            self._url(),
            data={
                "action": "terminate",
                "movement_date": "2026-07-10",
                "external_movement_type": "Desligamento Involuntário",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_plan_analista_can_apply_cycle_change(self):
        response = self.client.post(
            self._url(),
            data={
                "action": "update",
                "movement_date": "2026-07-15",
                "cycle": {"band": "B2"},
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["current_history"]["band"], "B2")

    def test_cycle_extra_fields_and_auto_shift(self):
        response = self.client.post(
            self._url(),
            data={
                "action": "update",
                "movement_date": "2026-07-15",
                "external_movement_type": "Promoção externa",
                "cycle": {
                    "journey": "08:00 - 17:00",
                    "job_activity": "Análise Visual",
                    "team_sector": "Operação",
                    "job_title_sector": "Backoffice",
                    "job_title_activity": "GA",
                    "inss_type": "Licença maternidade",
                    "productivity_discount": "01:30:00",
                    "formalization": "https://example.com/doc",
                },
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        current = response.json()["current_history"]
        self.assertEqual(current["journey_shift"], "Integral")
        self.assertEqual(current["job_activity"], "Análise Visual")
        self.assertEqual(current["team_sector"], "Operação")
        self.assertEqual(current["inss_type"], "Licença maternidade")
        self.assertEqual(str(current["productivity_discount"]), "1.50")
        self.assertEqual(current["formalization"], "https://example.com/doc")

        self.open_history.refresh_from_db()
        self.assertEqual(self.open_history.external_movement_type, "Promoção externa")
