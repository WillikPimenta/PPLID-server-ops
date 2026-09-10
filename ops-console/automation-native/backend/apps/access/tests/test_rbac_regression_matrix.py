"""Matriz mínima de regressão RBAC por perfil (Fase 6)."""

from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_ADM_PORTAL,
    ROLE_OP_AGENTE,
    ROLE_PLAN_ANALISTA,
    ROLE_PROC_USUARIO,
    role_group_name,
)
from apps.workforce.models import Agent, AgentHistory

User = get_user_model()


def _user_with_role(username: str, role: str, *, team: str, job_title: str) -> User:
    user = User.objects.create_user(username, email=f"{username}@test.local", password="x")
    agent = Agent.objects.create(
        user_lan_id=username,
        full_name=username,
        active=True,
        hire_date=date(2024, 1, 1),
    )
    AgentHistory.objects.create(
        agent=agent,
        team=team,
        job_title=job_title,
        start_date=date(2024, 1, 1),
        active=True,
    )
    Group.objects.get_or_create(name=role_group_name(role))
    user.groups.add(Group.objects.get(name=role_group_name(role)))
    return user


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class RbacRegressionMatrixTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.op_agente = _user_with_role(
            "c95001a", ROLE_OP_AGENTE, team="Operacional/Alpha", job_title="Agente Backoffice I"
        )
        self.plan_analista = _user_with_role(
            "c95002a", ROLE_PLAN_ANALISTA, team="Planejamento", job_title="Analista de Planejamento"
        )
        self.adm = _user_with_role(
            "c95003a", ROLE_ADM_PORTAL, team="Planejamento", job_title="Gerente"
        )
        self.proc = _user_with_role(
            "c95004a", ROLE_PROC_USUARIO, team="Processos", job_title="Analista de Processos"
        )

    def test_op_agente_denied_headcount(self):
        self.client.force_authenticate(user=self.op_agente)
        response = self.client.get("/api/v1/agents/")
        self.assertEqual(response.status_code, 403)

    def test_plan_analista_can_headcount(self):
        self.client.force_authenticate(user=self.plan_analista)
        response = self.client.get("/api/v1/agents/")
        self.assertEqual(response.status_code, 200)

    def test_plan_analista_can_megazord_meta(self):
        self.client.force_authenticate(user=self.plan_analista)
        response = self.client.get("/api/v1/dimensoes-processos/meta/")
        self.assertEqual(response.status_code, 200)

    def test_op_agente_denied_megazord_meta(self):
        self.client.force_authenticate(user=self.op_agente)
        response = self.client.get("/api/v1/dimensoes-processos/meta/")
        self.assertEqual(response.status_code, 403)

    def test_proc_user_denied_megazord_meta(self):
        self.client.force_authenticate(user=self.proc)
        response = self.client.get("/api/v1/dimensoes-processos/meta/")
        self.assertEqual(response.status_code, 403)

    def test_proc_user_denied_headcount(self):
        self.client.force_authenticate(user=self.proc)
        response = self.client.get("/api/v1/agents/")
        self.assertEqual(response.status_code, 403)

    def test_adm_can_headcount_catalog_meta(self):
        self.client.force_authenticate(user=self.adm)
        response = self.client.get("/api/v1/workforce/headcount-catalog/meta/")
        self.assertEqual(response.status_code, 200)

    def test_op_denied_headcount_catalog_meta(self):
        self.client.force_authenticate(user=self.op_agente)
        response = self.client.get("/api/v1/workforce/headcount-catalog/meta/")
        self.assertEqual(response.status_code, 403)

    def test_op_agente_can_view_case_manager_status(self):
        self.client.force_authenticate(user=self.op_agente)
        response = self.client.get("/api/v1/case-manager/status/")
        self.assertEqual(response.status_code, 200)

    def test_proc_user_denied_case_manager_status(self):
        self.client.force_authenticate(user=self.proc)
        response = self.client.get("/api/v1/case-manager/status/")
        self.assertEqual(response.status_code, 403)

    def test_plan_analista_denied_case_manager_sync(self):
        self.client.force_authenticate(user=self.plan_analista)
        response = self.client.post("/api/v1/case-manager/sync/", {}, format="json")
        self.assertEqual(response.status_code, 403)

    def test_plan_analista_can_generation_config(self):
        self.client.force_authenticate(user=self.plan_analista)
        response = self.client.get(
            "/api/v1/escala-flex/planejamento/generation/config-options/"
        )
        self.assertEqual(response.status_code, 200)

    def test_op_agente_denied_generation_config(self):
        self.client.force_authenticate(user=self.op_agente)
        response = self.client.get(
            "/api/v1/escala-flex/planejamento/generation/config-options/"
        )
        self.assertEqual(response.status_code, 403)
