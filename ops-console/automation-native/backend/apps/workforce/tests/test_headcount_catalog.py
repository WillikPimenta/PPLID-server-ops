from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import (
    ROLE_ADM_PORTAL,
    ROLE_OP_AGENTE,
    ROLE_PLAN_ANALISTA,
    role_group_name,
)
from apps.workforce.models import Agent, AgentHistory, HeadcountCatalogItem
from apps.workforce.services.catalog import assert_cycle_catalog_values
from apps.workforce.services.cycle_change import CycleChangeError

User = get_user_model()


class HeadcountCatalogApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.agent = Agent.objects.create(
            user_lan_id="c96001a",
            full_name="Catalog Agent",
            active=True,
            hire_date=date(2024, 1, 1),
        )
        AgentHistory.objects.create(
            agent=self.agent,
            team="Operacional/Compliance",
            job_title="Agente Backoffice I",
            team_sector="Compliance",
            start_date=date(2024, 1, 1),
            active=True,
        )

        self.plan_user = User.objects.create_user(
            "plan.cat", email="plan.cat@test.local", password="x"
        )
        self.op_user = User.objects.create_user(
            "op.cat", email="op.cat@test.local", password="x"
        )
        self.adm_user = User.objects.create_user(
            "adm.cat", email="adm.cat@test.local", password="x"
        )
        for role, user in (
            (ROLE_PLAN_ANALISTA, self.plan_user),
            (ROLE_OP_AGENTE, self.op_user),
            (ROLE_ADM_PORTAL, self.adm_user),
        ):
            Group.objects.get_or_create(name=role_group_name(role))
            user.groups.add(Group.objects.get(name=role_group_name(role)))

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_plan_can_read_options(self):
        self.client.force_authenticate(user=self.plan_user)
        response = self.client.get("/api/v1/workforce/headcount-catalog/options/")
        self.assertEqual(response.status_code, 200)
        options = response.json()["options"]
        self.assertIn("team", options)
        values = [o["value"] for o in options["team"]]
        self.assertIn("Operacional/Compliance", values)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_op_denied_options(self):
        self.client.force_authenticate(user=self.op_user)
        response = self.client.get("/api/v1/workforce/headcount-catalog/options/")
        self.assertEqual(response.status_code, 403)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_adm_can_create_catalog_item(self):
        self.client.force_authenticate(user=self.adm_user)
        response = self.client.post(
            "/api/v1/workforce/headcount-catalog/setor-time/",
            data={"value": "Novo Setor", "active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(
            HeadcountCatalogItem.objects.filter(
                catalog="team_sector", value="Novo Setor"
            ).exists()
        )

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_plan_denied_catalog_write(self):
        self.client.force_authenticate(user=self.plan_user)
        response = self.client.post(
            "/api/v1/workforce/headcount-catalog/setor-time/",
            data={"value": "Bloqueado"},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_assert_rejects_unknown_when_catalog_configured(self):
        HeadcountCatalogItem.objects.create(
            catalog="team_sector",
            value="Compliance",
            label="Compliance",
            active=True,
        )
        with self.assertRaises(CycleChangeError):
            assert_cycle_catalog_values({"team_sector": "Valor Inventado"})

    def test_assert_allows_known_and_empty_catalog(self):
        assert_cycle_catalog_values({"team_sector": "Qualquer"})
        HeadcountCatalogItem.objects.create(
            catalog="team_sector",
            value="Compliance",
            label="Compliance",
            active=True,
        )
        assert_cycle_catalog_values({"team_sector": "Compliance"})
        assert_cycle_catalog_values({"team_sector": ""})
