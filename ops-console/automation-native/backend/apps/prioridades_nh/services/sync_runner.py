# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
from typing import Tuple

from django.utils import timezone

from apps.prioridades_nh.models import PrioridadesNhSyncLog
from apps.prioridades_nh.services.sync import sync_prioridades_from_path

_SYNC_STALE_MINUTES = 30


class SyncInProgressError(Exception):
    """Outra sincronização ainda está em andamento."""


def _finalize_stale_sync_logs() -> None:
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    stale_qs = PrioridadesNhSyncLog.objects.filter(
        finished_at__isnull=True, started_at__lt=cutoff
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
        log.save(update_fields=["finished_at", "success", "message", "duration_seconds"])


def _sync_in_progress() -> bool:
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    return PrioridadesNhSyncLog.objects.filter(
        finished_at__isnull=True,
        started_at__gte=cutoff,
    ).exists()


def run_sync_with_audit(
    path: str | None = None,
    user=None,
    trigger_source: str = PrioridadesNhSyncLog.TRIGGER_SYSTEM,
) -> Tuple[bool, PrioridadesNhSyncLog, bool]:
    from apps.common.bot_db_sync_gate import bot_db_sync_slot
    from apps.common.bot_db_sync_lanes import LANE_LOW

    with bot_db_sync_slot("prioridades_nh", lane=LANE_LOW):
        return _run_sync_with_audit_unlocked(
            path=path, user=user, trigger_source=trigger_source
        )


def _run_sync_with_audit_unlocked(
    path: str | None = None,
    user=None,
    trigger_source: str = PrioridadesNhSyncLog.TRIGGER_SYSTEM,
) -> Tuple[bool, PrioridadesNhSyncLog, bool]:
    _finalize_stale_sync_logs()
    if _sync_in_progress():
        raise SyncInProgressError(
            "Já existe uma sincronização de prioridades NH em andamento."
        )

    source = (path or "").strip()
    if not source or not Path(source).is_file():
        log = PrioridadesNhSyncLog.objects.create(
            user=user,
            path=source,
            trigger_source=trigger_source,
            success=False,
            message="Arquivo JSON de prioridades NH não encontrado.",
            finished_at=timezone.now(),
            duration_seconds=0,
        )
        return False, log, False

    log = PrioridadesNhSyncLog.objects.create(
        user=user,
        path=source,
        trigger_source=trigger_source,
    )
    t0 = time.monotonic()
    try:
        stats = sync_prioridades_from_path(source)
        log.inserted = stats["inserted"]
        log.updated = stats["updated"]
        log.unchanged = stats["unchanged"]
        log.deleted = stats["deleted"]
        log.success = True
        log.message = (
            "Sincronização concluída. "
            f"inseridos={stats['inserted']} atualizados={stats['updated']} "
            f"iguais={stats['unchanged']} removidos={stats['deleted']}"
        )
        log.finished_at = timezone.now()
        log.duration_seconds = round(time.monotonic() - t0, 2)
        log.save()
        return True, log, False
    except Exception as exc:
        log.success = False
        log.message = str(exc)[:2000]
        log.finished_at = timezone.now()
        log.duration_seconds = round(time.monotonic() - t0, 2)
        log.save()
        return False, log, False
