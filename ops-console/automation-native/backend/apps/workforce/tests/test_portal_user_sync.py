from datetime import date
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from apps.access.resolve import load_portal_users_config
from apps.access.constants import ROLE_OP_AGENTE, role_group_name
from apps.workforce.models import Agent, AgentHistory, UserProfile
from apps.workforce.services.portal_user_sync import provision_portal_users, split_full_name

User = get_user_model()


class PortalUserSyncTests(TestCase):
    def _agent(self, lan: str, *, active=True, email="", name="Test User") -> Agent:
        return Agent.objects.create(
            user_lan_id=lan,
            full_name=name,
            email=email,
            active=active,
            hire_date=date(2020, 1, 1),
        )

    def test_split_full_name(self):
        self.assertEqual(split_full_name("Ana Silva Costa"), ("Ana", "Silva Costa"))
        self.assertEqual(split_full_name("Ana"), ("Ana", ""))

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_active_agent_creates_inactive_user_by_default(self):
        self._agent("c91001a", active=True)
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": [],
                "always_active": [],
                "preserve_usernames": [],
                "deactivate_orphans": False,
            },
        ):
            report = provision_portal_users()
        self.assertEqual(report["created"], 1)
        user = User.objects.get(username="c91001a")
        self.assertFalse(user.is_active)
        self.assertTrue(UserProfile.objects.filter(user=user).exists())
        self.assertTrue(user.check_password("test12345"))
        self.assertTrue(user.must_change_password)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_always_active_creates_active_user(self):
        self._agent("c93123a", active=True)
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": [],
                "always_active": ["c93123a"],
                "preserve_usernames": [],
                "deactivate_orphans": False,
            },
        ):
            provision_portal_users()
        user = User.objects.get(username="c93123a")
        self.assertTrue(user.is_active)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_resync_does_not_reactivate_manually_deactivated_user(self):
        self._agent("c91010a", active=True)
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": [],
                "always_active": [],
                "preserve_usernames": [],
                "deactivate_orphans": False,
            },
        ):
            provision_portal_users()
        user = User.objects.get(username="c91010a")
        user.is_active = True
        user.save(update_fields=["is_active"])
        user.is_active = False
        user.save(update_fields=["is_active"])
        provision_portal_users()
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_inactive_agent_skipped_without_always_provision(self):
        self._agent("c91002a", active=False)
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": [],
                "always_active": [],
                "preserve_usernames": [],
                "deactivate_orphans": False,
            },
        ):
            report = provision_portal_users()
        self.assertEqual(report["created"], 0)
        self.assertFalse(User.objects.filter(username="c91002a").exists())

    def test_inactive_agent_provisioned_when_in_always_provision(self):
        self._agent("c91003a", active=False)
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": ["c91003a"],
                "always_active": [],
                "preserve_usernames": [],
                "deactivate_orphans": False,
            },
        ):
            report = provision_portal_users()
        self.assertEqual(report["created"], 1)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_idempotent_second_run(self):
        self._agent("c91004a")
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": [],
                "always_active": [],
                "preserve_usernames": [],
                "deactivate_orphans": False,
            },
        ):
            provision_portal_users()
            report = provision_portal_users()
        self.assertEqual(report["created"], 0)
        self.assertEqual(report["updated"], 1)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_duplicate_email_sets_null_and_reports_conflict(self):
        self._agent("c91005a", email="dup@test.local")
        self._agent("c91006a", email="dup@test.local")
        report = provision_portal_users()
        self.assertEqual(report["created"], 2)
        self.assertIn("c91006a", report["email_conflicts"])
        second = User.objects.get(username="c91006a")
        self.assertIsNone(second.email)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="oldpass")
    def test_preserve_username_skips_password_reset(self):
        user = User.objects.create_user("gerente.dev", password="keepme")
        self._agent("gerente.dev")
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": [],
                "always_active": [],
                "preserve_usernames": ["gerente.dev"],
                "deactivate_orphans": False,
            },
        ):
            provision_portal_users(reset_passwords=True)
        user.refresh_from_db()
        self.assertTrue(user.check_password("keepme"))

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_reassigns_agent_profile_after_snapshot_style_conflict(self):
        agent = self._agent("c91008a")
        stale_user = User.objects.create_user("oldusername", password="x")
        UserProfile.objects.create(user=stale_user, agent=agent)

        report = provision_portal_users()

        user = User.objects.get(username="c91008a")
        profile = UserProfile.objects.get(agent=agent)
        self.assertEqual(profile.user_id, user.id)
        self.assertFalse(UserProfile.objects.filter(user=stale_user).exists())
        self.assertGreaterEqual(report["profiles_linked"], 1)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="test12345")
    def test_integration_provision_then_rbac(self):
        agent = self._agent("c91007a")
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional/Fraud",
            job_title="Agente Backoffice I",
            start_date=date(2024, 1, 1),
            active=True,
        )
        with patch(
            "apps.workforce.services.portal_user_sync.load_portal_users_config",
            return_value={
                "active_only": True,
                "always_provision": [],
                "always_active": [],
                "preserve_usernames": [],
                "deactivate_orphans": False,
            },
        ):
            provision_portal_users()
        user = User.objects.get(username="c91007a")
        user.is_active = True
        user.save(update_fields=["is_active"])
        call_command("seed_portal_rbac", "--infer-assignments", stdout=StringIO())
        user.refresh_from_db()
        group_names = set(user.groups.values_list("name", flat=True))
        self.assertIn(role_group_name(ROLE_OP_AGENTE), group_names)

    def test_load_portal_users_config_defaults(self):
        with patch(
            "apps.access.resolve._load_yaml",
            return_value={},
        ):
            config = load_portal_users_config()
        self.assertTrue(config["active_only"])
        self.assertEqual(config["always_active"], [])
        self.assertFalse(config["deactivate_orphans"])
