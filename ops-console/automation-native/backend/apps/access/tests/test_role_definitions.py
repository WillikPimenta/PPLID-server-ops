"""Testes de definições RBAC por perfil."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from io import StringIO
from rest_framework.test import APIClient

from apps.access.constants import (
    ALL_ROLES,
    ROLE_ADM_PORTAL,
    ROLE_OP_AGENTE,
    ROLE_PROC_USUARIO,
    role_group_name,
)
from apps.access.models import PortalRoleDefinition, PortalRoutePolicy
from apps.access.registry import (
    OPERACAO_TROCAS_VIEW,
    PLANEJAMENTO_TROCAS_VIEW,
    PORTAL_SECAO_VIEW,
    PROCESSOS_SUPORTE_CLARO_VIEW,
    QUAL_FALHAS_VIEW,
)
from apps.access.roles import permissions_for_role

User = get_user_model()


class SeedPortalRoleDefinitionsTests(TestCase):
    def test_seed_is_idempotent(self):
        from django.core.management import call_command

        call_command("seed_portal_role_definitions", stdout=StringIO())
        count_after_first = PortalRoleDefinition.objects.count()
        call_command("seed_portal_role_definitions", stdout=StringIO())
        self.assertEqual(PortalRoleDefinition.objects.count(), count_after_first)
        self.assertEqual(count_after_first, len(ALL_ROLES))

    def test_seed_creates_op_agente_defaults(self):
        from django.core.management import call_command

        call_command("seed_portal_role_definitions", stdout=StringIO())
        row = PortalRoleDefinition.objects.get(role=ROLE_OP_AGENTE)
        self.assertIn(QUAL_FALHAS_VIEW, row.permissions)
        self.assertTrue(row.is_editable)

    def test_adm_portal_not_editable(self):
        from django.core.management import call_command

        call_command("seed_portal_role_definitions", stdout=StringIO())
        row = PortalRoleDefinition.objects.get(role=ROLE_ADM_PORTAL)
        self.assertFalse(row.is_editable)

    def test_adm_portal_always_gets_full_catalog_even_if_db_stale(self):
        from apps.access.registry import ALL_PERMISSIONS, PLANEJAMENTO_MEGAZORD_VIEW

        PortalRoleDefinition.objects.create(
            role=ROLE_ADM_PORTAL,
            label="Admin",
            permissions=[QUAL_FALHAS_VIEW],
            default_scope="global",
            is_editable=False,
        )
        perms = permissions_for_role(ROLE_ADM_PORTAL)
        self.assertEqual(perms, set(ALL_PERMISSIONS))
        self.assertIn(PLANEJAMENTO_MEGAZORD_VIEW, perms)


class RoleDefinitionRuntimeTests(TestCase):
    def test_db_overrides_code_defaults(self):
        from django.core.management import call_command

        call_command("seed_portal_role_definitions", stdout=StringIO())
        row = PortalRoleDefinition.objects.get(role=ROLE_PROC_USUARIO)
        row.permissions = [PROCESSOS_SUPORTE_CLARO_VIEW, QUAL_FALHAS_VIEW]
        row.save(update_fields=["permissions"])

        perms = permissions_for_role(ROLE_PROC_USUARIO)
        self.assertIn(QUAL_FALHAS_VIEW, perms)
        self.assertIn(PROCESSOS_SUPORTE_CLARO_VIEW, perms)


class PortalRoleDefinitionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            "admin_roles", password="test123", email="admin_roles@test.local"
        )
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.admin.groups.add(group)
        self.user = User.objects.create_user(
            "plain_roles", password="test123", email="plain_roles@test.local"
        )

        from django.core.management import call_command

        call_command("seed_portal_role_definitions", stdout=StringIO())
        call_command("seed_portal_route_policies", stdout=StringIO())

    def test_list_role_definitions(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/rbac/role-definitions/")
        self.assertEqual(response.status_code, 200)
        roles = {item["role"] for item in response.json()}
        self.assertIn(ROLE_OP_AGENTE, roles)
        self.assertIn(ROLE_PROC_USUARIO, roles)

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_patch_requires_config_hub(self):
        self.client.force_authenticate(self.user)
        response = self.client.patch(
            f"/api/v1/rbac/role-definitions/{ROLE_OP_AGENTE}/",
            {"permissions": [QUAL_FALHAS_VIEW]},
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_patch_role_definition(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/rbac/role-definitions/{ROLE_OP_AGENTE}/",
            {"permissions": [QUAL_FALHAS_VIEW], "default_scope": "team"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["default_scope"], "team")
        self.assertTrue(payload["is_customized"])
        self.assertEqual(permissions_for_role(ROLE_OP_AGENTE), {QUAL_FALHAS_VIEW})

    def test_patch_rejects_unknown_permission(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/rbac/role-definitions/{ROLE_OP_AGENTE}/",
            {"permissions": ["invalid.permission"]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_admin_can_create_and_delete_custom_role(self):
        self.client.force_authenticate(self.admin)
        create = self.client.post(
            "/api/v1/rbac/role-definitions/",
            {
                "role": "qual_equipe_teste",
                "label": "Qual. Equipe Teste",
                "area": "qualidade",
                "default_scope": "team",
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        payload = create.json()
        self.assertEqual(payload["role"], "qual_equipe_teste")
        self.assertTrue(payload["can_delete"])
        self.assertTrue(Group.objects.filter(name=role_group_name("qual_equipe_teste")).exists())

        patch = self.client.patch(
            "/api/v1/rbac/role-definitions/qual_equipe_teste/",
            {"label": "Qual. Equipe Teste 2", "permissions": [QUAL_FALHAS_VIEW]},
            format="json",
        )
        self.assertEqual(patch.status_code, 200)
        self.assertEqual(patch.json()["label"], "Qual. Equipe Teste 2")

        delete = self.client.delete("/api/v1/rbac/role-definitions/qual_equipe_teste/")
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(PortalRoleDefinition.objects.filter(role="qual_equipe_teste").exists())

    def test_admin_can_rename_custom_role(self):
        self.client.force_authenticate(self.admin)
        create = self.client.post(
            "/api/v1/rbac/role-definitions/",
            {
                "role": "qual_slug_antigo",
                "label": "Qual. Slug",
                "area": "qualidade",
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        user = User.objects.create_user("slug_user", password="test123")
        user.groups.add(Group.objects.get(name=role_group_name("qual_slug_antigo")))

        rename = self.client.patch(
            "/api/v1/rbac/role-definitions/qual_slug_antigo/",
            {"role": "qual_slug_novo", "label": "Qual. Slug Novo"},
            format="json",
        )
        self.assertEqual(rename.status_code, 200)
        self.assertEqual(rename.json()["role"], "qual_slug_novo")
        self.assertEqual(rename.json()["label"], "Qual. Slug Novo")
        self.assertFalse(PortalRoleDefinition.objects.filter(role="qual_slug_antigo").exists())
        self.assertTrue(PortalRoleDefinition.objects.filter(role="qual_slug_novo").exists())
        user.refresh_from_db()
        self.assertTrue(user.groups.filter(name=role_group_name("qual_slug_novo")).exists())
        self.assertFalse(user.groups.filter(name=role_group_name("qual_slug_antigo")).exists())

    def test_builtin_role_cannot_be_renamed(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/rbac/role-definitions/{ROLE_OP_AGENTE}/",
            {"role": "op_agente_renomeado"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_builtin_role_cannot_be_deleted(self):
        self.client.force_authenticate(self.admin)
        response = self.client.delete(f"/api/v1/rbac/role-definitions/{ROLE_OP_AGENTE}/")
        self.assertEqual(response.status_code, 400)

    def test_adm_portal_not_editable_via_api(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            f"/api/v1/rbac/role-definitions/{ROLE_ADM_PORTAL}/",
            {"permissions": [QUAL_FALHAS_VIEW]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_routes_with_access_preview(self):
        self.client.force_authenticate(self.admin)
        PortalRoutePolicy.objects.filter(route_name="falhas-criticas").update(
            permissions_any=[QUAL_FALHAS_VIEW]
        )
        response = self.client.get(f"/api/v1/rbac/role-definitions/{ROLE_OP_AGENTE}/")
        self.assertEqual(response.status_code, 200)
        routes = {item["route_name"] for item in response.json()["routes_with_access"]}
        self.assertIn("falhas-criticas", routes)

    def test_catalog_returns_permissions(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/rbac/role-definitions/catalog/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("permissions", payload)
        self.assertIn("routes", payload)
        route_names = {item["route_name"] for item in payload["routes"]}
        self.assertIn("operacao-trocas", route_names)
        self.assertIn("home", route_names)
        home = next(item for item in payload["routes"] if item["route_name"] == "home")
        self.assertTrue(home.get("is_system"))

    def test_proc_usuario_excludes_trocas_after_fix_open(self):
        from django.core.management import call_command
        from apps.access.services.role_definitions import serialize_role_definition

        call_command("seed_portal_route_policies", "--fix-open", stdout=StringIO())
        row = PortalRoleDefinition.objects.get(role=ROLE_PROC_USUARIO)
        routes = {item["route_name"] for item in serialize_role_definition(row)["routes_with_access"]}
        self.assertNotIn("operacao-trocas", routes)
        self.assertNotIn("planejamento-trocas", routes)
        self.assertIn("processos-suporte-claro", routes)

    def test_portal_secao_view_grants_section_route(self):
        from django.core.management import call_command
        from apps.access.services.role_definitions import serialize_role_definition

        call_command("seed_portal_route_policies", "--fix-open", stdout=StringIO())
        row = PortalRoleDefinition.objects.get(role=ROLE_OP_AGENTE)
        row.permissions = [PORTAL_SECAO_VIEW]
        row.save(update_fields=["permissions"])
        routes = {item["route_name"] for item in serialize_role_definition(row)["routes_with_access"]}
        self.assertIn("section", routes)
