# -*- coding: utf-8 -*-
from django.conf import settings as dj_settings
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.falhas_criticas.permissions import IsAuthenticatedPortal
from apps.falhas_criticas.scoping import get_user_scope
from apps.falhas_criticas.services.sync_status import last_sync_info
from apps.falhas_criticas.services.team_hierarchy import get_team_context
from apps.falhas_criticas.services.user_display import resolve_user_display_name


class MeView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def get(self, request):
        scope = get_user_scope(request.user)
        sync_info = last_sync_info()
        display_name = resolve_user_display_name(request.user)
        team_ctx = get_team_context(request.user)
        return Response({
            "username": scope["username"],
            "display_name": display_name,
            "email": (request.user.email or "").strip(),
            "scope": scope["scope"],
            "localidade_forcada": scope["localidade_forcada"],
            "matricula_forcada": scope.get("matricula_forcada"),
            "can_sync": scope["can_sync"],
            "can_export": scope["can_sync"] or scope["scope"] == "global",
            "can_create_ticket": True,
            "can_choose_localidade": scope["can_choose_localidade"],
            "is_own_scope": scope["scope"] == "own",
            "is_staff": request.user.is_staff,
            "last_sync": sync_info["last_sync"],
            "last_sync_trigger": sync_info["last_sync_trigger"],
            "last_auto_sync": sync_info["last_auto_sync"],
            "last_sync_failed": sync_info["last_sync_failed"],
            "last_sync_failed_trigger": sync_info["last_sync_failed_trigger"],
            "sync_error_message": sync_info["sync_error_message"],
            "excel_source_name": sync_info["excel_source_name"],
            "data_max_date": sync_info["data_max_date"],
            "total_failures": sync_info["total_failures"],
            "team": team_ctx,
            "jira_collector_enabled": getattr(dj_settings, "JIRA_COLLECTOR_ENABLED", False),
        })
