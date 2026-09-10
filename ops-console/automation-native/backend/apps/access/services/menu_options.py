"""Helpers das opções de menu configuráveis pelo RBAC."""

from __future__ import annotations

from apps.access.models import PortalMenuOption, PortalRoutePolicy
from apps.access.services.route_policies import effective_permissions_for_policy


def _would_create_menu_cycle(menu_key: str, parent_key: str) -> bool:
    parent_map = {
        row.menu_key: (row.parent_menu_key or "").strip()
        for row in PortalMenuOption.objects.all().only("menu_key", "parent_menu_key")
    }
    seen = {menu_key}
    current = parent_key
    while current:
        if current in seen:
            return True
        seen.add(current)
        current = parent_map.get(current, "")
    return False


def _navigable_menu_path(path: str) -> str:
    parts = [
        segment
        for segment in path.split("/")
        if not (segment.startswith(":") and segment.endswith("?"))
    ]
    return "/".join(parts) or "/"


def serialize_menu_option(option: PortalMenuOption) -> dict:
    policies = {
        policy.route_name: policy
        for policy in PortalRoutePolicy.objects.filter(
            route_name__in=list(option.route_names or [])
        )
    }
    routes = []
    for route_name in option.route_names or []:
        policy = policies.get(route_name)
        if policy is None:
            continue
        permissions_any, permissions_all = effective_permissions_for_policy(policy)
        routes.append(
            {
                "route_name": policy.route_name,
                "path": _navigable_menu_path(policy.path),
                "label": policy.label,
                "permissions_any": permissions_any,
                "permissions_all": permissions_all,
                "is_active": policy.is_active,
            }
        )
    return {
        "menu_key": option.menu_key,
        "label": option.label,
        "description": option.description,
        "section": option.section,
        "parent_menu_key": (option.parent_menu_key or "").strip() or None,
        "sort_order": option.sort_order,
        "route_names": list(option.route_names or []),
        "routes": routes,
        "is_active": option.is_active,
        "is_builtin": option.is_builtin,
        "is_editable": option.is_editable,
        "can_delete": bool(option.is_editable) and not option.is_builtin,
        "updated_at": option.updated_at.isoformat() if option.updated_at else None,
    }
