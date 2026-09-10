"""Classes de permissão DRF para RBAC do portal."""

from __future__ import annotations

import logging

from django.conf import settings
from rest_framework.permissions import BasePermission

from apps.access.resolve import user_has_any_permission, user_has_permission
from apps.access.services.impersonation import resolve_effective_user

logger = logging.getLogger("apps.access.permissions")


def access_enforcement_enabled() -> bool:
    return getattr(settings, "ACCESS_ENFORCEMENT", False)


def _permission_user(request):
    """Usuário efetivo (impersonado) para checagens RBAC do portal."""
    try:
        return resolve_effective_user(request)
    except Exception:
        return request.user


class HasPortalPermission(BasePermission):
    """
    Exige uma permissão do registry.

    Shadow mode (ACCESS_ENFORCEMENT=false): negações são apenas logadas; acesso permitido.
    Produção (ACCESS_ENFORCEMENT=true): retorna 403 quando o usuário não tem a permissão.
    """

    permission_code: str = ""

    def get_permission_code(self, view) -> str:
        return getattr(view, "portal_permission", None) or self.permission_code

    def has_permission(self, request, view) -> bool:
        user = _permission_user(request)
        if not user or not getattr(user, "is_authenticated", False):
            if access_enforcement_enabled():
                return False
            return True

        code = self.get_permission_code(view)
        if not code:
            return True

        allowed = user_has_permission(user, code)
        if not allowed and access_enforcement_enabled():
            logger.warning(
                "Acesso negado (403 shadow=%s) user=%s permission=%s path=%s",
                not access_enforcement_enabled(),
                getattr(user, "username", "?"),
                code,
                getattr(request, "path", "?"),
            )
            return False
        if not allowed:
            logger.info(
                "Shadow deny user=%s permission=%s path=%s",
                getattr(user, "username", "?"),
                code,
                getattr(request, "path", "?"),
            )
            return True
        return True


class HasAnyPortalPermission(BasePermission):
    """Exige ao menos uma permissão da lista."""

    permission_codes: tuple[str, ...] = ()

    def get_permission_codes(self, view) -> tuple[str, ...]:
        codes = getattr(view, "portal_permissions", None)
        if codes:
            return tuple(codes)
        return self.permission_codes

    def has_permission(self, request, view) -> bool:
        user = _permission_user(request)
        if not user or not getattr(user, "is_authenticated", False):
            if access_enforcement_enabled():
                return False
            return True

        codes = self.get_permission_codes(view)
        if not codes:
            return True

        allowed = user_has_any_permission(user, *codes)
        if not allowed and access_enforcement_enabled():
            logger.warning(
                "Acesso negado user=%s permissions=%s path=%s",
                getattr(user, "username", "?"),
                codes,
                getattr(request, "path", "?"),
            )
            return False
        if not allowed:
            logger.info(
                "Shadow deny user=%s permissions=%s path=%s",
                getattr(user, "username", "?"),
                codes,
                getattr(request, "path", "?"),
            )
            return True
        return True
