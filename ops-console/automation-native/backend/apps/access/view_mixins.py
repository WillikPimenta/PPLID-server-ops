"""Mixins para ViewSets com permissões RBAC por action."""

from __future__ import annotations

from rest_framework.permissions import IsAuthenticated

from apps.access.permission_classes import portal_perm, portal_perm_any
from apps.access import registry as R


class HeadcountPortalPermissionMixin:
    """Permissões de headcount por action do AgentViewSet."""

    def get_permissions(self):
        view_perm = portal_perm(R.PLANEJAMENTO_HEADCOUNT_VIEW)()
        manage_perm = portal_perm(R.PLANEJAMENTO_HEADCOUNT_MANAGE)()
        auth = IsAuthenticated()
        action_map = {
            "list": [auth, view_perm],
            "retrieve": [auth, view_perm],
            "column_filter_options": [auth, view_perm],
            "create": [auth, manage_perm],
            "update": [auth, manage_perm],
            "partial_update": [auth, manage_perm],
            "bulk_patch": [auth, manage_perm],
            "apply_cycle_change": [auth, manage_perm],
            "destroy": [auth, manage_perm],
        }
        return action_map.get(self.action, [auth, view_perm])


class AgentHistoryPortalPermissionMixin:
    def get_permissions(self):
        view_perm = portal_perm(R.PLANEJAMENTO_HEADCOUNT_VIEW)()
        manage_perm = portal_perm(R.PLANEJAMENTO_HEADCOUNT_MANAGE)()
        auth = IsAuthenticated()
        if self.action in {"list", "retrieve"}:
            return [auth, view_perm]
        return [auth, manage_perm]


class NewsPortalPermissionMixin:
    def get_permissions(self):
        view_perm = portal_perm(R.COMUNICACAO_NOTICIAS_VIEW)()
        manage_perm = portal_perm(R.COMUNICACAO_NOTICIAS_MANAGE)()
        auth = IsAuthenticated()
        if self.action in {"list", "retrieve", "like", "acknowledge"}:
            return [auth, view_perm]
        return [auth, manage_perm]
