# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import timedelta
from typing import Tuple

from django.utils import timezone

from apps.produtividade_case.constants import (
    REPORT_CONSOLIDADO,
    REPORT_FILA_ABERTA,
    REPORT_PROD_HORA,
    REPORT_TEMPO_LOGADO,
)
from apps.produtividade_case.models import ProdutividadeCaseSyncLog
from apps.produtividade_case.services.fila_sync import sync_fila_from_json
from apps.produtividade_case.services.source_path import (
    SourceFileInfo,
    file_signature,
    get_source_file,
)
from apps.produtividade_case.services.sync import sync_case_to_productivity_record

_SYNC_STALE_MINUTES = 30
_VALID_REPORTS = {
    REPORT_CONSOLIDADO,
    REPORT_PROD_HORA,
    REPORT_TEMPO_LOGADO,
    REPORT_FILA_ABERTA,
}


class SyncInProgressError(Exception):
    """Outra sincronização do mesmo report_type ainda está em andamento."""


def _finalize_stale_sync_logs(report_type: str) -> None:
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    stale_qs = ProdutividadeCaseSyncLog.objects.filter(
        report_type=report_type,
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
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    return ProdutividadeCaseSyncLog.objects.filter(
        report_type=report_type,
        finished_at__isnull=True,
        started_at__gte=cutoff,
    ).exists()


def _last_success_signature(report_type: str) -> tuple[str, float, int] | None:
    log = (
        ProdutividadeCaseSyncLog.objects.filter(success=True, report_type=report_type)
        .exclude(source_file="")
        .order_by("-finished_at")
        .first()
    )
    if not log or log.source_mtime is None or log.source_size is None:
        return None
    return (log.source_file, log.source_mtime, log.source_size)


def should_sync(source: SourceFileInfo, report_type: str, force: bool = False) -> bool:
    if force:
        return True
    return _last_success_signature(report_type) != file_signature(source)


def run_sync_with_audit(
    *,
    report_type: str,
    path: str | None = None,
    user=None,
    trigger_source: str = ProdutividadeCaseSyncLog.TRIGGER_USER,
    force: bool = False,
) -> Tuple[bool, ProdutividadeCaseSyncLog, bool]:
    from apps.common.bot_db_sync_gate import bot_db_sync_slot
    from apps.common.bot_db_sync_lanes import LANE_LOW

    report_type = (report_type or "").strip().lower()
    if report_type not in _VALID_REPORTS:
        raise ValueError(f"report_type inválido: {report_type}")

    with bot_db_sync_slot(f"produtividade_case:{report_type}", lane=LANE_LOW):
        return _run_unlocked(
            report_type=report_type,
            path=path,
            user=user,
            trigger_source=trigger_source,
            force=force,
        )


def _run_unlocked(
    *,
    report_type: str,
    path: str | None,
    user,
    trigger_source: str,
    force: bool,
) -> Tuple[bool, ProdutividadeCaseSyncLog, bool]:
    _finalize_stale_sync_logs(report_type)

    # tempo_logado: só Excel no OneDrive — não grava ProductivityRecord
    if report_type == REPORT_TEMPO_LOGADO:
        source = get_source_file(path)
        log = ProdutividadeCaseSyncLog.objects.create(
            user=user,
            report_type=report_type,
            trigger_source=trigger_source,
            success=True,
            message="Ignorado no HxH: tempo_logado não entra em ProductivityRecord (stage_goal Case é fixo).",
            source_file=str(source.path.resolve()),
            source_mtime=source.mtime,
            source_size=source.size,
            finished_at=timezone.now(),
            duration_seconds=0,
        )
        return True, log, True

    # fila_aberta: JSON do bot → CaseFilaSnapshot / CaseFilaAgg
    if report_type == REPORT_FILA_ABERTA:
        return _run_fila_aberta(
            path=path,
            user=user,
            trigger_source=trigger_source,
            force=force,
        )

    if _sync_in_progress(report_type):
        raise SyncInProgressError(
            f"Já existe sync produtividade_case/{report_type} em andamento."
        )

    source = get_source_file(path)
    if not should_sync(source, report_type, force=force):
        log = ProdutividadeCaseSyncLog.objects.create(
            user=user,
            report_type=report_type,
            trigger_source=trigger_source,
            success=True,
            message="Nenhuma alteração detectada no arquivo fonte.",
            source_file=str(source.path.resolve()),
            source_mtime=source.mtime,
            source_size=source.size,
            finished_at=timezone.now(),
            duration_seconds=0,
        )
        return True, log, True

    log = ProdutividadeCaseSyncLog.objects.create(
        user=user,
        report_type=report_type,
        trigger_source=trigger_source,
        source_file=str(source.path.resolve()),
        source_mtime=source.mtime,
        source_size=source.size,
    )
    started = time.perf_counter()
    try:
        row_count, periodo_mes = sync_case_to_productivity_record(source.path, report_type)
        log.success = True
        log.row_count = row_count
        log.periodo_mes = periodo_mes
        log.message = "Sincronização Case → ProductivityRecord concluída."
    except Exception as exc:
        log.success = False
        log.message = f"Falha na sincronização: {exc}"
    finally:
        now = timezone.now()
        log.finished_at = now
        log.duration_seconds = round(time.perf_counter() - started, 2)
        log.save(
            update_fields=[
                "success",
                "message",
                "row_count",
                "periodo_mes",
                "finished_at",
                "duration_seconds",
            ]
        )
    return log.success, log, False


def _run_fila_aberta(
    *,
    path: str | None,
    user,
    trigger_source: str,
    force: bool,
) -> Tuple[bool, ProdutividadeCaseSyncLog, bool]:
    from pathlib import Path as FsPath

    if _sync_in_progress(REPORT_FILA_ABERTA):
        raise SyncInProgressError(
            "Já existe sync produtividade_case/fila_aberta em andamento."
        )

    source_path = (path or "").strip()
    if not source_path:
        from apps.produtividade_case.services.fila_sync import latest_successful_snapshot

        last = latest_successful_snapshot()
        if last and last.source_file:
            source_path = last.source_file
        else:
            raise FileNotFoundError(
                "Nenhum arquivo JSON da fila Case para sincronizar."
            )

    src = FsPath(source_path)
    if not src.is_file():
        raise FileNotFoundError(f"Arquivo da fila Case não encontrado: {src}")

    mtime = src.stat().st_mtime
    size = src.stat().st_size
    if not force:
        prev = _last_success_signature(REPORT_FILA_ABERTA)
        if prev == (str(src.resolve()), mtime, size):
            log = ProdutividadeCaseSyncLog.objects.create(
                user=user,
                report_type=REPORT_FILA_ABERTA,
                trigger_source=trigger_source,
                success=True,
                message="Nenhuma alteração detectada no snapshot da fila.",
                source_file=str(src.resolve()),
                source_mtime=mtime,
                source_size=size,
                finished_at=timezone.now(),
                duration_seconds=0,
            )
            return True, log, True

    log = ProdutividadeCaseSyncLog.objects.create(
        user=user,
        report_type=REPORT_FILA_ABERTA,
        trigger_source=trigger_source,
        source_file=str(src.resolve()),
        source_mtime=mtime,
        source_size=size,
    )
    started = time.perf_counter()
    try:
        snap = sync_fila_from_json(src)
        log.success = True
        log.row_count = snap.total_abertos
        log.message = (
            f"Snapshot fila Case gravado (id={snap.pk}, total={snap.total_abertos})."
        )
    except Exception as exc:
        log.success = False
        log.message = f"Falha na sincronização da fila: {exc}"
    finally:
        now = timezone.now()
        log.finished_at = now
        log.duration_seconds = round(time.perf_counter() - started, 2)
        log.save(
            update_fields=[
                "success",
                "message",
                "row_count",
                "finished_at",
                "duration_seconds",
            ]
        )
    return log.success, log, False
