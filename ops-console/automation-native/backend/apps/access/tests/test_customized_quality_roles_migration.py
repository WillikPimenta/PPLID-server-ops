from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.access.models import PortalRoleDefinition


MIGRATION_MODULE = "apps.access.migrations.0008_seed_customized_quality_roles"
DEFAULT_ROUTES_MIGRATION_MODULE = "apps.access.migrations.0009_add_quality_default_routes"


class CustomizedQualityRolesMigrationTests(TestCase):
    def test_seed_reproduces_local_role_definitions_and_groups(self):
        migration = import_module(MIGRATION_MODULE)

        PortalRoleDefinition.objects.filter(
            role="qual_contestacao_compliance"
        ).update(
            label="Configuração divergente",
            permissions=[],
            granted_routes=None,
            default_scope="global",
        )
        Group.objects.filter(name="role:qual_contestacao_compliance").delete()

        migration.seed_customized_quality_roles(django_apps, None)

        expected_by_role = {
            definition["role"]: definition for definition in migration.ROLE_DEFINITIONS
        }
        rows = PortalRoleDefinition.objects.filter(
            role__in=expected_by_role
        ).order_by("role")

        self.assertEqual(rows.count(), len(expected_by_role))
        for row in rows:
            expected = expected_by_role[row.role]
            self.assertEqual(row.label, expected["label"])
            self.assertEqual(row.area, expected["area"])
            self.assertEqual(row.permissions, expected["permissions"])
            self.assertEqual(row.granted_routes, expected["granted_routes"])
            self.assertEqual(row.default_scope, expected["default_scope"])
            self.assertEqual(row.is_editable, expected["is_editable"])
            self.assertTrue(
                Group.objects.filter(name=f"role:{row.role}").exists()
            )

    def test_add_default_routes_preserves_existing_custom_routes(self):
        migration = import_module(DEFAULT_ROUTES_MIGRATION_MODULE)
        definition = PortalRoleDefinition.objects.get(
            role="qual_auditoria_compliance"
        )
        definition.granted_routes = ["section", "rota-personalizada"]
        definition.save(update_fields=["granted_routes"])

        migration.add_quality_default_routes(django_apps, None)

        definition.refresh_from_db()
        self.assertEqual(
            set(definition.granted_routes),
            {
                "section",
                "rota-personalizada",
                "qualidade-compliance-auditoria-fila",
                "qualidade-compliance-auditoria-consulta",
            },
        )
