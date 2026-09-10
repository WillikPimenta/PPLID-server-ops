"""Helpers para políticas de rota RBAC."""

from __future__ import annotations

from apps.access.models import PortalRoutePolicy
from apps.access.portal_route_registry import PORTAL_ROUTE_BY_NAME, PortalRouteDefinition, SYSTEM_ROUTE_NAMES
from apps.access.registry import ALL_PERMISSIONS
from apps.access.roles import permissions_for_role


ROLE_LABELS: dict[str, str] = {
    "plan_gerencia": "Plan. Gerência",
    "plan_analista": "Plan. Analista",
    "plan_assistente": "Plan. Assistente",
    "op_gerencia": "Op. Gerência",
    "op_lider": "Op. Líder",
    "op_agente": "Op. Agente",
    "proc_usuario": "Processos",
    "qual_usuario": "Qualidade (legado)",
    "qual_auditoria_fraud": "Qual. Auditoria Fraud",
    "qual_auditoria_compliance": "Qual. Auditoria Compliance",
    "qual_contestacao_fraud": "Qual. Contestação Fraud",
    "qual_contestacao_compliance": "Qual. Contestação Compliance",
    "qual_capacitacao": "Qual. Capacitação",
    "qual_gerencia": "Qual. Gerência",
    "adm_portal": "Admin Portal",
}

# A página que controla manutenção precisa permanecer disponível para evitar
# que a própria configuração seja bloqueada sem caminho de retorno.
MAINTENANCE_CONTROL_ROUTE_NAMES = {"configuracoes-rbac-perfis"}


def can_toggle_route_active(route_name: str) -> bool:
    return route_name not in SYSTEM_ROUTE_NAMES | MAINTENANCE_CONTROL_ROUTE_NAMES


def _role_can_access_route(
    role: str,
    permissions_any: list[str],
    permissions_all: list[str],
) -> bool:
    perms = permissions_for_role(role)
    if permissions_all:
        if not all(code in perms for code in permissions_all):
            return False
    if permissions_any:
        return any(code in perms for code in permissions_any)
    return False


def roles_with_access_for_policy(
    permissions_any: list[str],
    permissions_all: list[str],
) -> list[str]:
    from apps.access.services.rbac_admin import iterable_role_ids_for_access_matrix

    return [
        role
        for role in iterable_role_ids_for_access_matrix()
        if _role_can_access_route(role, permissions_any, permissions_all)
    ]


def code_defaults_for_route(route_name: str) -> dict[str, list[str]]:
    definition = PORTAL_ROUTE_BY_NAME.get(route_name)
    if not definition:
        return {"permissions_any": [], "permissions_all": []}
    return {
        "permissions_any": list(definition.permissions_any),
        "permissions_all": list(definition.permissions_all),
    }


def is_customized(policy: PortalRoutePolicy) -> bool:
    defaults = code_defaults_for_route(policy.route_name)
    return (
        list(policy.permissions_any or []) != defaults["permissions_any"]
        or list(policy.permissions_all or []) != defaults["permissions_all"]
        or not policy.is_active
    )


def effective_permissions_for_policy(policy: PortalRoutePolicy) -> tuple[list[str], list[str]]:
    permissions_any = list(policy.permissions_any or [])
    permissions_all = list(policy.permissions_all or [])
    registry = PORTAL_ROUTE_BY_NAME.get(policy.route_name)
    if not permissions_any and registry:
        permissions_any = list(registry.permissions_any)
    if not permissions_all and registry:
        permissions_all = list(registry.permissions_all)
    return permissions_any, permissions_all


def serialize_route_policy(policy: PortalRoutePolicy, *, include_roles: bool = True) -> dict:
    permissions_any, permissions_all = effective_permissions_for_policy(policy)
    is_system = policy.route_name in SYSTEM_ROUTE_NAMES
    payload = {
        "route_name": policy.route_name,
        "path": policy.path,
        "label": policy.label,
        "section": policy.section,
        "permissions_any": permissions_any,
        "permissions_all": permissions_all,
        "requires_auth": policy.requires_auth,
        "is_active": policy.is_active,
        "can_toggle_active": can_toggle_route_active(policy.route_name),
        "is_editable": bool(policy.is_editable) and not is_system,
        "is_system": is_system,
        "can_delete": bool(policy.is_editable) and not is_system,
        "code_defaults": code_defaults_for_route(policy.route_name),
        "is_customized": is_customized(policy),
        "updated_at": policy.updated_at.isoformat() if policy.updated_at else None,
    }
    if include_roles:
        payload["roles_with_access"] = roles_with_access_for_policy(
            permissions_any, permissions_all
        )
    return payload


def build_permissions_catalog() -> list[dict]:
    grouped: dict[str, list[str]] = {}
    for code in sorted(ALL_PERMISSIONS):
        module = code.split(".", 1)[0]
        grouped.setdefault(module, []).append(code)
    return [
        {"module": module, "permissions": permissions}
        for module, permissions in sorted(grouped.items())
    ]


def build_roles_catalog() -> list[dict]:
    from apps.access.models import PortalRoleDefinition
    from apps.access.services.rbac_admin import iterable_role_ids_for_access_matrix

    labels = dict(ROLE_LABELS)
    for row in PortalRoleDefinition.objects.all().only("role", "label"):
        labels[row.role] = row.label
    return [
        {"id": role, "label": labels.get(role, role)}
        for role in iterable_role_ids_for_access_matrix()
    ]


def build_role_permissions_catalog() -> dict[str, list[str]]:
    from apps.access.services.rbac_admin import iterable_role_ids_for_access_matrix

    return {
        role: sorted(permissions_for_role(role))
        for role in iterable_role_ids_for_access_matrix()
    }


def definition_to_defaults(definition: PortalRouteDefinition) -> dict:
    return {
        "route_name": definition.route_name,
        "path": definition.path,
        "label": definition.label,
        "section": definition.section,
        "permissions_any": list(definition.permissions_any),
        "permissions_all": list(definition.permissions_all),
        "requires_auth": definition.requires_auth,
        "is_editable": definition.is_editable,
    }
