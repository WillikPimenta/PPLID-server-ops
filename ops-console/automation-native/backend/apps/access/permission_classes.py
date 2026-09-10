"""Factory de permission classes DRF ligadas ao registry RBAC."""

from __future__ import annotations

from apps.access.constants import ROLE_ADM_PORTAL
from apps.access.permissions import HasAnyPortalPermission, HasPortalPermission, access_enforcement_enabled
from apps.access.resolve import resolve_user_access
from apps.access.services.impersonation import resolve_effective_user
from rest_framework.permissions import BasePermission


def _permission_user(request):
    try:
        return resolve_effective_user(request)
    except Exception:
        return request.user


class HasAdmPortalRole(BasePermission):
    """Exige perfil adm_portal (ou superuser bypass)."""

    def has_permission(self, request, view) -> bool:
        user = _permission_user(request)
        if not user or not getattr(user, "is_authenticated", False):
            return not access_enforcement_enabled()

        access = resolve_user_access(user)
        roles = set(access.get("roles") or [])
        allowed = bool(access.get("bypass")) or ROLE_ADM_PORTAL in roles
        if not allowed and access_enforcement_enabled():
            return False
        return True


AdmPortalPerm = HasAdmPortalRole


def portal_perm(code: str):
    """Retorna classe DRF que exige uma permissão do registry."""

    class _Permission(HasPortalPermission):
        permission_code = code

    _Permission.__name__ = f"PortalPerm_{code.replace('.', '_')}"
    return _Permission


def portal_perm_any(*codes: str):
    """Retorna classe DRF que exige ao menos uma permissão."""

    class _Permission(HasAnyPortalPermission):
        permission_codes = tuple(codes)

    _Permission.__name__ = "PortalPermAny_" + "_".join(
        code.replace(".", "_") for code in codes
    )
    return _Permission
