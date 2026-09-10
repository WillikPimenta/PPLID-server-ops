from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import ROLE_ADM_PORTAL, ROLE_OP_AGENTE, ROLE_OP_LIDER, role_group_name
from apps.accounts.models import UserChangeHistorico
from apps.accounts.services.portal_user_admin import (
    PortalUserAdminError,
    bulk_replicate_rbac,
    bulk_set_active,
    bulk_set_roles,
    create_external_user,
    deactivate_all_except_always_active,
    list_portal_users,
    reset_password,
    roles_for_user,
    set_user_roles,
)
from apps.access.resolve import ensure_role_groups_exist
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class PortalUserAdminServiceTests(TestCase):
    def setUp(self):
        ensure_role_groups_exist()
        self.admin = User.objects.create_user(
            "c93123a", password="adminpass", email="c93123a@test.local"
        )
        adm_group = Group.objects.get(name=role_group_name(ROLE_ADM_PORTAL))
        self.admin.groups.add(adm_group)
        self.admin.is_active = True
        self.admin.save()

    def test_create_external_user_inactive_by_default(self):
        data = create_external_user(
            actor=self.admin,
            username="externo01",
            first_name="Externo",
            last_name="User",
        )
        self.assertFalse(data["is_active"])
        self.assertFalse(data["has_agent"])

    def test_reset_password_sets_must_change(self):
        user = User.objects.create_user("c91001a", password="old", email="c91001a@test.local")
        reset_password(user, actor=self.admin)
        user.refresh_from_db()
        self.assertTrue(user.must_change_password)
        self.assertTrue(
            UserChangeHistorico.objects.filter(
                target_user=user,
                action=UserChangeHistorico.ACTION_PASSWORD_RESET,
            ).exists()
        )

    def test_set_user_roles_logs_history(self):
        user = User.objects.create_user("c91002a", password="x", email="c91002a@test.local")
        set_user_roles(user, [ROLE_OP_AGENTE], actor=self.admin)
        user.refresh_from_db()
        self.assertIn(role_group_name(ROLE_OP_AGENTE), user.groups.values_list("name", flat=True))
        entry = UserChangeHistorico.objects.filter(
            target_user=user,
            action=UserChangeHistorico.ACTION_ROLES,
        ).first()
        self.assertIsNotNone(entry)

    @override_settings(PORTAL_SEED_DEFAULT_PASSWORD="newpass123")
    def test_deactivate_all_except_always_active(self):
        keep = User.objects.create_user(
            "c91763a", password="x", is_active=True, email="c91763a@test.local"
        )
        other = User.objects.create_user(
            "c91003a", password="x", is_active=True, email="c91003a@test.local"
        )
        with self.settings():
            from unittest.mock import patch

            with patch(
                "apps.accounts.services.portal_user_admin.load_portal_users_config",
                return_value={"always_active": ["c91763a"]},
            ):
                report = deactivate_all_except_always_active(actor=self.admin)
        self.assertEqual(report["deactivated"], 2)  # c91003a + c93123a from setUp
        keep.refresh_from_db()
        other.refresh_from_db()
        self.assertTrue(keep.is_active)
        self.assertFalse(other.is_active)

    def test_bulk_set_active(self):
        user = User.objects.create_user(
            "c91004a", password="x", is_active=False, email="c91004a@test.local"
        )
        result = bulk_set_active(actor=self.admin, user_ids=[user.pk], is_active=True)
        user.refresh_from_db()
        self.assertEqual(result["updated"], 1)
        self.assertTrue(user.is_active)

    def test_list_portal_users_includes_job_title_from_current_history(self):
        agent = Agent.objects.create(full_name="Colaborador Teste", user_lan_id="c91006a")
        user = User.objects.create_user(
            "c91006a", password="x", is_active=True, email="c91006a@test.local"
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional",
            job_title="Analista Pl",
            start_date=date(2024, 1, 1),
            active=True,
        )

        users = list_portal_users()
        entry = next(item for item in users if item["username"] == "c91006a")
        self.assertEqual(entry["job_title"], "Analista Pl")
        self.assertTrue(entry["agent_active"])

    def test_list_portal_users_job_title_fallback_when_current_history_empty(self):
        agent = Agent.objects.create(full_name="Fallback Cargo", user_lan_id="c91008a")
        user = User.objects.create_user(
            "c91008a", password="x", is_active=True, email="c91008a@test.local"
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional",
            job_title="",
            start_date=date(2025, 1, 1),
            active=True,
        )
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional",
            job_title="Agente Backoffice I",
            start_date=date(2024, 1, 1),
            active=False,
            final_date=date(2024, 12, 31),
        )

        users = list_portal_users()
        entry = next(item for item in users if item["username"] == "c91008a")
        self.assertEqual(entry["job_title"], "Agente Backoffice I")

    def test_list_portal_users_job_title_from_sector_when_title_blank(self):
        agent = Agent.objects.create(full_name="Setor Cargo", user_lan_id="c91009a")
        user = User.objects.create_user(
            "c91009a", password="x", is_active=True, email="c91009a@test.local"
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            team="Operacional",
            job_title="",
            job_title_sector="Analista De Planejamento II",
            start_date=date(2025, 1, 1),
            active=True,
        )

        users = list_portal_users()
        entry = next(item for item in users if item["username"] == "c91009a")
        self.assertEqual(entry["job_title"], "Analista De Planejamento II")

    def test_list_portal_users_agent_active_false(self):
        agent = Agent.objects.create(
            full_name="Inativo HC",
            user_lan_id="c91007a",
            active=False,
        )
        user = User.objects.create_user(
            "c91007a", password="x", is_active=True, email="c91007a@test.local"
        )
        UserProfile.objects.create(user=user, agent=agent)

        users = list_portal_users()
        entry = next(item for item in users if item["username"] == "c91007a")
        self.assertFalse(entry["agent_active"])

    def test_list_portal_users_includes_leader_location_team(self):
        leader = Agent.objects.create(full_name="Lider Vigente", user_lan_id="ldr_vig")
        agent = Agent.objects.create(full_name="Colaborador HC", user_lan_id="c91010a")
        user = User.objects.create_user(
            "c91010a", password="x", is_active=True, email="c91010a@test.local"
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            leader=Agent.objects.create(full_name="Lider Antigo", user_lan_id="ldr_old"),
            location="Campinas",
            team="Time Antigo",
            job_title="Analista",
            start_date=date(2023, 1, 1),
            final_date=date(2023, 12, 31),
            active=False,
        )
        AgentHistory.objects.create(
            agent=agent,
            leader=leader,
            location="Curitiba",
            team="Time Alfa",
            job_title="Analista Pl",
            start_date=date(2024, 1, 1),
            active=True,
        )

        users = list_portal_users()
        entry = next(item for item in users if item["username"] == "c91010a")
        self.assertEqual(entry["leader_name"], "Lider Vigente")
        self.assertEqual(entry["leader_lan_id"], "ldr_vig")
        self.assertEqual(entry["location"], "Curitiba")
        self.assertEqual(entry["team"], "Time Alfa")

    def test_list_portal_users_external_or_without_leader_returns_null(self):
        external = create_external_user(
            actor=self.admin,
            username="externo02",
            first_name="Ex",
            last_name="Terno",
        )
        self.assertIsNone(external["leader_name"])
        self.assertIsNone(external["leader_lan_id"])
        self.assertIsNone(external["location"])
        self.assertIsNone(external["team"])

        agent = Agent.objects.create(full_name="Sem Lider", user_lan_id="c91011a")
        user = User.objects.create_user(
            "c91011a", password="x", is_active=True, email="c91011a@test.local"
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            leader=None,
            location="BH",
            team="Time Livre",
            start_date=date(2024, 1, 1),
            active=True,
        )
        users = list_portal_users()
        entry = next(item for item in users if item["username"] == "c91011a")
        self.assertIsNone(entry["leader_name"])
        self.assertIsNone(entry["leader_lan_id"])
        self.assertEqual(entry["location"], "BH")
        self.assertEqual(entry["team"], "Time Livre")

    def test_bulk_set_roles_replace_add_remove(self):
        user = User.objects.create_user(
            "c91012a", password="x", email="c91012a@test.local"
        )
        set_user_roles(user, [ROLE_OP_AGENTE], actor=self.admin)

        result = bulk_set_roles(
            actor=self.admin,
            user_ids=[user.pk],
            operation="add",
            roles=[ROLE_OP_LIDER],
        )
        self.assertEqual(result["updated"], 1)
        self.assertEqual(set(roles_for_user(user)), {ROLE_OP_AGENTE, ROLE_OP_LIDER})

        result = bulk_set_roles(
            actor=self.admin,
            user_ids=[user.pk],
            operation="remove",
            roles=[ROLE_OP_AGENTE],
        )
        self.assertEqual(result["updated"], 1)
        self.assertEqual(roles_for_user(user), [ROLE_OP_LIDER])

        result = bulk_set_roles(
            actor=self.admin,
            user_ids=[user.pk],
            operation="replace",
            roles=[ROLE_OP_AGENTE],
        )
        self.assertEqual(result["updated"], 1)
        self.assertEqual(roles_for_user(user), [ROLE_OP_AGENTE])

        noop = bulk_set_roles(
            actor=self.admin,
            user_ids=[user.pk],
            operation="replace",
            roles=[ROLE_OP_AGENTE],
        )
        self.assertEqual(noop["updated"], 0)
        self.assertEqual(noop["skipped"], 1)

    def test_bulk_set_roles_rejects_invalid_role(self):
        user = User.objects.create_user(
            "c91013a", password="x", email="c91013a@test.local"
        )
        with self.assertRaises(PortalUserAdminError):
            bulk_set_roles(
                actor=self.admin,
                user_ids=[user.pk],
                operation="add",
                roles=["role_inexistente"],
            )

    def test_bulk_set_roles_preserves_non_rbac_groups(self):
        user = User.objects.create_user(
            "c91014a", password="x", email="c91014a@test.local"
        )
        custom = Group.objects.create(name="custom-ops")
        user.groups.add(custom)
        set_user_roles(user, [ROLE_OP_AGENTE], actor=self.admin)

        bulk_set_roles(
            actor=self.admin,
            user_ids=[user.pk],
            operation="replace",
            roles=[ROLE_OP_LIDER],
        )
        names = set(user.groups.values_list("name", flat=True))
        self.assertIn("custom-ops", names)
        self.assertIn(role_group_name(ROLE_OP_LIDER), names)
        self.assertNotIn(role_group_name(ROLE_OP_AGENTE), names)

    def test_bulk_replicate_rbac_copies_source_roles(self):
        source = User.objects.create_user(
            "src_rbac", password="x", email="src_rbac@test.local"
        )
        target = User.objects.create_user(
            "tgt_rbac", password="x", email="tgt_rbac@test.local"
        )
        set_user_roles(source, [ROLE_OP_AGENTE, ROLE_OP_LIDER], actor=self.admin)
        set_user_roles(target, [ROLE_OP_AGENTE], actor=self.admin)

        result = bulk_replicate_rbac(
            actor=self.admin,
            source_user_id=source.pk,
            target_user_ids=[target.pk, source.pk],
        )
        self.assertEqual(result["updated"], 1)
        self.assertGreaterEqual(result["skipped"], 1)
        self.assertEqual(set(roles_for_user(source)), {ROLE_OP_AGENTE, ROLE_OP_LIDER})
        self.assertEqual(set(roles_for_user(target)), {ROLE_OP_AGENTE, ROLE_OP_LIDER})
        self.assertTrue(
            UserChangeHistorico.objects.filter(
                target_user=target,
                action=UserChangeHistorico.ACTION_ROLES,
            ).exists()
        )

    def test_bulk_roles_protects_last_admin(self):
        only_admin = self.admin
        other = User.objects.create_user(
            "c91015a", password="x", email="c91015a@test.local"
        )
        set_user_roles(other, [ROLE_OP_AGENTE], actor=self.admin)

        result = bulk_set_roles(
            actor=only_admin,
            user_ids=[only_admin.pk],
            operation="remove",
            roles=[ROLE_ADM_PORTAL],
        )
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertTrue(result["errors"])
        self.assertIn(ROLE_ADM_PORTAL, roles_for_user(only_admin))


