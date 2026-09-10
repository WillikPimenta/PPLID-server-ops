from importlib import import_module

from django.apps import apps as django_apps
from django.test import TestCase

from apps.access.models import PortalRoleDefinition


MIGRATION_MODULE = "apps.access.migrations.0019_revisao_falhas_permissions"
EXPECTED_PERMISSIONS = {
    "qual.capacitacao.revisao_falhas.view",
    "qual.capacitacao.revisao_falhas.decide",
    "qual.capacitacao.revisao_falhas.change",
}
ROUTE_NAME = "qualidade-capacitacao-revisao-falhas"


class RevisaoFalhasPermissionsMigrationTests(TestCase):
    def test_adds_permissions_and_route_without_losing_customizations(self):
        customized = PortalRoleDefinition.objects.create(
            role="qual_capacitacao",
            permissions=["permissao.personalizada"],
            granted_routes=["rota-personalizada"],
            default_scope="global",
        )
        unrestricted = PortalRoleDefinition.objects.create(
            role="qual_gerencia",
            permissions=[],
            granted_routes=None,
            default_scope="global",
        )

        migration = import_module(MIGRATION_MODULE)
        migration.add_revisao_falhas_permissions(django_apps, None)
        migration.add_revisao_falhas_permissions(django_apps, None)

        customized.refresh_from_db()
        unrestricted.refresh_from_db()
        self.assertTrue(EXPECTED_PERMISSIONS.issubset(customized.permissions))
        self.assertIn("permissao.personalizada", customized.permissions)
        self.assertEqual(customized.granted_routes, ["rota-personalizada", ROUTE_NAME])
        self.assertEqual(customized.granted_routes.count(ROUTE_NAME), 1)
        self.assertTrue(EXPECTED_PERMISSIONS.issubset(unrestricted.permissions))
        self.assertIsNone(unrestricted.granted_routes)

    def test_ignores_unrelated_roles(self):
        unrelated = PortalRoleDefinition.objects.create(
            role="op_agente",
            permissions=["permissao.existente"],
            granted_routes=["rota-existente"],
            default_scope="own",
        )

        migration = import_module(MIGRATION_MODULE)
        migration.add_revisao_falhas_permissions(django_apps, None)

        unrelated.refresh_from_db()
        self.assertEqual(unrelated.permissions, ["permissao.existente"])
        self.assertEqual(unrelated.granted_routes, ["rota-existente"])
