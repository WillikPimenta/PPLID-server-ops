from rest_framework import permissions

from apps.access import registry as R
from apps.access.permissions import HasPortalPermission
from apps.access.resolve import user_has_permission


class IsAuthenticatedPortal(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class CanViewProductivity(HasPortalPermission):
    permission_code = R.INDICADORES_PRODUTIVIDADE_VIEW


class CanSyncProductivity(HasPortalPermission):
    permission_code = R.INDICADORES_PRODUTIVIDADE_SYNC


def user_can_view_productivity(user) -> bool:
    return user_has_permission(user, R.INDICADORES_PRODUTIVIDADE_VIEW)
