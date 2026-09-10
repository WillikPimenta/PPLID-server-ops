"""Matriz perfil × ambiente do portal (permissões + APIs com enforcement)."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.access.constants import (
    ALL_ROLES,
    ROLE_ADM_PORTAL,
    ROLE_OP_AGENTE,
    ROLE_OP_GERENCIA,
    ROLE_OP_LIDER,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_ASSISTENTE,
    ROLE_PLAN_GERENCIA,
    ROLE_PROC_USUARIO,
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_AUDITORIA_FRAUD,
    ROLE_QUAL_CAPACITACAO,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_FRAUD,
    ROLE_QUAL_GERENCIA,
    role_group_name,
)
from apps.access.portal_environments import (
    PORTAL_ENVIRONMENTS,
    build_role_environment_matrix,
    environments_for_role,
)
from apps.access.resolve import user_has_any_permission
from apps.access.roles import permissions_for_role

User = get_user_model()

# Expectativas de negócio documentadas — falha se roles.py divergir sem revisão.
DOCUMENTED_ROLE_ENVIRONMENTS: dict[str, frozenset[str]] = {
    ROLE_OP_AGENTE: frozenset(
        {
            "falhas_criticas",
            "escala_consulta",
            "controle_jornada_status",
            "ocorrencias_operacao",
            "produtividade",
            "case_manager",
            "noticias",
        }
    ),
    ROLE_OP_LIDER: frozenset(
        {
            "falhas_criticas",
            "escala_consulta",
            "controle_jornada_painel",
            "ocorrencias_operacao",
            "produtividade",
            "case_manager",
            "noticias",
        }
    ),
    ROLE_OP_GERENCIA: frozenset(
        {
            "falhas_criticas",
            "escala_consulta",
            "controle_jornada_painel",
            "ocorrencias_operacao",
            "produtividade",
            "case_manager",
            "noticias",
        }
    ),
    ROLE_PLAN_ASSISTENTE: frozenset(
        {
            "headcount",
            "megazord",
            "dashboard_headcount",
            "falhas_criticas",
            "qualidade_operacional",
            "auditoria",
            "contestacao",
            "escala_consulta",
            "monitoramento",
            "ocorrencias_planejamento",
            "noticias",
        }
    ),
    ROLE_PLAN_ANALISTA: frozenset(
        {
            "headcount",
            "megazord",
            "dashboard_headcount",
            "falhas_criticas",
            "qualidade_operacional",
            "auditoria",
            "contestacao",
            "escala_consulta",
            "escala_import",
            "escala_generate",
            "escala_publish",
            "monitoramento",
            "automacao",
            "ocorrencias_planejamento",
            "produtividade",
            "case_manager",
            "noticias",
        }
    ),
    ROLE_PLAN_GERENCIA: frozenset(
        {
            "headcount",
            "megazord",
            "dashboard_headcount",
            "falhas_criticas",
            "qualidade_operacional",
            "auditoria",
            "contestacao",
            "escala_consulta",
            "escala_import",
            "escala_generate",
            "escala_publish",
            "monitoramento",
            "automacao",
            "ocorrencias_planejamento",
            "produtividade",
            "case_manager",
            "console_ops",
            "noticias",
        }
    ),
    ROLE_PROC_USUARIO: frozenset({"suporte_claro"}),
    ROLE_QUAL_AUDITORIA_FRAUD: frozenset({"auditoria", "contestacao"}),
    ROLE_QUAL_AUDITORIA_COMPLIANCE: frozenset({"auditoria", "contestacao"}),
    ROLE_QUAL_CONTESTACAO_FRAUD: frozenset({"auditoria", "contestacao"}),
    ROLE_QUAL_CONTESTACAO_COMPLIANCE: frozenset({"auditoria", "contestacao"}),
    ROLE_QUAL_CAPACITACAO: frozenset(),
    ROLE_QUAL_GERENCIA: frozenset(
        {"falhas_criticas", "qualidade_operacional", "auditoria", "contestacao"}
    ),
    ROLE_ADM_PORTAL: frozenset(env.id for env in PORTAL_ENVIRONMENTS),
}


def _ensure_role_group(role: str) -> Group:
    group, _ = Group.objects.get_or_create(name=role_group_name(role))
    return group


def _user_for_role(username: str, role: str) -> User:
    """Usuário com perfil RBAC explícito (sem inferência por AgentHistory)."""
    user = User.objects.create_user(username, email=f"{username}@test.local", password="x")
    user.groups.add(_ensure_role_group(role))
    return user


class PortalEnvironmentMatrixTests(TestCase):
    """Garante que cada perfil RBAC acessa exatamente os ambientes previstos."""

    def test_documented_matrix_matches_role_definitions(self):
        matrix = build_role_environment_matrix()
        for role, expected in DOCUMENTED_ROLE_ENVIRONMENTS.items():
            with self.subTest(role=role):
                self.assertEqual(matrix[role], set(expected))

    def test_permissions_for_role_align_with_environments(self):
        for role in ALL_ROLES:
            perms = permissions_for_role(role)
            for env in PORTAL_ENVIRONMENTS:
                expected = any(code in perms for code in env.permissions_any)
                with self.subTest(role=role, environment=env.id):
                    self.assertEqual(env.role_can_access(role), expected)

    def test_resolve_user_has_any_permission_matches_environment(self):
        user = _user_for_role("c96001a", ROLE_PLAN_ANALISTA)
        for env in PORTAL_ENVIRONMENTS:
            with self.subTest(environment=env.id):
                self.assertEqual(
                    user_has_any_permission(user, *env.permissions_any),
                    env.role_can_access(ROLE_PLAN_ANALISTA),
                )


# Ambientes cuja API usa permissão dedicada (não ANY_ESCALA_FLEX_VIEW).
API_STRICT_ENVIRONMENT_IDS = frozenset(
    {
        "headcount",
        "dashboard_headcount",
        "produtividade",
        "case_manager",
        "falhas_criticas",
        "qualidade_operacional",
        "auditoria",
        "contestacao",
        "escala_consulta",
        "escala_import",
        "escala_generate",
        "escala_publish",
        "automacao",
        "suporte_claro",
        "noticias",
        "falhas_import",
        "console_ops",
        "gestao_usuarios",
    }
)


@override_settings(ACCESS_ENFORCEMENT=True, ESCALA_FLEX_OPEN_ACCESS=False)
class PortalEnvironmentApiEnforcementTests(TestCase):
    """Verifica HTTP 403/200 nos endpoints com permissão dedicada."""

    AUTHORIZED_OK = {200, 201, 204, 400, 404, 405}

    @classmethod
    def setUpTestData(cls):
        cls.users = {
            role: _user_for_role(f"c96{idx:03d}a", role) for idx, role in enumerate(ALL_ROLES)
        }

    def setUp(self):
        self.client = APIClient()

    def _probe(self, user: User, env) -> int:
        self.client.force_authenticate(user=user)
        method = env.api_method.lower()
        if method == "post":
            response = self.client.post(env.api_path, {}, format="multipart")
        else:
            response = getattr(self.client, method)(env.api_path)
        return response.status_code

    def test_strict_api_access_matches_matrix(self):
        strict_envs = [
            env for env in PORTAL_ENVIRONMENTS if env.id in API_STRICT_ENVIRONMENT_IDS and env.api_path
        ]
        for role in ALL_ROLES:
            user = self.users[role]
            allowed_envs = {env.id for env in strict_envs if env.role_can_access_api(role)}
            for env in strict_envs:
                with self.subTest(role=role, environment=env.id, path=env.api_path):
                    status_code = self._probe(user, env)
                    if env.id in allowed_envs:
                        self.assertIn(
                            status_code,
                            self.AUTHORIZED_OK,
                            f"{role} deveria acessar {env.id}",
                        )
                    else:
                        self.assertEqual(
                            status_code,
                            403,
                            f"{role} não deveria acessar {env.id}",
                        )

    def test_op_agente_denied_headcount_api(self):
        self.client.force_authenticate(user=self.users[ROLE_OP_AGENTE])
        self.assertEqual(self.client.get("/api/v1/agents/").status_code, 403)

    def test_proc_user_only_suporte_claro(self):
        proc = self.users[ROLE_PROC_USUARIO]
        allowed = environments_for_role(ROLE_PROC_USUARIO)
        self.assertEqual(allowed, {"suporte_claro"})
        self.client.force_authenticate(user=proc)
        self.assertEqual(self.client.get("/api/v1/suporte-claro/registros/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/agents/").status_code, 403)

    def test_adm_portal_reaches_admin_endpoints(self):
        adm = self.users[ROLE_ADM_PORTAL]
        self.client.force_authenticate(user=adm)
        self.assertIn(self.client.get("/api/v1/portal-users/").status_code, self.AUTHORIZED_OK)
        self.assertIn(self.client.get("/api/v1/falhas/sync/imports/").status_code, self.AUTHORIZED_OK)

    def test_cyber_psa_access_flag_matches_permission(self):
        for role in ALL_ROLES:
            user = self.users[role]
            expected = environments_for_role(role)
            self.client.force_authenticate(user=user)
            response = self.client.get("/api/v1/cyber-psa/access/")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.data["has_access"],
                "psa_cyber" in expected,
                f"{role} has_access cyber psa",
            )
