# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db.models import Max, Min

from apps.monitor_eventos.models import MonitorEventoRecord, MonitorEventoSyncLog


def get_sync_status() -> dict:
    bounds = MonitorEventoRecord.objects.aggregate(
        min_date=Min("data_evento"),
        max_date=Max("data_evento"),
    )
    last_log = (
        MonitorEventoSyncLog.objects.filter(success=True)
        .order_by("-finished_at")
        .first()
    )
    return {
        "row_count": MonitorEventoRecord.objects.count(),
        "min_data_evento": bounds["min_date"].isoformat() if bounds["min_date"] else None,
        "max_data_evento": bounds["max_date"].isoformat() if bounds["max_date"] else None,
        "last_sync_at": last_log.finished_at.isoformat() if last_log and last_log.finished_at else None,
        "last_sync_success": bool(last_log),
        "last_sync_message": last_log.message if last_log else "",
        "last_sync_row_count": last_log.row_count if last_log else None,
    }
