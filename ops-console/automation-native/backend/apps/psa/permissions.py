# -*- coding: utf-8 -*-
from rest_framework import permissions

from apps.access import registry as R
from apps.access.permissions import HasPortalPermission
from apps.access.resolve import user_has_permission


class IsStaffOrCanSync(HasPortalPermission):
    """Console Ops — perfil adm.console_ops ou planejamento.console_ops.view."""

    permission_code = R.ADM_CONSOLE_OPS

    def has_permission(self, request, view):
        if super().has_permission(request, view):
            return True
        alt = HasPortalPermission()
        alt.permission_code = R.PLANEJAMENTO_CONSOLE_OPS_VIEW
        return alt.has_permission(request, view)


class IsCyberPsaAccess(HasPortalPermission):
    permission_code = R.ADM_PSA_CYBER


def user_has_psa_access(user) -> bool:
    return user_has_permission(user, R.ADM_CONSOLE_OPS) or user_has_permission(
        user, R.PLANEJAMENTO_CONSOLE_OPS_VIEW
    )


def user_has_portal_ops_access(user) -> bool:
    return user_has_psa_access(user)


def user_has_cyber_psa_access(user) -> bool:
    return user_has_permission(user, R.ADM_PSA_CYBER)
