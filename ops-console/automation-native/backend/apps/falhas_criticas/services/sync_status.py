# -*- coding: utf-8 -*-
from pathlib import Path

from apps.falhas_criticas.models import Failure, SyncAuditLog
from apps.falhas_criticas.services.data_bounds import get_portal_data_bounds
from apps.falhas_criticas.services.excel_path import get_excel_source_path
from apps.falhas_criticas.services.sync_runner import _finalize_stale_sync_logs


def _default_sync_error_message(log) -> str:
    if log.trigger_source == SyncAuditLog.TRIGGER_SYSTEM:
        return "Verifique se o Excel está fechado."
    return "Feche o Excel e tente novamente pelo botão Sincronizar Excel."


def last_sync_info():
    _finalize_stale_sync_logs()
    info = {
        "last_sync": None,
        "last_sync_trigger": None,
        "last_auto_sync": None,
        "last_sync_failed": None,
        "last_sync_failed_trigger": None,
        "sync_error_message": None,
        "excel_source_name": None,
        "data_max_date": None,
        "total_failures": 0,
    }
    last_ok = (
        SyncAuditLog.objects.filter(success=True, finished_at__isnull=False)
        .order_by("-finished_at")
        .first()
    )
    if last_ok:
        info["last_sync"] = last_ok.finished_at or last_ok.started_at
        info["last_sync_trigger"] = last_ok.trigger_source
    last_auto = (
        SyncAuditLog.objects.filter(
            success=True,
            trigger_source=SyncAuditLog.TRIGGER_SYSTEM,
            finished_at__isnull=False,
        )
        .order_by("-finished_at")
        .first()
    )
    if last_auto:
        info["last_auto_sync"] = last_auto.finished_at or last_auto.started_at
    last_fail = (
        SyncAuditLog.objects.filter(success=False, finished_at__isnull=False)
        .order_by("-finished_at")
        .first()
    )
    if last_fail and last_ok:
        ok_time = last_ok.finished_at or last_ok.started_at
        fail_time = last_fail.finished_at or last_fail.started_at
        if fail_time > ok_time:
            info["last_sync_failed"] = fail_time
            info["last_sync_failed_trigger"] = last_fail.trigger_source
            msg = (last_fail.message or "").strip()
            info["sync_error_message"] = msg or _default_sync_error_message(last_fail)
    try:
        info["excel_source_name"] = Path(get_excel_source_path()).name
    except Exception:
        pass
    try:
        bounds = get_portal_data_bounds()
        info["data_max_date"] = bounds.get("max_date")
        info["total_failures"] = Failure.objects.count()
    except Exception:
        pass
    return info
