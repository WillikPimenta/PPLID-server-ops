# -*- coding: utf-8 -*-
"""Executa sync Excel → PostgreSQL com auditoria em SyncAuditLog."""
from __future__ import annotations

import time
from datetime import timedelta
from typing import Tuple

from django.utils import timezone

from apps.falhas_criticas.models import SyncAuditLog
from apps.falhas_criticas.services.excel_path import require_excel_source_path
from apps.falhas_criticas.services.excel_sync import sync_excel_to_db

_SYNC_STALE_MINUTES = 30


class SyncInProgressError(Exception):
    """Outra sincronização ainda está em andamento."""


def _finalize_stale_sync_logs() -> None:
    SyncAuditLog.objects.filter(
        success=False,
        message="Sincronização concluída.",
        finished_at__isnull=False,
    ).update(success=True)

    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    stale_qs = SyncAuditLog.objects.filter(finished_at__isnull=True, started_at__lt=cutoff)
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
    return SyncAuditLog.objects.filter(
        finished_at__isnull=True,
        started_at__gte=cutoff,
    ).exists()


def run_sync_with_audit(
    path: str | None = None,
    user=None,
    trigger_source: str = SyncAuditLog.TRIGGER_USER,
) -> Tuple[bool, SyncAuditLog]:
    from apps.common.bot_db_sync_gate import bot_db_sync_slot
    from apps.common.bot_db_sync_lanes import LANE_HIGH

    with bot_db_sync_slot("falhas_criticas", lane=LANE_HIGH):
        return _run_sync_with_audit_unlocked(path=path, user=user, trigger_source=trigger_source)


def _run_sync_with_audit_unlocked(
    path: str | None = None,
    user=None,
    trigger_source: str = SyncAuditLog.TRIGGER_USER,
) -> Tuple[bool, SyncAuditLog]:
    _finalize_stale_sync_logs()
    if _sync_in_progress():
        raise SyncInProgressError(
            "Já existe uma sincronização em andamento. Aguarde a conclusão antes de tentar novamente."
        )

    excel_path = require_excel_source_path(path)
    log = SyncAuditLog.objects.create(
        user=user,
        path=excel_path,
        trigger_source=trigger_source,
    )
    t0 = time.perf_counter()
    stats: dict = {}
    try:
        success, stats = sync_excel_to_db(path=excel_path, user=user)
        log.success = success
        log.message = (
            "Sincronização concluída."
            if success
            else "Sincronização falhou (planilha vazia ou inválida)."
        )
    except Exception as exc:
        log.success = False
        log.message = str(exc)
        success = False
    finally:
        log.finished_at = timezone.now()
        log.duration_seconds = round(time.perf_counter() - t0, 2)
        log.stats = stats or {}
        log.save(update_fields=["success", "message", "finished_at", "duration_seconds", "stats"])
    return success, log
