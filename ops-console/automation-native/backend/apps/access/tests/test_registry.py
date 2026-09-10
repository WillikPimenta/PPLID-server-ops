from django.test import TestCase

from apps.access.constants import ALL_ROLES, ROLE_ADM_PORTAL, ROLE_OP_AGENTE, ROLE_PLAN_ASSISTENTE
from apps.access.registry import (
    ALL_PERMISSIONS,
    PLANEJAMENTO_OCORRENCIAS_APPROVE,
    QUAL_AUDITORIA_RESULT_CHANGE,
)
from apps.access.roles import ROLE_DEFINITIONS, permissions_for_role, validate_role_definitions


class RegistryTests(TestCase):
    def test_all_roles_have_definitions(self):
        for role in ALL_ROLES:
            self.assertIn(role, ROLE_DEFINITIONS)
            self.assertTrue(ROLE_DEFINITIONS[role]["permissions"])

    def test_validate_role_definitions_passes(self):
        validate_role_definitions()

    def test_plan_assistente_can_approve_occurrences(self):
        perms = permissions_for_role(ROLE_PLAN_ASSISTENTE)
        self.assertIn(PLANEJAMENTO_OCORRENCIAS_APPROVE, perms)

    def test_op_agente_has_status_change(self):
        from apps.access.registry import OPERACAO_STATUS_CHANGE

        perms = permissions_for_role(ROLE_OP_AGENTE)
        self.assertIn(OPERACAO_STATUS_CHANGE, perms)

    def test_all_role_permissions_in_registry(self):
        for role in ALL_ROLES:
            unknown = permissions_for_role(role) - ALL_PERMISSIONS
            self.assertEqual(unknown, set(), msg=f"Perfil {role} tem permissões inválidas: {unknown}")

    def test_adm_portal_covers_full_catalog(self):
        perms = permissions_for_role(ROLE_ADM_PORTAL)
        self.assertEqual(perms, set(ALL_PERMISSIONS))

    def test_result_change_is_not_granted_to_non_admin_roles_by_default(self):
        for role in (item for item in ALL_ROLES if item != ROLE_ADM_PORTAL):
            self.assertNotIn(QUAL_AUDITORIA_RESULT_CHANGE, permissions_for_role(role))
