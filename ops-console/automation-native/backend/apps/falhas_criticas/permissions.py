# -*- coding: utf-8 -*-
from rest_framework import permissions

from apps.access import registry as R
from apps.access.constants import (
    ROLE_ADM_PORTAL,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_GERENCIA,
)
from apps.access.permissions import HasPortalPermission, _permission_user
from apps.access.resolve import resolve_user_access, user_has_permission
from apps.falhas_criticas.scoping import get_user_scope

BASE_AUDITORIA_ROLES = frozenset({
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_GERENCIA,
    ROLE_ADM_PORTAL,
})


class IsAuthenticatedPortal(permissions.BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class HasQualFalhasView(HasPortalPermission):
    permission_code = R.QUAL_FALHAS_VIEW


class HasDataScope(HasPortalPermission):
    """Acesso ao painel de Falhas Críticas via registry."""

    permission_code = R.QUAL_FALHAS_VIEW


class IsGlobalOnly(permissions.BasePermission):
    """
    Comparativo BSB×SC e relatório executivo nacional.

    Só Lideranca_Global / superuser (get_user_scope == global).
    Sempre enforced — não usa shadow RBAC, para não vazar a outra BU.
    """

    def has_permission(self, request, view):
        user = _permission_user(request)
        if not user or not getattr(user, "is_authenticated", False):
            return False
        return get_user_scope(user).get("scope") == "global"


class IsBaseAuditoriaRole(permissions.BasePermission):
    """
    Base e Auditoria — Planejamento (analista/gerência) e ADM.
    Sempre enforced (não shadow).
    """

    def has_permission(self, request, view):
        user = _permission_user(request)
        if not user or not getattr(user, "is_authenticated", False):
            return False
        access = resolve_user_access(user)
        if access.get("bypass"):
            return True
        roles = set(access.get("roles") or [])
        return bool(roles & BASE_AUDITORIA_ROLES)


class CanSyncExcel(HasPortalPermission):
    """Sincronizar/importar planilha — somente ADM."""

    permission_code = R.ADM_FALHAS_SYNC


class CanExportExecutive(HasPortalPermission):
    permission_code = R.QUAL_FALHAS_EXPORT_EXECUTIVE


def user_can_view_falhas(user) -> bool:
    return user_has_permission(user, R.QUAL_FALHAS_VIEW)


def user_can_view_comparativo(user) -> bool:
    return get_user_scope(user).get("scope") == "global"


def user_can_view_base_auditoria(user) -> bool:
    access = resolve_user_access(user)
    if access.get("bypass"):
        return True
    return bool(set(access.get("roles") or []) & BASE_AUDITORIA_ROLES)
