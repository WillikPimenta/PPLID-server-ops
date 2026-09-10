from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PLAN_ANALISTA, role_group_name
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


class HeadcountAccessTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agent_user = User.objects.create_user(
            "c94002a", email="c94002a@test.local", password="test123"
        )
        self.plan_user = User.objects.create_user(
            "c94003a", email="c94003a@test.local", password="test123"
        )

        agent = Agent.objects.create(
            user_lan_id="c94002a",
            full_name="Agente Operacional",
            email="agente@test.local",
            hire_date="2024-01-01",
            active=True,
        )
        AgentHistory.objects.create(
            agent=agent,
            start_date="2024-01-01",
            team="Operacional/Alpha",
            job_title="Agente Backoffice I",
            active=True,
        )

        plan_agent = Agent.objects.create(
            user_lan_id="c94003a",
            full_name="Analista Planejamento",
            email="plan@test.local",
            hire_date="2023-01-01",
            active=True,
        )
        AgentHistory.objects.create(
            agent=plan_agent,
            start_date="2023-01-01",
            team="Planejamento",
            job_title="Analista de Planejamento",
            active=True,
        )

        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.agent_user.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))
        self.plan_user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_op_agente_denied_headcount_list(self):
        self.client.force_authenticate(user=self.agent_user)
        response = self.client.get("/api/v1/agents/")
        self.assertEqual(response.status_code, 403)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_plan_analista_can_list_headcount(self):
        self.client.force_authenticate(user=self.plan_user)
        response = self.client.get("/api/v1/agents/")
        self.assertEqual(response.status_code, 200)
