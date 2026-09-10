from datetime import date
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.workforce.models import Agent, AgentHistory


class AgentsApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("workforce-agent-admin")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        user.groups.add(group)
        self.client.force_login(user)
        self.leader = Agent.objects.create(
            user_lan_id="c94001a",
            full_name="Leader Agent",
            active=True,
            hire_date=date(2019, 1, 1),
        )
        self.agent = Agent.objects.create(
            user_lan_id="c94002a",
            full_name="Bulk Test Agent",
            email="old@example.com",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        self.other = Agent.objects.create(
            user_lan_id="c94003a",
            full_name="Other Agent",
            active=True,
            hire_date=date(2021, 1, 1),
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Operacional/Fraud",
            job_title="Agente Backoffice II",
            job_activity="Análise de fraude",
            location="SP",
            journey="8h",
            band="B1",
            start_date=date(2024, 1, 1),
            final_date=None,
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Operacional/Fraud",
            job_title="Agente Backoffice I",
            location="SP",
            start_date=date(2023, 1, 1),
            final_date=date(2023, 12, 31),
            active=False,
        )

    def test_agents_list_includes_current_history(self):
        response = self.client.get("/api/v1/agents/?search=c94002a")
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        current = results[0]["current_history"]
        self.assertIsNotNone(current)
        self.assertEqual(current["job_title"], "Agente Backoffice II")
        self.assertEqual(current["job_activity"], "Análise de fraude")
        self.assertEqual(current["team"], "Operacional/Fraud")
        self.assertEqual(current["leader_name"], "Leader Agent")

    def test_agents_list_can_opt_out_current_history(self):
        response = self.client.get(
            "/api/v1/agents/?search=c94002a&include_current_history=false"
        )
        self.assertEqual(response.status_code, 200)
        item = response.json()["results"][0]
        self.assertNotIn("current_history", item)

    def test_agents_bulk_patch_updates_allowed_fields(self):
        response = self.client.post(
            "/api/v1/agents/bulk-patch/",
            data={
                "agent_ids": [str(self.agent.id), str(self.other.id)],
                "updates": {"active": False, "email": "bulk@example.com"},
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["updated"], 2)
        self.assertEqual(body["errors"], [])

        self.agent.refresh_from_db()
        self.other.refresh_from_db()
        self.assertFalse(self.agent.active)
        self.assertFalse(self.other.active)
        self.assertEqual(self.agent.email, "bulk@example.com")
        self.assertEqual(self.other.email, "bulk@example.com")

    def test_agents_bulk_patch_rejects_name_and_lan_id(self):
        response = self.client.post(
            "/api/v1/agents/bulk-patch/",
            data={
                "agent_ids": [str(self.agent.id)],
                "updates": {"full_name": "Novo Nome"},
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("full_name", response.json()["detail"])

        response = self.client.post(
            "/api/v1/agents/bulk-patch/",
            data={
                "agent_ids": [str(self.agent.id)],
                "updates": {"user_lan_id": "novo123"},
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("user_lan_id", response.json()["detail"])

    def test_agents_bulk_patch_reports_missing_ids(self):
        missing_id = str(uuid.uuid4())
        response = self.client.post(
            "/api/v1/agents/bulk-patch/",
            data={
                "agent_ids": [str(self.agent.id), missing_id],
                "updates": {"active": True},
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["updated"], 1)
        self.assertEqual(len(body["errors"]), 1)
        self.assertEqual(body["errors"][0]["agent_id"], missing_id)

    def test_agents_column_filter_options_returns_distinct_values(self):
        response = self.client.get("/api/v1/agents/column-filter-options/")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("job_title", body)
        self.assertIn("Agente Backoffice II", body["job_title"])
        self.assertIn("Other Agent", body["full_name"])

    def test_agents_list_column_filter_job_title(self):
        response = self.client.get(
            "/api/v1/agents/?col_job_title=Agente Backoffice II&search=c94002a"
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["user_lan_id"], "c94002a")

        response = self.client.get(
            "/api/v1/agents/?col_job_title=Agente Backoffice I&search=c94002a"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 0)
