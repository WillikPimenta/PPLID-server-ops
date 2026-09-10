from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_ADM_PORTAL, ROLE_QUAL_USUARIO, role_group_name
from apps.access.registry import ADM_ESCALA_IMPERSONATE, QUAL_AUDITORIA_VIEW
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


@override_settings(ACCESS_ENFORCEMENT=True)
class PortalImpersonationTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            username="admin_imp",
            email="admin_imp@test.local",
            password="test12345",
        )
        admin_agent = Agent.objects.create(
            user_lan_id="admin_imp",
            full_name="Admin Impersonation",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        UserProfile.objects.create(user=self.admin, agent=admin_agent)
        self.admin.groups.add(Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))[0])

        self.target = User.objects.create_user(
            username="agent_imp",
            email="agent_imp@test.local",
            password="test12345",
            first_name="Agente",
            last_name="Qualidade",
        )
        target_agent = Agent.objects.create(
            user_lan_id="agent_imp",
            full_name="Agente Qualidade",
            active=True,
            hire_date=date(2021, 1, 1),
        )
        UserProfile.objects.create(user=self.target, agent=target_agent)
        AgentHistory.objects.create(
            agent=target_agent,
            team="Qualidade",
            job_title="Analista de Qualidade",
            start_date=date(2024, 1, 1),
            active=True,
        )
        self.target.groups.add(Group.objects.get_or_create(name=role_group_name(ROLE_QUAL_USUARIO))[0])

        self.client.force_authenticate(user=self.admin)

    def test_targets_and_impersonation_switches_rbac(self):
        targets = self.client.get("/api/v1/access/impersonate/targets/")
        self.assertEqual(targets.status_code, 200)
        values = {item["value"] for item in targets.data["results"]}
        self.assertIn("agent_imp", values)

        start = self.client.post(
            "/api/v1/access/impersonate/",
            {"user_lan_id": "agent_imp"},
            format="json",
        )
        self.assertEqual(start.status_code, 200, start.data)
        self.assertTrue(start.data["impersonating"])
        self.assertEqual(start.data["user"]["username"], "agent_imp")
        self.assertEqual(start.data["real_user"]["username"], "admin_imp")
        self.assertIn(QUAL_AUDITORIA_VIEW, start.data["access"]["permissions"])
        self.assertIn(ADM_ESCALA_IMPERSONATE, start.data["access"]["permissions"])

        me = self.client.get("/api/v1/auth/me/")
        self.assertEqual(me.status_code, 200)
        self.assertTrue(me.data["impersonating"])
        self.assertEqual(me.data["user"]["username"], "agent_imp")
        self.assertIn(QUAL_AUDITORIA_VIEW, me.data["access"]["permissions"])

        clear = self.client.post("/api/v1/access/impersonate/clear/")
        self.assertEqual(clear.status_code, 200)
        self.assertFalse(clear.data["impersonating"])
        self.assertEqual(clear.data["user"]["username"], "admin_imp")

    def test_preview_includes_headcount_and_rbac(self):
        preview = self.client.get(
            "/api/v1/access/impersonate/preview/",
            {"user_lan_id": "agent_imp"},
        )
        self.assertEqual(preview.status_code, 200, preview.data)
        self.assertEqual(preview.data["headcount"]["full_name"], "Agente Qualidade")
        self.assertEqual(preview.data["headcount"]["job_title"], "Analista de Qualidade")
        self.assertIn("roles", preview.data["access"])
        self.assertIn("permissions", preview.data["access"])
        self.assertIn(QUAL_AUDITORIA_VIEW, preview.data["access"]["permissions"])
