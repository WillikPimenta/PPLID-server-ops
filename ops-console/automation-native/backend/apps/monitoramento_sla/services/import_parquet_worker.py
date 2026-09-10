# -*- coding: utf-8 -*-
"""Worker assíncrono para apply de import Parquet (monitoramento SLA)."""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections
from django.utils import timezone

from apps.monitoramento_sla.models import SlaUtilSyncRun
from apps.monitoramento_sla.services.import_consolidado_parquet import (
    PARQUET_IMPORT_KIND,
    import_consolidado_parquet_months,
)
from apps.monitoramento_sla.services.parquet_upload_staging import get_upload_record

log = logging.getLogger(__name__)


def _import_sync_enabled() -> bool:
    return bool(getattr(settings, "MONITORAMENTO_SLA_PARQUET_IMPORT_SYNC", False))


def _stale_hours() -> int:
    return int(getattr(settings, "MONITORAMENTO_SLA_PARQUET_IMPORT_STALE_HOURS", 6))


def recover_stale_parquet_imports() -> int:
    """Marca imports parquet órfãos (running há muito tempo) como erro."""
    cutoff = timezone.now() - timedelta(hours=_stale_hours())
    stale = SlaUtilSyncRun.objects.filter(
        status=SlaUtilSyncRun.STATUS_RUNNING,
        started_at__lt=cutoff,
        metrics__kind=PARQUET_IMPORT_KIND,
    )
    count = 0
    for run in stale.iterator():
        metrics = dict(run.metrics or {})
        metrics["phase"] = "error"
        metrics["error"] = "Import interrompido (servidor reiniciado ou timeout)."
        run.status = SlaUtilSyncRun.STATUS_ERROR
        run.message = "Import interrompido."
        run.finished_at = timezone.now()
        run.metrics = metrics
        run.save(update_fields=["status", "message", "finished_at", "metrics"])
        count += 1
    return count


def _run_parquet_apply(
    run_id: int,
    upload_id: str,
    months: list[str],
    user_id: int | None,
    filename: str,
) -> None:
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first() if user_id else None
    run = SlaUtilSyncRun.objects.filter(pk=run_id).first()
    if run is None:
        return

    try:
        record = get_upload_record(upload_id, user=user)
        import_consolidado_parquet_months(
            record.path,
            months,
            user=user,
            filename=filename or record.original_name,
            run=run,
            upload_id=upload_id,
        )
    except Exception as exc:
        log.exception("parquet apply worker failed run_id=%s", run_id)
        run.refresh_from_db()
        if run.status == SlaUtilSyncRun.STATUS_RUNNING:
            metrics = dict(run.metrics or {})
            metrics["kind"] = PARQUET_IMPORT_KIND
            metrics["phase"] = "error"
            metrics["error"] = str(exc)[:500]
            run.status = SlaUtilSyncRun.STATUS_ERROR
            run.message = str(exc)[:500]
            run.finished_at = timezone.now()
            run.metrics = metrics
            run.save(update_fields=["status", "message", "finished_at", "metrics"])


def _run_parquet_apply_threadsafe(
    run_id: int,
    upload_id: str,
    months: list[str],
    user_id: int | None,
    filename: str,
) -> None:
    close_old_connections()
    try:
        recover_stale_parquet_imports()
        _run_parquet_apply(run_id, upload_id, months, user_id, filename)
    finally:
        close_old_connections()


def schedule_parquet_apply(
    run_id: int,
    upload_id: str,
    months: list[str],
    user_id: int | None,
    filename: str,
) -> None:
    if _import_sync_enabled():
        recover_stale_parquet_imports()
        _run_parquet_apply(run_id, upload_id, months, user_id, filename)
        return
    threading.Thread(
        target=_run_parquet_apply_threadsafe,
        args=(run_id, upload_id, months, user_id, filename),
        daemon=True,
    ).start()
