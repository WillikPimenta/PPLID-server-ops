from datetime import date
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.access.constants import ROLE_OP_AGENTE, role_group_name
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class SeedPortalRbacTests(TestCase):
    def test_seed_creates_role_groups(self):
        out = StringIO()
        call_command("seed_portal_rbac", "--skip-assignments", stdout=out)
        self.assertTrue(
            __import__("django.contrib.auth.models", fromlist=["Group"])
            .Group.objects.filter(name=role_group_name(ROLE_OP_AGENTE))
            .exists()
        )

    def test_seed_assigns_inferred_roles_with_flag(self):
        user = User.objects.create_user("c90201a", password="test123")
        agent = Agent.objects.create(
            user_lan_id="c90201a",
            full_name="Agent Seed",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional/Fraud",
            job_title="Agente Backoffice I",
            start_date=date(2024, 1, 1),
            active=True,
        )

        call_command("seed_portal_rbac", "--infer-assignments", stdout=StringIO())

        group_names = set(user.groups.values_list("name", flat=True))
        self.assertIn(role_group_name(ROLE_OP_AGENTE), group_names)

    def test_seed_default_does_not_assign_roles(self):
        user = User.objects.create_user("c90203a", password="test123")
        agent = Agent.objects.create(
            user_lan_id="c90203a",
            full_name="Agent No Seed",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional/Fraud",
            job_title="Agente Backoffice I",
            start_date=date(2024, 1, 1),
            active=True,
        )

        call_command("seed_portal_rbac", stdout=StringIO())

        group_names = set(user.groups.values_list("name", flat=True))
        self.assertNotIn(role_group_name(ROLE_OP_AGENTE), group_names)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_seed_with_provision_users_flag(self):
        agent = Agent.objects.create(
            user_lan_id="c90202a",
            full_name="Provision Flag Agent",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional/Fraud",
            job_title="Agente Backoffice I",
            start_date=date(2024, 1, 1),
            active=True,
        )
        call_command("seed_portal_rbac", "--provision-users", "--infer-assignments", stdout=StringIO())
        self.assertTrue(User.objects.filter(username="c90202a").exists())
        user = User.objects.get(username="c90202a")
        self.assertIn(role_group_name(ROLE_OP_AGENTE), set(user.groups.values_list("name", flat=True)))

    def test_seed_dry_run(self):
        out = StringIO()
        call_command("seed_portal_rbac", "--dry-run", stdout=out)
        self.assertIn("dry-run", out.getvalue().lower())
