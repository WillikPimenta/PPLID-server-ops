# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import timedelta
from typing import Tuple

from django.utils import timezone

from apps.monitor_eventos.models import MonitorEventoRecord, MonitorEventoSyncLog
from apps.monitor_eventos.services.source_path import (
    SourceFileInfo,
    file_signature,
    get_source_file,
)
from apps.monitor_eventos.services.sync import sync_monitor_eventos_to_db

_SYNC_STALE_MINUTES = 30


class SyncInProgressError(Exception):
    """Outra sincronização ainda está em andamento."""


def _finalize_stale_sync_logs() -> None:
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    stale_qs = MonitorEventoSyncLog.objects.filter(
        finished_at__isnull=True,
        started_at__lt=cutoff,
    )
    now = timezone.now()
    for log in stale_qs:
        log.finished_at = now
        msg = (log.message or "").strip()
        if msg == "Sincronização concluída.":
            log.success = True
        else:
            log.success = False
            if not msg:
                log.message = "Sincronização interrompida (não concluída)."
        log.duration_seconds = round((now - log.started_at).total_seconds(), 2)
        log.save(
            update_fields=["finished_at", "success", "message", "duration_seconds"]
        )


def _sync_in_progress() -> bool:
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    return MonitorEventoSyncLog.objects.filter(
        finished_at__isnull=True,
        started_at__gte=cutoff,
    ).exists()


def _last_success_signature() -> tuple[str, float, int] | None:
    log = (
        MonitorEventoSyncLog.objects.filter(success=True)
        .exclude(source_file="")
        .order_by("-finished_at")
        .first()
    )
    if not log or log.source_mtime is None or log.source_size is None:
        return None
    return (log.source_file, log.source_mtime, log.source_size)


def should_sync(source: SourceFileInfo, force: bool = False) -> bool:
    if force:
        return True
    current = file_signature(source)
    previous = _last_success_signature()
    return previous != current


def run_sync_with_audit(
    path: str | None = None,
    user=None,
    trigger_source: str = MonitorEventoSyncLog.TRIGGER_USER,
    force: bool = False,
) -> Tuple[bool, MonitorEventoSyncLog, bool]:
    """Retorna (success, log, skipped)."""
    from apps.common.bot_db_sync_gate import bot_db_sync_slot
    from apps.common.bot_db_sync_lanes import LANE_LOW

    with bot_db_sync_slot("monitor_eventos", lane=LANE_LOW):
        return _run_sync_with_audit_unlocked(
            path=path, user=user, trigger_source=trigger_source, force=force
        )


def _run_sync_with_audit_unlocked(
    path: str | None = None,
    user=None,
    trigger_source: str = MonitorEventoSyncLog.TRIGGER_USER,
    force: bool = False,
) -> Tuple[bool, MonitorEventoSyncLog, bool]:
    _finalize_stale_sync_logs()
    if _sync_in_progress():
        raise SyncInProgressError(
            "Já existe uma sincronização em andamento. Aguarde a conclusão antes de tentar novamente."
        )

    source = get_source_file(path)
    skipped = False
    if not should_sync(source, force=force):
        log = MonitorEventoSyncLog.objects.create(
            user=user,
            trigger_source=trigger_source,
            success=True,
            message="Nenhuma alteração detectada no arquivo fonte.",
            source_file=str(source.path.resolve()),
            source_mtime=source.mtime,
            source_size=source.size,
            row_count=MonitorEventoRecord.objects.count(),
            finished_at=timezone.now(),
            duration_seconds=0,
        )
        return True, log, True

    log = MonitorEventoSyncLog.objects.create(
        user=user,
        trigger_source=trigger_source,
        source_file=str(source.path.resolve()),
        source_mtime=source.mtime,
        source_size=source.size,
    )
    t0 = time.perf_counter()
    try:
        success, _, row_count = sync_monitor_eventos_to_db(path=path, force=force)
        log.success = success
        log.row_count = row_count
        log.message = (
            "Sincronização concluída."
            if success
            else "Sincronização falhou (arquivo vazio ou inválido)."
        )
    except Exception as exc:
        log.success = False
        log.message = str(exc)
        raise
    finally:
        log.finished_at = timezone.now()
        log.duration_seconds = round(time.perf_counter() - t0, 2)
        log.save()

    return bool(log.success), log, skipped
