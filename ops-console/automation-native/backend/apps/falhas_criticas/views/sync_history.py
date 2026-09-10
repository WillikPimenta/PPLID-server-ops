# -*- coding: utf-8 -*-
from pathlib import Path

from rest_framework.response import Response
from rest_framework.views import APIView

from apps.falhas_criticas.models import SyncAuditLog
from apps.falhas_criticas.permissions import CanSyncExcel, IsAuthenticatedPortal

HISTORY_LIMIT = 50


def _serialize_sync_log(log: SyncAuditLog) -> dict:
    path_raw = (log.path or "").strip()
    filename = Path(path_raw).name if path_raw else ""
    username = ""
    if log.user_id:
        username = log.user.username or ""
    return {
        "id": log.pk,
        "started_at": log.started_at,
        "finished_at": log.finished_at,
        "username": username,
        "success": log.success,
        "message": log.message,
        "duration_seconds": log.duration_seconds,
        "filename": filename,
        "trigger_source": log.trigger_source,
    }


class SyncImportListView(APIView):
    permission_classes = [IsAuthenticatedPortal, CanSyncExcel]

    def get(self, request):
        logs = SyncAuditLog.objects.select_related("user").order_by("-started_at")[:HISTORY_LIMIT]
        return Response([_serialize_sync_log(log) for log in logs])
