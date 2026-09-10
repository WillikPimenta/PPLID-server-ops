from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from io import StringIO

from apps.access.constants import ROLE_OP_AGENTE, ROLE_PLAN_ANALISTA, role_group_name
from apps.access.registry import (
    PLANEJAMENTO_HEADCOUNT_VIEW,
    PLANEJAMENTO_MONITORAMENTO_VIEW,
    QUAL_FALHAS_VIEW,
)
from django.contrib.auth.models import Group

User = get_user_model()


class AuditPortalAccessCommandTests(TestCase):
    def test_audit_shows_roles_and_permission_check(self):
        user = User.objects.create_user("c94003a", password="x")
        Group.objects.get_or_create(name=role_group_name(ROLE_PLAN_ANALISTA))
        user.groups.add(Group.objects.get(name=role_group_name(ROLE_PLAN_ANALISTA)))

        out = StringIO()
        call_command(
            "audit_portal_access",
            "--username",
            "c94003a",
            f"--permissions={PLANEJAMENTO_HEADCOUNT_VIEW}",
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("plan_analista", output)
        self.assertIn(PLANEJAMENTO_HEADCOUNT_VIEW, output)

    def test_audit_op_agente_denied_planejamento(self):
        user = User.objects.create_user("c95001a", password="x")
        Group.objects.get_or_create(name=role_group_name(ROLE_OP_AGENTE))
        user.groups.add(Group.objects.get(name=role_group_name(ROLE_OP_AGENTE)))

        out = StringIO()
        call_command(
            "audit_portal_access",
            "--username",
            "c95001a",
            "--permissions",
            PLANEJAMENTO_HEADCOUNT_VIEW,
            PLANEJAMENTO_MONITORAMENTO_VIEW,
            QUAL_FALHAS_VIEW,
            stdout=out,
        )
        output = out.getvalue()
        self.assertIn("op_agente", output)
        self.assertIn(f"Checagem {PLANEJAMENTO_HEADCOUNT_VIEW}: NEGADO", output)
        self.assertIn(f"Checagem {PLANEJAMENTO_MONITORAMENTO_VIEW}: NEGADO", output)
        self.assertIn(f"Checagem {QUAL_FALHAS_VIEW}: OK", output)
