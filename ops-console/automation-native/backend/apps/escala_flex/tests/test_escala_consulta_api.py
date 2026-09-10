"""Testes da API de consulta de escala (Escala Flex)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from apps.access.constants import (
    ROLE_OP_AGENTE,
    ROLE_OP_LIDER,
    ROLE_PLAN_ANALISTA,
    role_group_name,
)
from apps.escala_flex.models import Escala, JobActivity, Location
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


@override_settings(ESCALA_FLEX_OPEN_ACCESS=False, ACCESS_ENFORCEMENT=True)
class EscalaConsultaApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.leader = Agent.objects.create(
            user_lan_id="lider001",
            full_name="Lider Operacional",
            active=True,
        )
        self.agent_team = Agent.objects.create(
            user_lan_id="agent001",
            full_name="Agente Equipe",
            active=True,
        )
        self.agent_other = Agent.objects.create(
            user_lan_id="agent002",
            full_name="Agente Outro",
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.agent_team,
            leader=self.leader,
            team="Operacional Alpha",
            job_title="Analista",
            start_date=date(2026, 1, 1),
            active=True,
        )

        self.leader_user = User.objects.create_user(
            username="lider.user",
            email="lider@test.local",
            password="test1234",
        )
        UserProfile.objects.create(user=self.leader_user, agent=self.leader)
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_LIDER))
        self.leader_user.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_LIDER)))

        self.operational_user = User.objects.create_user(
            username="oper.user",
            email="oper@test.local",
            password="test1234",
        )
        UserProfile.objects.create(user=self.operational_user, agent=self.agent_team)
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        self.operational_user.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))

        self.plan_agent = Agent.objects.create(
            user_lan_id="plan001",
            full_name="Analista Planejamento",
            active=True,
        )
        AgentHistory.objects.create(
            agent=self.plan_agent,
            team="Planejamento",
            job_title="Analista de Planejamento",
            start_date=date(2026, 1, 1),
            active=True,
        )

        self.plan_user = User.objects.create_user(
            username="plan.user",
            email="plan@test.local",
            password="test1234",
        )
        UserProfile.objects.create(user=self.plan_user, agent=self.plan_agent)
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        self.plan_user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))

        self.no_profile_user = User.objects.create_user(
            username="noprofile",
            email="noprofile@test.local",
            password="test1234",
        )

        activity = JobActivity.objects.create(name="Conferência")
        location = Location.objects.create(city_name="Brasília", display_name="Brasília")

        Escala.objects.create(
            agent=self.agent_team,
            leader=self.leader,
            job_activity=activity,
            location=location,
            equipe="CONFER",
            horario="08:00 - 14:00",
            data=date(2026, 6, 1),
            dia_escala="08:00 - 14:00",
        )
        Escala.objects.create(
            agent=self.agent_other,
            leader=None,
            job_activity=activity,
            location=location,
            equipe="CONFER",
            horario="09:00 - 15:00",
            data=date(2026, 6, 1),
            dia_escala="FOLGA",
        )
        Escala.objects.create(
            agent=self.agent_team,
            leader=self.leader,
            job_activity=activity,
            location=location,
            equipe="CONFER",
            horario="08:00 - 14:00",
            data=date(2026, 6, 15),
            dia_escala="08:00 - 14:00",
        )

    def test_month_results_ordered_by_date_asc(self):
        self.client.force_authenticate(user=self.operational_user)
        response = self.client.get("/api/v1/escala-flex/escala/", {"month": "2026-06"})
        self.assertEqual(response.status_code, 200)
        dates = [row["data"] for row in response.data["results"]]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[0], "2026-06-01")

    def test_consulta_requires_profile(self):
        response = self.client.get("/api/v1/escala-flex/escala/")
        self.assertIn(response.status_code, (401, 403))

        self.client.force_authenticate(user=self.no_profile_user)
        response = self.client.get("/api/v1/escala-flex/escala/")
        self.assertEqual(response.status_code, 403)

    def test_op_agente_sees_only_own_escala(self):
        self.client.force_authenticate(user=self.operational_user)
        response = self.client.get("/api/v1/escala-flex/escala/", {"month": "2026-06"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertTrue(
            all(row["agent_lan_id"] == "agent001" for row in response.data["results"])
        )

    def test_op_agente_cannot_widen_with_search(self):
        self.client.force_authenticate(user=self.operational_user)
        response = self.client.get(
            "/api/v1/escala-flex/escala/",
            {"month": "2026-06", "search": "agent002"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertTrue(
            all(row["agent_lan_id"] == "agent001" for row in response.data["results"])
        )

    def test_leader_sees_team_escala(self):
        self.client.force_authenticate(user=self.leader_user)
        response = self.client.get("/api/v1/escala-flex/escala/", {"month": "2026-06"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["agent_lan_id"], "agent001")

    def test_plan_user_sees_all_escala(self):
        self.client.force_authenticate(user=self.plan_user)
        response = self.client.get("/api/v1/escala-flex/escala/", {"month": "2026-06"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)

    def test_filter_by_equipe_scoped(self):
        self.client.force_authenticate(user=self.operational_user)
        response = self.client.get(
            "/api/v1/escala-flex/escala/",
            {"month": "2026-06", "equipe": "CONFER"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)

    def test_filter_by_dia_escala(self):
        self.client.force_authenticate(user=self.operational_user)
        response = self.client.get(
            "/api/v1/escala-flex/escala/",
            {"month": "2026-06", "dia_escala": "08:00 - 14:00"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["agent_lan_id"], "agent001")

    def test_filter_by_dia_escala_absence_out_of_scope(self):
        self.client.force_authenticate(user=self.operational_user)
        response = self.client.get(
            "/api/v1/escala-flex/escala/",
            {"month": "2026-06", "dia_escala": "FOLGA"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_filter_options_scoped_to_own(self):
        self.client.force_authenticate(user=self.operational_user)
        response = self.client.get(
            "/api/v1/escala-flex/escala/filters/",
            {"month": "2026-06"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["dia_escalas"], ["08:00 - 14:00"])
        self.assertIn("columns", response.data)
        self.assertEqual(
            response.data["columns"]["dia_escala"],
            ["08:00 - 14:00"],
        )
        self.assertEqual(response.data["columns"]["agent_lan_id"], ["agent001"])

    def test_column_filter_uses_full_dataset_not_page(self):
        self.client.force_authenticate(user=self.plan_user)
        response = self.client.get(
            "/api/v1/escala-flex/escala/",
            {"month": "2026-06", "col_agent_lan_id": "agent002", "page_size": 1},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["agent_lan_id"], "agent002")
