# -*- coding: utf-8 -*-
from rest_framework import permissions
from rest_framework.permissions import IsAuthenticated

from apps.access import registry as R
from apps.access.permissions import HasAnyPortalPermission, HasPortalPermission


class IsAuthenticatedPortal(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class CanViewSuporteClaro(HasPortalPermission):
    permission_code = R.PROCESSOS_SUPORTE_CLARO_VIEW


class CanCreateSuporteClaro(HasPortalPermission):
    permission_code = R.PROCESSOS_SUPORTE_CLARO_CREATE


class CanChangeStatusSuporteClaro(HasPortalPermission):
    permission_code = R.PROCESSOS_SUPORTE_CLARO_CHANGE_STATUS


class SuporteClaroPermissionMixin:
    """Permissões por método HTTP para views de Suporte Claro."""

    def get_permissions(self):
        method = (self.request.method or "GET").upper()
        if method in {"GET", "HEAD", "OPTIONS"}:
            return [IsAuthenticated(), CanViewSuporteClaro()]
        if method == "POST":
            return [IsAuthenticated(), CanCreateSuporteClaro()]
        return [IsAuthenticated(), CanChangeStatusSuporteClaro()]
