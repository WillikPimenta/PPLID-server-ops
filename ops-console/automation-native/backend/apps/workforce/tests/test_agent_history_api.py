from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.workforce.models import Agent, AgentHistory


class AgentHistoryApiTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("workforce-history-admin")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        user.groups.add(group)
        self.client.force_login(user)
        self.agent = Agent.objects.create(
            user_lan_id="c93001a",
            full_name="Hist Test Agent",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        self.leader = Agent.objects.create(
            user_lan_id="c93002a",
            full_name="Leader Agent",
            active=True,
            hire_date=date(2019, 1, 1),
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
        AgentHistory.objects.create(
            agent=self.agent,
            leader=self.leader,
            team="Operacional/Fraud",
            job_title="Agente Backoffice II",
            location="SP",
            start_date=date(2024, 1, 1),
            final_date=None,
            active=True,
        )
        other = Agent.objects.create(
            user_lan_id="c93003a",
            full_name="Other Agent",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        AgentHistory.objects.create(
            agent=other,
            team="Outro Time",
            job_title="Analista",
            location="RJ",
            start_date=date(2024, 6, 1),
            active=True,
        )

    def test_filter_by_lan_id(self):
        response = self.client.get("/api/v1/agent-history/?lan_id=C93001A")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 2)
        self.assertTrue(all(item["agent_name"] == "Hist Test Agent" for item in data["results"]))

    def test_filter_by_active(self):
        response = self.client.get(
            f"/api/v1/agent-history/?agent={self.agent.id}&active=true"
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["count"], 1)
        self.assertTrue(data["results"][0]["active"])
        self.assertIsNone(data["results"][0]["final_date"])

    def test_filter_by_team(self):
        response = self.client.get("/api/v1/agent-history/?team__icontains=Operacional/Fraud")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 2)

    def test_ordering_start_date_asc(self):
        response = self.client.get(
            f"/api/v1/agent-history/?agent={self.agent.id}&ordering=start_date"
        )
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(results[0]["job_title"], "Agente Backoffice I")
        self.assertEqual(results[1]["job_title"], "Agente Backoffice II")
