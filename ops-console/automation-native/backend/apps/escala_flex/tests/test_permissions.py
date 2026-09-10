from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from apps.access.constants import ROLE_OP_AGENTE, role_group_name
from apps.escala_flex.services.permissions import (
    build_operational_profile,
    resolve_menu_keys,
)
from apps.workforce.models import Agent, AgentHistory, UserProfile
from apps.accounts.models import User


@override_settings(ESCALA_FLEX_OPEN_ACCESS=False)
class PermissionsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="agent001",
            password="test",
        )
        self.agent = Agent.objects.create(
            user_lan_id="agent001",
            full_name="Agente Teste",
            active=True,
        )
        UserProfile.objects.create(user=self.user, agent=self.agent)
        AgentHistory.objects.create(
            agent=self.agent,
            team="Operacional BrFlow",
            job_title="Agente Backoffice I",
            start_date=date(2026, 1, 1),
            active=True,
        )
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        self.user.groups.add(group)

    def test_agent_backoffice_menu(self):
        profile = build_operational_profile(self.user)
        self.assertTrue(profile.is_agent_backoffice)
        self.assertIn("agente", profile.menu_keys)
        self.assertIn("escala", profile.menu_keys)
        self.assertIn("historico", profile.menu_keys)
        self.assertNotIn("painel", profile.menu_keys)
        self.assertNotIn("equipe", profile.menu_keys)

    def test_operational_menu(self):
        keys = resolve_menu_keys("Operacional BrFlow", False, False)
        self.assertEqual(keys, sorted({"painel", "escala", "historico"}))

    def test_planning_menu(self):
        keys = resolve_menu_keys("Planejamento", False, False)
        self.assertIn("nh", keys)
        self.assertIn("dashboards", keys)

    def test_superuser_without_agent_gets_admin_profile(self):
        superuser = User.objects.create_superuser(
            username="super.dev",
            email="super.dev@test.local",
            password="test",
        )
        profile = build_operational_profile(superuser)
        self.assertIsNotNone(profile)
        self.assertTrue(profile.is_admin)
        self.assertEqual(profile.team, "Planejamento")
        self.assertIn("painel", profile.menu_keys)
        self.assertIn("dashboards", profile.menu_keys)

    def test_staff_without_agent_has_no_admin_profile(self):
        staff = User.objects.create_user(
            username="staff.dev",
            email="staff.dev@test.local",
            password="test",
            is_staff=True,
        )
        profile = build_operational_profile(staff)
        self.assertIsNone(profile)