@override_settings(ACCESS_ENFORCEMENT=True)
class PortalUserApiTests(TestCase):
    def setUp(self):
        ensure_role_groups_exist()
        self.client = APIClient()
        self.admin = User.objects.create_user(
            "c93123a", password="adminpass", email="c93123a-api@test.local"
        )
        adm_group = Group.objects.get(name=role_group_name(ROLE_ADM_PORTAL))
        self.admin.groups.add(adm_group)

        self.other = User.objects.create_user(
            "c91005a", password="x", is_active=False, email="c91005a@test.local"
        )

    def test_list_requires_permission(self):
        user = User.objects.create_user(
            "noperm", password="x", email="noperm@test.local"
        )
        self.client.force_authenticate(user)
        response = self.client.get("/api/v1/portal-users/")
        self.assertEqual(response.status_code, 403)

    def test_admin_can_list_and_activate(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/portal-users/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(item["username"] == "c91005a" for item in response.json()))

        response = self.client.post(
            "/api/v1/portal-users/bulk-active/",
            {"user_ids": [str(self.other.pk)], "is_active": True},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.other.refresh_from_db()
        self.assertTrue(self.other.is_active)

    def test_create_external_user_via_api(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/portal-users/",
            {
                "username": "vendor01",
                "first_name": "Vendor",
                "last_name": "Externo",
                "roles": [ROLE_OP_AGENTE],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()["is_active"])

    def test_historico_endpoint(self):
        self.client.force_authenticate(self.admin)
        self.client.post(
            "/api/v1/portal-users/bulk-active/",
            {"user_ids": [str(self.other.pk)], "is_active": True},
            format="json",
        )
        response = self.client.get(f"/api/v1/portal-users/{self.other.pk}/historico/")
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.json()), 1)

    def test_bulk_roles_and_replicate_require_permission(self):
        user = User.objects.create_user(
            "noperm2", password="x", email="noperm2@test.local"
        )
        self.client.force_authenticate(user)
        for path, payload in (
            (
                "/api/v1/portal-users/bulk-roles/",
                {
                    "user_ids": [str(self.other.pk)],
                    "operation": "add",
                    "roles": [ROLE_OP_AGENTE],
                },
            ),
            (
                "/api/v1/portal-users/bulk-replicate-rbac/",
                {
                    "source_user_id": str(self.admin.pk),
                    "target_user_ids": [str(self.other.pk)],
                },
            ),
        ):
            response = self.client.post(path, payload, format="json")
            self.assertEqual(response.status_code, 403)

    def test_bulk_roles_and_replicate_via_api(self):
        self.client.force_authenticate(self.admin)
        target = User.objects.create_user(
            "api_tgt", password="x", email="api_tgt@test.local"
        )
        source = User.objects.create_user(
            "api_src", password="x", email="api_src@test.local"
        )
        set_user_roles(source, [ROLE_OP_LIDER], actor=self.admin)

        response = self.client.post(
            "/api/v1/portal-users/bulk-roles/",
            {
                "user_ids": [str(target.pk)],
                "operation": "replace",
                "roles": [ROLE_OP_AGENTE],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)
        self.assertEqual(roles_for_user(target), [ROLE_OP_AGENTE])

        response = self.client.post(
            "/api/v1/portal-users/bulk-replicate-rbac/",
            {
                "source_user_id": str(source.pk),
                "target_user_ids": [str(target.pk)],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["updated"], 1)
        self.assertEqual(roles_for_user(target), [ROLE_OP_LIDER])

    def test_bulk_roles_missing_user_reported(self):
        self.client.force_authenticate(self.admin)
        missing = "00000000-0000-0000-0000-000000000099"
        response = self.client.post(
            "/api/v1/portal-users/bulk-roles/",
            {
                "user_ids": [missing],
                "operation": "add",
                "roles": [ROLE_OP_AGENTE],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["updated"], 0)
        self.assertEqual(body["skipped"], 1)
        self.assertTrue(any("inexistente" in err for err in body["errors"]))
