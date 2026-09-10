"""Constantes RBAC do portal PPLID."""

from __future__ import annotations

ROLE_GROUP_PREFIX = "role:"

# Perfis consolidados (docs/acessos-portal-rbac.md §3)
ROLE_PLAN_GERENCIA = "plan_gerencia"
ROLE_PLAN_ANALISTA = "plan_analista"
ROLE_PLAN_ASSISTENTE = "plan_assistente"
ROLE_OP_GERENCIA = "op_gerencia"
ROLE_OP_LIDER = "op_lider"
ROLE_OP_AGENTE = "op_agente"
ROLE_PROC_USUARIO = "proc_usuario"
# Legado — substituído pelos perfis específicos abaixo; mantido para grupos já atribuídos.
ROLE_QUAL_USUARIO = "qual_usuario"
ROLE_QUAL_AUDITORIA_FRAUD = "qual_auditoria_fraud"
ROLE_QUAL_AUDITORIA_COMPLIANCE = "qual_auditoria_compliance"
ROLE_QUAL_CONTESTACAO_FRAUD = "qual_contestacao_fraud"
ROLE_QUAL_CONTESTACAO_COMPLIANCE = "qual_contestacao_compliance"
ROLE_QUAL_CAPACITACAO = "qual_capacitacao"
ROLE_QUAL_GERENCIA = "qual_gerencia"
ROLE_ADM_PORTAL = "adm_portal"

ALL_ROLES: tuple[str, ...] = (
    ROLE_PLAN_GERENCIA,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_ASSISTENTE,
    ROLE_OP_GERENCIA,
    ROLE_OP_LIDER,
    ROLE_OP_AGENTE,
    ROLE_PROC_USUARIO,
    ROLE_QUAL_AUDITORIA_FRAUD,
    ROLE_QUAL_AUDITORIA_COMPLIANCE,
    ROLE_QUAL_CONTESTACAO_FRAUD,
    ROLE_QUAL_CONTESTACAO_COMPLIANCE,
    ROLE_QUAL_CAPACITACAO,
    ROLE_QUAL_GERENCIA,
    ROLE_ADM_PORTAL,
)

# Perfis fora do catálogo atribuível, ainda resolvidos se o grupo Django existir.
LEGACY_ROLES: frozenset[str] = frozenset({ROLE_QUAL_USUARIO})

MANUAL_ROLES: frozenset[str] = frozenset(
    {
        ROLE_PLAN_GERENCIA,
        ROLE_OP_GERENCIA,
        ROLE_QUAL_GERENCIA,
        ROLE_ADM_PORTAL,
    }
)

SCOPE_OWN = "own"
SCOPE_TEAM = "team"
SCOPE_GLOBAL = "global"
SCOPE_LOCALITY = "locality"

DataScope = str

DEFAULT_RBAC_DATA_DIR = "data/rbac"
MANUAL_ROLES_FILENAME = "manual_roles.yaml"
LEGACY_GROUP_MAP_FILENAME = "legacy_group_map.yaml"
PORTAL_USERS_FILENAME = "portal_users.yaml"

# Grupos legados Falhas (transição)
LEGACY_GROUP_GLOBAL = "Lideranca_Global"
LEGACY_GROUP_BRASILIA = "Lideranca_Brasilia"
LEGACY_GROUP_SAO_CARLOS = "Lideranca_SaoCarlos"
LEGACY_GROUP_SYNC = "Operacao_Sync"

LEGACY_FALHAS_GROUPS: tuple[str, ...] = (
    LEGACY_GROUP_GLOBAL,
    LEGACY_GROUP_BRASILIA,
    LEGACY_GROUP_SAO_CARLOS,
    LEGACY_GROUP_SYNC,
)


def role_group_name(role: str) -> str:
    return f"{ROLE_GROUP_PREFIX}{role}"


def parse_role_from_group(group_name: str) -> str | None:
    if group_name.startswith(ROLE_GROUP_PREFIX):
        return group_name[len(ROLE_GROUP_PREFIX) :]
    return None


def is_role_group(group_name: str) -> bool:
    return group_name.startswith(ROLE_GROUP_PREFIX)
