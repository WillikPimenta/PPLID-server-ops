"""Helpers para definições de perfil RBAC persistidas."""

from __future__ import annotations

from apps.access.constants import (
    ALL_ROLES,
    ROLE_ADM_PORTAL,
    SCOPE_GLOBAL,
    SCOPE_OWN,
    SCOPE_TEAM,
)
from apps.access.models import PortalRoleDefinition, PortalRoutePolicy
from apps.access.portal_route_registry import SYSTEM_ROUTE_NAMES
from apps.access.registry import ALL_PERMISSIONS
from apps.access.services.route_policies import ROLE_LABELS, effective_permissions_for_policy

ROLE_AREAS: dict[str, str] = {
    "plan_gerencia": "planejamento",
    "plan_analista": "planejamento",
    "plan_assistente": "planejamento",
    "op_gerencia": "operacao",
    "op_lider": "operacao",
    "op_agente": "operacao",
    "proc_usuario": "processos",
    "qual_usuario": "qualidade",
    "qual_auditoria_fraud": "qualidade",
    "qual_auditoria_compliance": "qualidade",
    "qual_contestacao_fraud": "qualidade",
    "qual_contestacao_compliance": "qualidade",
    "qual_capacitacao": "qualidade",
    "qual_gerencia": "qualidade",
    "adm_portal": "administracao",
}

AREA_LABELS: dict[str, str] = {
    "planejamento": "Planejamento",
    "operacao": "Operação",
    "processos": "Processos",
    "qualidade": "Qualidade",
    "administracao": "Administração",
    "outros": "Outros",
}

SCOPE_LABELS: dict[str, str] = {
    SCOPE_OWN: "Próprio",
    SCOPE_TEAM: "Time",
    SCOPE_GLOBAL: "Global",
}

VALID_SCOPES = frozenset({SCOPE_OWN, SCOPE_TEAM, SCOPE_GLOBAL})


def stored_role_definition(role: str) -> PortalRoleDefinition | None:
    try:
        return PortalRoleDefinition.objects.filter(role=role).first()
    except Exception:
        return None


def stored_permissions_for_role(role: str) -> set[str] | None:
    row = stored_role_definition(role)
    if row is None:
        return None
    return set(row.permissions or [])


def stored_scope_for_role(role: str) -> str | None:
    row = stored_role_definition(role)
    if row is None:
        return None
    return row.default_scope


def code_defaults_for_role(role: str) -> dict:
    from apps.access.roles import ROLE_DEFINITIONS

    definition = ROLE_DEFINITIONS.get(role, {})
    perms = definition.get("permissions", set())
    return {
        "permissions": sorted(perms),
        "default_scope": definition.get("default_scope", SCOPE_OWN),
    }


def is_role_customized(row: PortalRoleDefinition) -> bool:
    """True se o perfil foi editado além do padrão (extras ou escopo).

    Snapshot só desatualizado (faltam perms novas do código) NÃO conta como
    customizado — assim `--sync-uncustomized` propaga permissões adicionadas
    no registry (ex.: planejamento.megazord.view).
    """
    defaults = code_defaults_for_role(row.role)
    if row.default_scope != defaults["default_scope"]:
        return True
    if row.granted_routes is not None:
        return True
    stored = set(row.permissions or [])
    default = set(defaults["permissions"])
    return bool(stored - default)


def _role_can_access_route_permissions(
    role_permissions: set[str],
    permissions_any: list[str],
    permissions_all: list[str],
) -> bool:
    if permissions_all:
        if not all(code in role_permissions for code in permissions_all):
            return False
    if permissions_any:
        return any(code in role_permissions for code in permissions_any)
    return False


def routes_accessible_by_permissions(
    role_permissions: set[str],
    *,
    granted_routes: list[str] | None = None,
) -> list[dict]:
    allowed = set(granted_routes) if granted_routes is not None else None
    routes: list[dict] = []
    for policy in PortalRoutePolicy.objects.all().order_by("section", "path"):
        if policy.route_name in SYSTEM_ROUTE_NAMES:
            continue
        if allowed is not None and policy.route_name not in allowed:
            continue
        permissions_any, permissions_all = effective_permissions_for_policy(policy)
        if _role_can_access_route_permissions(role_permissions, permissions_any, permissions_all):
            routes.append(
                {
                    "route_name": policy.route_name,
                    "path": policy.path,
                    "label": policy.label,
                    "section": policy.section,
                }
            )
    return routes


