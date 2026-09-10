from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase

from apps.access.constants import (
    ROLE_ADM_PORTAL,
    ROLE_OP_AGENTE,
    ROLE_QUAL_GERENCIA,
    role_group_name,
)
from apps.access.registry import (
    ADM_FALHAS_IMPORT,
    OPERACAO_JORNADA_PAINEL_VIEW,
    OPERACAO_STATUS_CHANGE,
    PLANEJAMENTO_HEADCOUNT_VIEW,
    PROCESSOS_SUPORTE_CLARO_VIEW,
    QUAL_FALHAS_VIEW,
)
from apps.access.resolve import resolve_user_access, user_has_permission
from apps.falhas_criticas.constants import GROUP_GLOBAL
from apps.workforce.models import Agent, AgentHistory, UserProfile

User = get_user_model()


class ResolveAccessTests(TestCase):
    def setUp(self):
        Group.objects.get_or_create(name=GROUP_GLOBAL)

    def _link_user_agent(self, username: str, team: str, title: str) -> User:
        user = User.objects.create_user(username=username, password="test123")
        agent = Agent.objects.create(
            user_lan_id=username,
            full_name=f"User {username}",
            active=True,
            hire_date=date(2020, 1, 1),
        )
        UserProfile.objects.create(user=user, agent=agent)
        AgentHistory.objects.create(
            agent=agent,
            team=team,
            job_title=title,
            start_date=date(2024, 1, 1),
            active=True,
        )
        return user

    def test_superuser_bypass(self):
        user = User.objects.create_superuser("admin", password="test123")
        access = resolve_user_access(user)
        self.assertTrue(access["bypass"])
        self.assertIn(ADM_FALHAS_IMPORT, access["permissions"])

    def test_op_agente_permissions(self):
        user = self._link_user_agent("c90101a", "Operacional/Fraud", "Agente Backoffice I")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        user.groups.add(group)
        access = resolve_user_access(user)
        self.assertIn(ROLE_OP_AGENTE, access["roles"])
        self.assertIn(OPERACAO_STATUS_CHANGE, access["permissions"])
        self.assertIn(QUAL_FALHAS_VIEW, access["permissions"])
        self.assertNotIn(PLANEJAMENTO_HEADCOUNT_VIEW, access["permissions"])
        planejamento = [
            code for code in access["permissions"] if code.startswith("planejamento.")
        ]
        self.assertEqual(planejamento, [], "Op. Agente não deve ter permissões de planejamento")

    def test_legacy_global_does_not_grant_rbac_role(self):
        user = User.objects.create_user("legacy_global", password="test123")
        user.groups.add(Group.objects.get(name=GROUP_GLOBAL))
        access = resolve_user_access(user)
        self.assertNotIn(ROLE_QUAL_GERENCIA, access["roles"])
        self.assertEqual(access["permissions"], [])

    def test_manual_adm_portal_group(self):
        user = User.objects.create_user("c91763a", password="test123")
        group, _ = Group.objects.get_or_create(name=role_group_name(ROLE_ADM_PORTAL))
        user.groups.add(group)
        access = resolve_user_access(user)
        self.assertIn(ROLE_ADM_PORTAL, access["roles"])
        self.assertTrue(user_has_permission(user, ADM_FALHAS_IMPORT))
        self.assertIn(OPERACAO_JORNADA_PAINEL_VIEW, access["permissions"])
        self.assertIn(PROCESSOS_SUPORTE_CLARO_VIEW, access["permissions"])
        self.assertFalse(access["bypass"])
