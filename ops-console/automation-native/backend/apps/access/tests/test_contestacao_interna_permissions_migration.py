from importlib import import_module

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.access.models import PortalRoleDefinition, PortalRoutePolicy
from apps.access.services.role_definitions import compute_allowed_routes_for_roles


MIGRATION_MODULE = "apps.access.migrations.0017_contestacao_interna_permissions"


class ContestacaoInternaPermissionsMigrationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        executor = MigrationExecutor(connection)
        cls.historical_apps = executor.loader.project_state(
            [("access", "0016_split_auditoria_compliance_permissions")]
        ).apps

    def create_role(self, *, role, permissions, granted_routes):
        HistoricalRole = self.historical_apps.get_model(
            "access", "PortalRoleDefinition"
        )
        role_definition, _ = HistoricalRole.objects.update_or_create(
            role=role,
            defaults={
                "label": role,
                "area": "qualidade",
                "permissions": permissions,
                "granted_routes": granted_routes,
                "default_scope": "global",
            },
        )
        return role_definition

    def test_adiciona_permissoes_sem_apagar_customizacoes(self):
        custom_routes = ["section", "rota-personalizada"]
        self.create_role(
            role="qual_contestacao_fraud",
            permissions=["portal.secao.view", "permissao.personalizada"],
            granted_routes=custom_routes,
        )
        self.create_role(
            role="qual_contestacao_compliance",
            permissions=["portal.secao.view"],
            granted_routes=None,
        )

        migration = import_module(MIGRATION_MODULE)
        migration.add_contestacao_interna_permissions(self.historical_apps, None)

        fraud = PortalRoleDefinition.objects.get(role="qual_contestacao_fraud")
        compliance = PortalRoleDefinition.objects.get(role="qual_contestacao_compliance")
        fraud.refresh_from_db()
        compliance.refresh_from_db()
        self.assertTrue(
            {
                "qual.contestacao_interna.fraud.view",
                "qual.contestacao_interna.fraud.analyze",
                "permissao.personalizada",
            }.issubset(fraud.permissions)
        )
        self.assertEqual(
            fraud.granted_routes,
            [*custom_routes, "qualidade-fraud-contestacao-interna"],
        )
        self.assertTrue(
            {
                "qual.contestacao_interna.compliance.view",
                "qual.contestacao_interna.compliance.analyze",
            }.issubset(compliance.permissions)
        )
        self.assertIsNone(compliance.granted_routes)

    def test_rota_fraud_fica_disponivel_e_aplicacao_e_idempotente(self):
        self.create_role(
            role="qual_contestacao_fraud",
            permissions=["portal.secao.view"],
            granted_routes=["section"],
        )
        PortalRoutePolicy.objects.update_or_create(
            route_name="qualidade-fraud-contestacao-interna",
            defaults={
                "path": "/secao/qualidade/contestacao/fraud/interna",
                "label": "Contestação Fraud — interna",
                "section": "qualidade",
                "permissions_any": ["qual.contestacao_interna.fraud.view"],
            },
        )

        migration = import_module(MIGRATION_MODULE)
        migration.add_contestacao_interna_permissions(self.historical_apps, None)
        migration.add_contestacao_interna_permissions(self.historical_apps, None)

        fraud = PortalRoleDefinition.objects.get(role="qual_contestacao_fraud")
        self.assertEqual(
            fraud.permissions.count("qual.contestacao_interna.fraud.view"), 1
        )
        self.assertEqual(
            fraud.granted_routes.count("qualidade-fraud-contestacao-interna"), 1
        )
        self.assertIn(
            "qualidade-fraud-contestacao-interna",
            compute_allowed_routes_for_roles({"qual_contestacao_fraud"}),
        )

    def test_reverse_preserva_valores_preexistentes(self):
        original_permissions = [
            "portal.secao.view",
            "qual.contestacao_interna.fraud.view",
        ]
        original_routes = ["section", "qualidade-fraud-contestacao-interna"]
        self.create_role(
            role="qual_contestacao_fraud",
            permissions=original_permissions,
            granted_routes=original_routes,
        )

        migration = import_module(MIGRATION_MODULE)
        migration.remove_contestacao_interna_permissions(self.historical_apps, None)

        fraud = PortalRoleDefinition.objects.get(role="qual_contestacao_fraud")
        self.assertEqual(fraud.permissions, original_permissions)
        self.assertEqual(fraud.granted_routes, original_routes)


