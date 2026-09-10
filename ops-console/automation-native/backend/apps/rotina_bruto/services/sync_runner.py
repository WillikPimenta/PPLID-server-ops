# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import timedelta
from typing import Tuple

from django.utils import timezone

from apps.rotina_bruto.models import RotinaBrutoSyncLog
from apps.rotina_bruto.services.source_path import SourceFileInfo, file_signature, get_source_file
from apps.rotina_bruto.services.sync import SYNC_HANDLERS

_SYNC_STALE_MINUTES = 30


class SyncInProgressError(Exception):
    """Outra sincronização ainda está em andamento."""


def _finalize_stale_sync_logs() -> None:
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    stale_qs = RotinaBrutoSyncLog.objects.filter(
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


def _sync_in_progress(report_type: str) -> bool:
    """Bloqueia só o mesmo report_type (high/low podem correr em paralelo)."""
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    return RotinaBrutoSyncLog.objects.filter(
        report_type=report_type,
        finished_at__isnull=True,
        started_at__gte=cutoff,
    ).exists()


def _last_success_signature(report_type: str) -> tuple[str, str, float, int] | None:
    log = (
        RotinaBrutoSyncLog.objects.filter(success=True, report_type=report_type)
        .exclude(source_file="")
        .order_by("-finished_at")
        .first()
    )
    if not log or log.source_mtime is None or log.source_size is None:
        return None
    return (report_type, log.source_file, log.source_mtime, log.source_size)


def should_sync(source: SourceFileInfo, force: bool = False) -> bool:
    if force:
        return True
    current = file_signature(source)
    previous = _last_success_signature(source.report_type)
    return previous != current


def run_sync_with_audit(
    report_type: str,
    path: str | None = None,
    user=None,
    trigger_source: str = RotinaBrutoSyncLog.TRIGGER_USER,
    force: bool = False,
) -> Tuple[bool, RotinaBrutoSyncLog, bool]:
    """Retorna (success, log, skipped)."""
    if report_type not in SYNC_HANDLERS:
        raise ValueError(f"Tipo de relatório inválido: {report_type}")

    from apps.common.bot_db_sync_gate import bot_db_sync_slot
    from apps.common.bot_db_sync_lanes import resolve_lane
    from apps.common.models import BotDbSyncJob

    lane = resolve_lane(BotDbSyncJob.DOMAIN_ROTINA_BRUTO, report_type)
    with bot_db_sync_slot(f"rotina_bruto:{report_type}", lane=lane):
        return _run_sync_with_audit_unlocked(
            report_type=report_type,
            path=path,
            user=user,
            trigger_source=trigger_source,
            force=force,
        )


def _run_sync_with_audit_unlocked(
    report_type: str,
    path: str | None = None,
    user=None,
    trigger_source: str = RotinaBrutoSyncLog.TRIGGER_USER,
    force: bool = False,
) -> Tuple[bool, RotinaBrutoSyncLog, bool]:
    _finalize_stale_sync_logs()
    if _sync_in_progress(report_type):
        raise SyncInProgressError(
            f"Já existe uma sincronização de rotina bruto ({report_type}) em andamento. "
            "Aguarde a conclusão."
        )

    source = get_source_file(report_type, override_path=path)
    skipped = False
    if not should_sync(source, force=force):
        log = RotinaBrutoSyncLog.objects.create(
            report_type=report_type,
            user=user,
            trigger_source=trigger_source,
            success=True,
            message="Nenhuma alteração detectada no arquivo fonte.",
            source_file=str(source.path.resolve()),
            source_mtime=source.mtime,
            source_size=source.size,
            report_date=source.report_date,
            finished_at=timezone.now(),
            duration_seconds=0,
        )
        return True, log, True

    log = RotinaBrutoSyncLog.objects.create(
        report_type=report_type,
        user=user,
        trigger_source=trigger_source,
        source_file=str(source.path.resolve()),
        source_mtime=source.mtime,
        source_size=source.size,
        report_date=source.report_date,
    )
    t0 = time.perf_counter()
    try:
        handler_result = SYNC_HANDLERS[report_type](source)
        if isinstance(handler_result, int):
            row_count = handler_result
        else:
            row_count = int(handler_result.row_count)
            log.metrics = dict(handler_result.metrics)
            log.source_sha256 = str(handler_result.source_sha256)
            log.mapping_version = int(handler_result.mapping_version)
        log.success = True
        log.row_count = row_count
        log.message = (
            "Sincronização concluída."
            if row_count > 0
            else "Sincronização concluída (arquivo vazio)."
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