def serialize_role_definition(row: PortalRoleDefinition) -> dict:
    from apps.access.services.rbac_admin import is_builtin_role

    permissions = sorted(row.permissions or [])
    role_permissions = set(permissions)
    granted = list(row.granted_routes) if row.granted_routes is not None else None
    routes = routes_accessible_by_permissions(role_permissions, granted_routes=granted)
    area = (row.area or "").strip() or ROLE_AREAS.get(row.role, "outros")
    return {
        "role": row.role,
        "label": row.label,
        "area": area,
        "area_label": AREA_LABELS.get(area, "Outros"),
        "permissions": permissions,
        "permission_count": len(permissions),
        "default_scope": row.default_scope,
        "scope_label": SCOPE_LABELS.get(row.default_scope, row.default_scope),
        "is_editable": row.is_editable and row.role != ROLE_ADM_PORTAL,
        "is_builtin": is_builtin_role(row.role),
        "can_delete": row.is_editable and not is_builtin_role(row.role),
        "can_rename": row.is_editable and not is_builtin_role(row.role),
        "code_defaults": code_defaults_for_role(row.role),
        "is_customized": is_role_customized(row),
        "granted_routes": granted,
        "routes_with_access": routes,
        "routes_with_access_count": len(routes),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def compute_allowed_routes_for_roles(roles: set[str]) -> list[str] | None:
    """Rotas efetivamente liberadas.

    None = nenhum perfil restringe por rota (comportamento legado, só permissões).
    Lista = união das rotas que cada perfil concede (perm + allowlist).
    """
    from apps.access.roles import permissions_for_role

    if not roles:
        return []

    role_grants: dict[str, list[str] | None] = {}
    any_unrestricted = False
    for role in roles:
        row = stored_role_definition(role)
        grants = None if row is None else row.granted_routes
        if grants is None:
            any_unrestricted = True
            role_grants[role] = None
        else:
            role_grants[role] = list(grants)

    if any_unrestricted and all(g is None for g in role_grants.values()):
        return None

    allowed: set[str] = set(SYSTEM_ROUTE_NAMES)
    for policy in PortalRoutePolicy.objects.all():
        name = policy.route_name
        if name in SYSTEM_ROUTE_NAMES:
            continue
        permissions_any, permissions_all = effective_permissions_for_policy(policy)
        for role in roles:
            perms = permissions_for_role(role)
            if not _role_can_access_route_permissions(perms, permissions_any, permissions_all):
                continue
            grants = role_grants.get(role)
            if grants is None or name in grants:
                allowed.add(name)
                break
    return sorted(allowed)


def build_permissions_catalog() -> list[dict]:
    grouped: dict[str, list[str]] = {}
    for code in sorted(ALL_PERMISSIONS):
        module = code.split(".", 1)[0]
        grouped.setdefault(module, []).append(code)
    return [
        {"module": module, "permissions": permissions}
        for module, permissions in sorted(grouped.items())
    ]


def build_routes_catalog() -> list[dict]:
    routes: list[dict] = []
    for policy in PortalRoutePolicy.objects.all().order_by("section", "path"):
        permissions_any, permissions_all = effective_permissions_for_policy(policy)
        routes.append(
            {
                "route_name": policy.route_name,
                "path": policy.path,
                "label": policy.label,
                "section": policy.section,
                "permissions_any": permissions_any,
                "permissions_all": permissions_all,
                "is_system": policy.route_name in SYSTEM_ROUTE_NAMES,
            }
        )
    return routes


def seed_defaults_for_role(role: str) -> dict:
    from apps.access.roles import ROLE_DEFINITIONS

    definition = ROLE_DEFINITIONS[role]
    return {
        "role": role,
        "label": ROLE_LABELS.get(role, role),
        "area": ROLE_AREAS.get(role, "outros"),
        "permissions": sorted(definition["permissions"]),
        "default_scope": definition["default_scope"],
        "is_editable": role != ROLE_ADM_PORTAL,
    }


def all_role_ids() -> tuple[str, ...]:
    return ALL_ROLES
