"""Resolução de acesso efetivo do usuário."""

from __future__ import annotations

from pathlib import Path

import yaml
from django.conf import settings

from apps.access.constants import (
    ALL_ROLES,
    DEFAULT_RBAC_DATA_DIR,
    LEGACY_GROUP_MAP_FILENAME,
    MANUAL_ROLES_FILENAME,
    PORTAL_USERS_FILENAME,
    ROLE_ADM_PORTAL,
    SCOPE_GLOBAL,
    parse_role_from_group,
    role_group_name,
)
from apps.access.registry import ALL_PERMISSIONS
from apps.access.roles import default_scope_for_role, permissions_for_role, validate_role_definitions
from apps.access.services.role_definitions import compute_allowed_routes_for_roles

_rbac_data_validated = False


def _rbac_data_dir() -> Path:
    base = Path(settings.BASE_DIR)
    return base / DEFAULT_RBAC_DATA_DIR


def _load_yaml(filename: str) -> dict:
    path = _rbac_data_dir() / filename
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_manual_role_assignments() -> dict[str, list[str]]:
    data = _load_yaml(MANUAL_ROLES_FILENAME)
    result: dict[str, list[str]] = {}
    for role in ALL_ROLES:
        entries = data.get(role) or []
        result[role] = [str(x).strip().lower() for x in entries if str(x).strip()]
    return result


def load_legacy_group_map() -> dict[str, list[str]]:
    data = _load_yaml(LEGACY_GROUP_MAP_FILENAME)
    return {str(k): list(v or []) for k, v in data.items()}


def load_portal_users_config() -> dict:
    """Configuração de provisionamento User a partir de portal_users.yaml."""
    data = _load_yaml(PORTAL_USERS_FILENAME)
    always = [str(x).strip().lower() for x in (data.get("always_provision") or []) if str(x).strip()]
    always_active = [str(x).strip().lower() for x in (data.get("always_active") or []) if str(x).strip()]
    preserve = [str(x).strip().lower() for x in (data.get("preserve_usernames") or []) if str(x).strip()]
    return {
        "active_only": bool(data.get("active_only", True)),
        "always_provision": always,
        "always_active": always_active,
        "preserve_usernames": preserve,
        "deactivate_orphans": bool(data.get("deactivate_orphans", False)),
    }


def roles_from_manual_assignments(lan_id: str) -> set[str]:
    if not lan_id:
        return set()
    normalized = lan_id.strip().lower()
    roles: set[str] = set()
    for role, lan_ids in load_manual_role_assignments().items():
        if normalized in lan_ids:
            roles.add(role)
    return roles


def roles_from_django_groups(user) -> set[str]:
    from apps.access.services.rbac_admin import known_role_ids

    known = known_role_ids()
    roles: set[str] = set()
    for group_name in user.groups.values_list("name", flat=True):
        role = parse_role_from_group(group_name)
        if role and role in known:
            roles.add(role)
    return roles


def _ensure_validated() -> None:
    global _rbac_data_validated
    if not _rbac_data_validated:
        validate_role_definitions()
        _rbac_data_validated = True


def resolve_user_access(user) -> dict:
    """
    Agrega perfis, permissões efetivas e escopos a partir dos grupos Django role:*.

    Única exceção: is_superuser recebe bypass com todas as permissões.
    Atribuição de perfis é feita pela UI (Configurações → Usuários) ou Django Admin.
    """
    _ensure_validated()

    if not user or not getattr(user, "is_authenticated", False):
        return {
            "roles": [],
            "permissions": [],
            "scopes": {},
            "allowed_routes": [],
            "bypass": False,
        }

    if getattr(user, "is_superuser", False):
        return {
            "roles": list(ALL_ROLES) + [ROLE_ADM_PORTAL],
            "permissions": sorted(ALL_PERMISSIONS),
            "scopes": {"default": SCOPE_GLOBAL},
            "allowed_routes": None,
            "bypass": True,
        }

    roles = roles_from_django_groups(user)

    permissions: set[str] = set()
    scopes: dict[str, str] = {"default": SCOPE_GLOBAL}

    for role in roles:
        permissions |= permissions_for_role(role)
        role_scope = default_scope_for_role(role)
        if role_scope != SCOPE_GLOBAL:
            scopes.setdefault("operacao", role_scope)
            scopes["default"] = role_scope if len(roles) == 1 else scopes.get("default", SCOPE_GLOBAL)

    return {
        "roles": sorted(roles),
        "permissions": sorted(permissions),
        "scopes": scopes,
        "allowed_routes": compute_allowed_routes_for_roles(roles),
        "bypass": False,
    }


def user_has_permission(user, permission: str) -> bool:
    access = resolve_user_access(user)
    if access.get("bypass"):
        return True
    return permission in access.get("permissions", [])


def user_has_any_permission(user, *permissions: str) -> bool:
    access = resolve_user_access(user)
    if access.get("bypass"):
        return True
    effective = set(access.get("permissions", []))
    return any(p in effective for p in permissions)


def ensure_role_groups_exist() -> dict[str, int]:
    from django.contrib.auth.models import Group

    from apps.access.services.rbac_admin import known_role_ids

    created = 0
    roles = sorted(known_role_ids())
    for role in roles:
        _, was_created = Group.objects.get_or_create(name=role_group_name(role))
        if was_created:
            created += 1
    return {"created": created, "total": len(roles)}