class ContestacaoInternaPermissionsMigrationCycleTests(TransactionTestCase):
    migrate_from = ("access", "0016_split_auditoria_compliance_permissions")
    migrate_to = ("access", "0017_contestacao_interna_permissions")
    fraud_role = "qual_contestacao_fraud"
    route_name = "qualidade-fraud-contestacao-interna"

    def migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([target])
        return executor.loader.project_state([target]).apps

    @staticmethod
    def snapshot(model, lookup):
        row = model.objects.filter(**lookup).values().first()
        if row is None:
            return None
        row.pop("updated_at", None)
        return row

    @staticmethod
    def restore(model, lookup, snapshot):
        if snapshot is None:
            model.objects.filter(**lookup).delete()
            return
        defaults = {
            key: value for key, value in snapshot.items() if key not in lookup
        }
        model.objects.update_or_create(**lookup, defaults=defaults)

    def test_ciclo_real_forward_rota_calculada_e_reverse_preservativo(self):
        from_apps = self.migrate(self.migrate_from)
        HistoricalRole = from_apps.get_model("access", "PortalRoleDefinition")
        HistoricalRoute = from_apps.get_model("access", "PortalRoutePolicy")
        role_lookup = {"role": self.fraud_role}
        route_lookup = {"route_name": self.route_name}
        original_role = self.snapshot(HistoricalRole, role_lookup)
        original_route = self.snapshot(HistoricalRoute, route_lookup)

        try:
            HistoricalRole.objects.update_or_create(
                **role_lookup,
                defaults={
                    "label": self.fraud_role,
                    "area": "qualidade",
                    "permissions": ["portal.secao.view", "permissao.personalizada"],
                    "granted_routes": ["section", "rota-personalizada"],
                    "default_scope": "global",
                },
            )
            HistoricalRoute.objects.update_or_create(
                **route_lookup,
                defaults={
                    "path": "/secao/qualidade/contestacao/fraud/interna",
                    "label": "Contestação Fraud — interna",
                    "section": "qualidade",
                    "permissions_any": ["qual.contestacao_interna.fraud.view"],
                },
            )

            to_apps = self.migrate(self.migrate_to)
            MigratedRole = to_apps.get_model("access", "PortalRoleDefinition")
            fraud = MigratedRole.objects.get(**role_lookup)
            self.assertEqual(
                fraud.permissions.count("qual.contestacao_interna.fraud.view"), 1
            )
            self.assertEqual(
                fraud.permissions.count("qual.contestacao_interna.fraud.analyze"), 1
            )
            self.assertIn("permissao.personalizada", fraud.permissions)
            self.assertEqual(fraud.granted_routes.count(self.route_name), 1)
            self.assertIn("rota-personalizada", fraud.granted_routes)
            self.assertIn(
                self.route_name,
                compute_allowed_routes_for_roles({self.fraud_role}),
            )

            rolled_back_apps = self.migrate(self.migrate_from)
            RolledBackRole = rolled_back_apps.get_model(
                "access", "PortalRoleDefinition"
            )
            fraud = RolledBackRole.objects.get(**role_lookup)
            self.assertIn("qual.contestacao_interna.fraud.view", fraud.permissions)
            self.assertIn("qual.contestacao_interna.fraud.analyze", fraud.permissions)
            self.assertIn(self.route_name, fraud.granted_routes)
            self.assertIn("permissao.personalizada", fraud.permissions)
            self.assertIn("rota-personalizada", fraud.granted_routes)
        finally:
            restored_apps = self.migrate(self.migrate_to)
            RestoredRole = restored_apps.get_model("access", "PortalRoleDefinition")
            RestoredRoute = restored_apps.get_model("access", "PortalRoutePolicy")
            self.restore(RestoredRole, role_lookup, original_role)
            self.restore(RestoredRoute, route_lookup, original_route)
