"""Testes de políticas RBAC por rota."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from io import StringIO
from rest_framework.test import APIClient

from apps.access.constants import ROLE_ADM_PORTAL, role_group_name
from apps.access.models import PortalMenuOption, PortalRoutePolicy
from apps.access.portal_route_registry import PORTAL_ROUTE_BY_NAME
from apps.access.registry import OPERACAO_TROCAS_VIEW, QUAL_FALHAS_VIEW
from apps.access.services.route_policies import roles_with_access_for_policy

User = get_user_model()


class SeedPortalRoutePoliciesTests(TestCase):
    def test_seed_is_idempotent(self):
        from django.core.management import call_command

        call_command("seed_portal_route_policies", stdout=StringIO())
        count_after_first = PortalRoutePolicy.objects.count()
        call_command("seed_portal_route_policies", stdout=StringIO())
        self.assertEqual(PortalRoutePolicy.objects.count(), count_after_first)
        self.assertGreater(count_after_first, 0)

    def test_seed_creates_falhas_criticas_defaults(self):
        from django.core.management import call_command

        call_command("seed_portal_route_policies", stdout=StringIO())
        policy = PortalRoutePolicy.objects.get(route_name="falhas-criticas")
        self.assertEqual(policy.permissions_any, [QUAL_FALHAS_VIEW])

    def test_fix_open_updates_empty_permissions(self):
        from django.core.management import call_command

        call_command("seed_portal_route_policies", stdout=StringIO())
        PortalRoutePolicy.objects.filter(route_name="operacao-trocas").update(permissions_any=[])
        call_command("seed_portal_route_policies", "--fix-open", stdout=StringIO())
        policy = PortalRoutePolicy.objects.get(route_name="operacao-trocas")
        self.assertEqual(policy.permissions_any, [OPERACAO_TROCAS_VIEW])


class RoutePolicyServiceTests(TestCase):
    def test_roles_with_access_for_qual_route(self):
        roles = roles_with_access_for_policy([QUAL_FALHAS_VIEW], [])
        self.assertIn(ROLE_ADM_PORTAL, roles)
        self.assertIn("qual_gerencia", roles)
        self.assertNotIn("proc_usuario", roles)
        self.assertNotIn("qual_auditoria_fraud", roles)


class PortalRoutePolicyApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            "admin_rbac", password="test123", email="admin_rbac@test.local"
        )
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.admin.groups.add(group)
        self.user = User.objects.create_user(
            "plain_user", password="test123", email="plain_user@test.local"
        )

        from django.core.management import call_command

        call_command("seed_portal_route_policies", stdout=StringIO())

    def test_list_requires_auth(self):
        response = self.client.get("/api/v1/rbac/route-policies/")
        self.assertIn(response.status_code, (401, 403))

    def test_authenticated_user_can_list_policies(self):
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/rbac/route-policies/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(item["route_name"] == "falhas-criticas" for item in response.json()))

    def test_admin_can_patch_route_policy(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/rbac/route-policies/falhas-criticas/",
            {"label": "Falhas (editada)", "permissions_any": [QUAL_FALHAS_VIEW]},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["label"], "Falhas (editada)")

    def test_admin_can_toggle_route_maintenance(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/rbac/route-policies/falhas-criticas/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])
        self.assertFalse(
            PortalRoutePolicy.objects.get(route_name="falhas-criticas").is_active
        )

    def test_maintenance_control_route_cannot_be_deactivated(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/rbac/route-policies/configuracoes-rbac-perfis/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertTrue(
            PortalRoutePolicy.objects.get(
                route_name="configuracoes-rbac-perfis"
            ).is_active
        )

    def test_admin_can_create_and_delete_custom_route(self):
        self.client.force_authenticate(self.admin)
        create = self.client.post(
            "/api/v1/rbac/route-policies/",
            {
                "path": "/secao/indicadores/falhas-custom",
                "label": "Falhas custom",
                "section": "indicadores",
                "permissions_any": [QUAL_FALHAS_VIEW],
            },
            format="json",
        )
        self.assertEqual(create.status_code, 201)
        route_name = create.json()["route_name"]
        self.assertTrue(PortalRoutePolicy.objects.filter(route_name=route_name).exists())

        delete = self.client.delete(f"/api/v1/rbac/route-policies/{route_name}/")
        self.assertEqual(delete.status_code, 200)
        self.assertFalse(PortalRoutePolicy.objects.filter(route_name=route_name).exists())

    def test_catalog_requires_config_hub(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/rbac/route-policies/catalog/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("permissions", data)
        self.assertIn("roles", data)
        self.assertIn("role_permissions", data)
        self.assertTrue(any(group["module"] == "qual" for group in data["permissions"]))

    def test_system_route_rejected(self):
        self.client.force_authenticate(self.admin)
        response = self.client.patch(
            "/api/v1/rbac/route-policies/home/",
            {"permissions_any": [QUAL_FALHAS_VIEW]},
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_code_defaults_match_registry(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get("/api/v1/rbac/route-policies/falhas-criticas/")
        self.assertEqual(response.status_code, 200)
        defaults = response.json()["code_defaults"]
        definition = PORTAL_ROUTE_BY_NAME["falhas-criticas"]
        self.assertEqual(defaults["permissions_any"], list(definition.permissions_any))

    def test_seed_covers_all_registry_routes(self):
        from django.core.management import call_command
        from apps.access.services.route_policies import code_defaults_for_route

        call_command("seed_portal_route_policies", stdout=StringIO())
        for route_name in PORTAL_ROUTE_BY_NAME:
            self.assertTrue(
                PortalRoutePolicy.objects.filter(route_name=route_name).exists(),
                msg=f"policy ausente para {route_name}",
            )
            policy = PortalRoutePolicy.objects.get(route_name=route_name)
            defaults = code_defaults_for_route(route_name)
            self.assertEqual(list(policy.permissions_any or []), defaults["permissions_any"])
            self.assertEqual(list(policy.permissions_all or []), defaults["permissions_all"])

    def test_frontend_registry_mirror_matches_backend(self):
        """Paridade backend portal_route_registry.py ↔ frontend portalRouteRegistry.ts."""
        from pathlib import Path

        ts_path = (
            Path(__file__).resolve().parents[4]
            / "frontend"
            / "src"
            / "constants"
            / "portalRouteRegistry.ts"
        )
        self.assertTrue(ts_path.is_file(), msg=f"espelho front ausente: {ts_path}")
        content = ts_path.read_text(encoding="utf-8")
        front_names = {
            m.group(1)
            for m in __import__("re").finditer(r'route_name:\s*"([^"]+)"', content)
        }
        backend_names = set(PORTAL_ROUTE_BY_NAME.keys())
        self.assertEqual(
            front_names,
            backend_names,
            msg=(
                f"divergência registry: só no back={sorted(backend_names - front_names)} "
                f"só no front={sorted(front_names - backend_names)}"
            ),
        )


class PortalMenuOptionApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            "admin_menu",
            email="admin_menu@test.local",
            password="test123",
        )
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        self.admin.groups.add(group)
        self.user = User.objects.create_user(
            "plain_menu",
            email="plain_menu@test.local",
            password="test123",
        )

        from django.core.management import call_command

        call_command("seed_portal_route_policies", stdout=StringIO())

    def create_menu(self, *, builtin=False):
        self.client.force_authenticate(self.admin)
        return self.client.post(
            "/api/v1/rbac/menu-options/",
            {
                "menu_key": "falhas-menu",
                "label": "Falhas do menu",
                "description": "Atalho configurável",
                "section": "indicadores",
                "route_names": ["falhas-criticas"],
                "is_builtin": builtin,
            },
            format="json",
        )

    def test_authenticated_user_can_list_menu_options(self):
        self.create_menu()
        self.client.force_authenticate(self.user)
        response = self.client.get("/api/v1/rbac/menu-options/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["menu_key"], "falhas-menu")

    def test_admin_can_create_menu_and_assign_routes(self):
        response = self.create_menu()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["route_names"], ["falhas-criticas"])
        self.assertEqual(response.json()["routes"][0]["path"], "/secao/indicadores/falhas")
        self.assertTrue(PortalMenuOption.objects.filter(menu_key="falhas-menu").exists())

    def test_admin_can_deactivate_menu_without_deactivating_route(self):
        self.create_menu()
        response = self.client.patch(
            "/api/v1/rbac/menu-options/falhas-menu/",
            {"is_active": False},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])
        self.assertTrue(
            PortalRoutePolicy.objects.get(route_name="falhas-criticas").is_active
        )

    @override_settings(ACCESS_ENFORCEMENT=True)
    def test_plain_user_cannot_create_menu(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            "/api/v1/rbac/menu-options/",
            {
                "label": "Sem acesso",
                "section": "indicadores",
                "route_names": ["falhas-criticas"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 403)

    def test_builtin_menu_cannot_be_deleted(self):
        self.create_menu(builtin=True)
        response = self.client.delete("/api/v1/rbac/menu-options/falhas-menu/")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(PortalMenuOption.objects.filter(menu_key="falhas-menu").exists())

    def test_custom_menu_delete_preserves_route(self):
        self.create_menu()
        response = self.client.delete("/api/v1/rbac/menu-options/falhas-menu/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(PortalMenuOption.objects.filter(menu_key="falhas-menu").exists())
        self.assertTrue(PortalRoutePolicy.objects.filter(route_name="falhas-criticas").exists())

    def test_assigned_route_cannot_be_deleted(self):
        self.create_menu()
        response = self.client.delete(
            "/api/v1/rbac/route-policies/falhas-criticas/"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Falhas do menu", response.json()["detail"])

    def test_parameterized_route_cannot_be_assigned_to_menu(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/rbac/menu-options/",
            {
                "label": "Detalhe inválido",
                "section": "qualidade",
                "route_names": ["qualidade-auditoria-atividade-detalhe"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)

    def test_admin_can_create_group_menu_without_routes(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/rbac/menu-options/",
            {
                "menu_key": "grupo-indicadores",
                "label": "Grupo Indicadores",
                "section": "indicadores",
                "route_names": [],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["route_names"], [])

    def test_admin_can_create_nested_submenu(self):
        self.client.force_authenticate(self.admin)
        self.client.post(
            "/api/v1/rbac/menu-options/",
            {
                "menu_key": "grupo-indicadores",
                "label": "Grupo Indicadores",
                "section": "indicadores",
                "route_names": [],
            },
            format="json",
        )
        response = self.client.post(
            "/api/v1/rbac/menu-options/",
            {
                "menu_key": "atalho-indicadores",
                "label": "Atalho Indicadores",
                "section": "indicadores",
                "parent_menu_key": "grupo-indicadores",
                "route_names": ["falhas-criticas"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["parent_menu_key"], "grupo-indicadores")

    def test_admin_can_create_menu_with_null_parent_menu_key(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(
            "/api/v1/rbac/menu-options/",
            {
                "label": "Menu raiz null parent",
                "section": "qualidade",
                "parent_menu_key": None,
                "route_names": ["falhas-criticas"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["parent_menu_key"], None)
