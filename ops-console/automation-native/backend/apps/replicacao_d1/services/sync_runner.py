# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import timedelta
from typing import Tuple

from django.utils import timezone

from apps.replicacao_d1.constants import REPORT_TYPE_REPLICADOS
from apps.replicacao_d1.models import ReplicacaoD1SyncLog
from apps.replicacao_d1.services.replicados_source_path import (
    ReplicadosSourceFileInfo,
    get_replicados_source_file,
    replicados_file_signature,
)
from apps.replicacao_d1.services.source_path import (
    SourceFileInfo,
    file_signature,
    get_source_file,
)
from apps.replicacao_d1.services.sync import sync_replicacao_d1_to_db
from apps.replicacao_d1.services.sync_replicados import sync_replicados_to_db
from apps.replicacao_d1.feature_flags import ingestion_enabled, shadow_mode_enabled
from apps.replicacao_d1.services.ingestion import finish_ingestion, start_ingestion

_SYNC_STALE_MINUTES = 30


class SyncInProgressError(Exception):
    """Outra sincronização ainda está em andamento."""


def _finalize_stale_sync_logs() -> None:
    cutoff = timezone.now() - timedelta(minutes=_SYNC_STALE_MINUTES)
    stale_qs = ReplicacaoD1SyncLog.objects.filter(
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
    return ReplicacaoD1SyncLog.objects.filter(
        finished_at__isnull=True,
        started_at__gte=cutoff,
    ).exists()


def _last_success_signature(
    *,
    kind: str,
    run_id: str = "",
    report_date=None,
) -> tuple[str, float, int] | None:
    qs = ReplicacaoD1SyncLog.objects.filter(success=True, kind=kind).exclude(source_file="")
    if run_id:
        qs = qs.filter(run_id=run_id)
    if report_date is not None:
        qs = qs.filter(report_date=report_date)
    log = qs.order_by("-finished_at").first()
    if not log or log.source_mtime is None or log.source_size is None:
        return None
    return (log.source_file, log.source_mtime, log.source_size)


def should_sync_plano(source: SourceFileInfo, force: bool = False) -> bool:
    if force:
        return True
    return file_signature(source) != _last_success_signature(
        kind=ReplicacaoD1SyncLog.KIND_PLANO, run_id=source.run_id
    )


def should_sync_replicados(source: ReplicadosSourceFileInfo, force: bool = False) -> bool:
    if force:
        return True
    return replicados_file_signature(source) != _last_success_signature(
        kind=ReplicacaoD1SyncLog.KIND_REPLICADOS, report_date=source.report_date
    )


def run_sync_with_audit(
    path: str | None = None,
    user=None,
    trigger_source: str = ReplicacaoD1SyncLog.TRIGGER_USER,
    force: bool = False,
    run_id: str | None = None,
    report_type: str | None = None,
    sync_job=None,
) -> Tuple[bool, ReplicacaoD1SyncLog, bool]:
    """Retorna (success, log, skipped).

    report_type=replicados → CSV BRFlow; senão → Excel do plano (run_id opcional).
    """
    from apps.common.bot_db_sync_gate import bot_db_sync_slot
    from apps.common.bot_db_sync_lanes import LANE_LOW

    kind_label = (
        "replicacao_d1:replicados"
        if (report_type or "").strip() == REPORT_TYPE_REPLICADOS
        else "replicacao_d1"
    )
    with bot_db_sync_slot(kind_label, lane=LANE_LOW):
        if (report_type or "").strip() == REPORT_TYPE_REPLICADOS:
            return _run_replicados_sync_unlocked(
                path=path,
                user=user,
                trigger_source=trigger_source,
                force=force,
                sync_job=sync_job,
            )
        return _run_plano_sync_unlocked(
            path=path,
            user=user,
            trigger_source=trigger_source,
            force=force,
            run_id=run_id,
            sync_job=sync_job,
        )


def _run_plano_sync_unlocked(
    path: str | None = None,
    user=None,
    trigger_source: str = ReplicacaoD1SyncLog.TRIGGER_USER,
    force: bool = False,
    run_id: str | None = None,
    sync_job=None,
) -> Tuple[bool, ReplicacaoD1SyncLog, bool]:
    _finalize_stale_sync_logs()
    if _sync_in_progress():
        raise SyncInProgressError(
            "Já existe uma sincronização em andamento. Aguarde a conclusão antes de tentar novamente."
        )

    source = get_source_file(path, run_id=run_id)
    skipped = False
    if not should_sync_plano(source, force=force):
        log = ReplicacaoD1SyncLog.objects.create(
            user=user,
            trigger_source=trigger_source,
            kind=ReplicacaoD1SyncLog.KIND_PLANO,
            success=True,
            message="Nenhuma alteração detectada no arquivo fonte.",
            source_file=str(source.path.resolve()),
            source_mtime=source.mtime,
            source_size=source.size,
            run_id=source.run_id,
            row_count=0,
            finished_at=timezone.now(),
            duration_seconds=0,
        )
        return True, log, True

    log = ReplicacaoD1SyncLog.objects.create(
        user=user,
        trigger_source=trigger_source,
        kind=ReplicacaoD1SyncLog.KIND_PLANO,
        source_file=str(source.path.resolve()),
        source_mtime=source.mtime,
        source_size=source.size,
        run_id=source.run_id,
    )
    ingestion = None
    if ingestion_enabled():
        ingestion = start_ingestion(
            domain="replicacao_d1",
            kind="plano",
            reference_date=None,
            run_id=source.run_id,
            source_file=log.source_file,
            source_mtime=source.mtime,
            source_size=source.size,
            sync_job=sync_job,
            content_path=source.path,
        )
        log.ingestion = ingestion
        log.save(update_fields=["ingestion"])
    t0 = time.perf_counter()
    try:
        success, _, row_count, synced_run, rows_rejected = sync_replicacao_d1_to_db(
            path=str(source.path),
            run_id=source.run_id,
            force=force,
            ingestion_id=ingestion.pk if ingestion else None,
            attempt_number=ingestion.attempt_number if ingestion else None,
        )
        rows_read = row_count + rows_rejected
        log.success = success
        log.row_count = row_count
        log.rows_read = rows_read
        log.rows_loaded = row_count if success else 0
        log.rows_rejected = rows_rejected
        log.run_id = synced_run
        log.message = (
            "Sincronização concluída."
            if success
            else "Sincronização falhou (planilha vazia ou inválida)."
        )
        if shadow_mode_enabled() and success:
            from apps.replicacao_d1.services.excel_reader import read_replicacao_d1_excel
            from apps.replicacao_d1.services.shadow_compare import log_shadow_comparison

            parsed_shadow = read_replicacao_d1_excel(source.path, run_id=synced_run)
            shadow = log_shadow_comparison(parsed_shadow)
            if not shadow["match"]:
                log.message = f"{log.message} [shadow: {'; '.join(shadow['diffs'])}]"
        if ingestion:
            finish_ingestion(
                ingestion,
                success=success,
                rows_read=rows_read,
                rows_loaded=row_count if success else 0,
                rows_rejected=rows_rejected,
                error_summary="" if success else log.message,
            )
    except Exception as exc:
        log.success = False
        log.message = str(exc)
        if ingestion:
            finish_ingestion(
                ingestion,
                success=False,
                error_summary=str(exc)[:2000],
            )
        raise
    finally:
        log.finished_at = timezone.now()
        log.duration_seconds = round(time.perf_counter() - t0, 2)
        log.save()

    return bool(log.success), log, skipped


def _run_replicados_sync_unlocked(
    path: str | None = None,
    user=None,
    trigger_source: str = ReplicacaoD1SyncLog.TRIGGER_USER,
    force: bool = False,
    sync_job=None,
) -> Tuple[bool, ReplicacaoD1SyncLog, bool]:
    _finalize_stale_sync_logs()
    if _sync_in_progress():
        raise SyncInProgressError(
            "Já existe uma sincronização em andamento. Aguarde a conclusão antes de tentar novamente."
        )

    source = get_replicados_source_file(path)
    skipped = False
    if not should_sync_replicados(source, force=force):
        log = ReplicacaoD1SyncLog.objects.create(
            user=user,
            trigger_source=trigger_source,
            kind=ReplicacaoD1SyncLog.KIND_REPLICADOS,
            success=True,
            message="Nenhuma alteração detectada no arquivo fonte.",
            source_file=str(source.path.resolve()),
            source_mtime=source.mtime,
            source_size=source.size,
            report_date=source.report_date,
            row_count=0,
            finished_at=timezone.now(),
            duration_seconds=0,
        )
        return True, log, True

    log = ReplicacaoD1SyncLog.objects.create(
        user=user,
        trigger_source=trigger_source,
        kind=ReplicacaoD1SyncLog.KIND_REPLICADOS,
        source_file=str(source.path.resolve()),
        source_mtime=source.mtime,
        source_size=source.size,
        report_date=source.report_date,
    )
    ingestion = None
    if ingestion_enabled():
        ingestion = start_ingestion(
            domain="replicacao_d1",
            kind="replicados",
            reference_date=source.report_date,
            run_id="",
            source_file=log.source_file,
            source_mtime=source.mtime,
            source_size=source.size,
            sync_job=sync_job,
            content_path=source.path,
        )
        log.ingestion = ingestion
        log.save(update_fields=["ingestion"])
    t0 = time.perf_counter()
    try:
        def report_progress(message: str) -> None:
            if sync_job is None or not getattr(sync_job, "pk", None):
                return
            from apps.common.models import BotDbSyncJob

            BotDbSyncJob.objects.filter(
                pk=sync_job.pk,
                status=BotDbSyncJob.STATUS_RUNNING,
            ).update(message=message[:2000])

        success, _, row_count, rows_rejected = sync_replicados_to_db(
            path=str(source.path),
            force=force,
            ingestion_id=ingestion.pk if ingestion else None,
            progress=report_progress,
        )
        log.success = success
        log.row_count = row_count
        log.rows_read = row_count + rows_rejected
        log.rows_loaded = row_count if success else 0
        log.rows_rejected = rows_rejected
        log.report_date = source.report_date
        log.message = (
            "Sincronização de replicados concluída."
            if success
            else "Sincronização de replicados falhou (CSV vazio ou inválido)."
        )
        if ingestion:
            finish_ingestion(
                ingestion,
                success=success,
                rows_read=row_count + rows_rejected,
                rows_loaded=row_count if success else 0,
                rows_rejected=rows_rejected,
                error_summary="" if success else log.message,
            )
    except Exception as exc:
        log.success = False
        log.message = str(exc)
        if ingestion:
            finish_ingestion(
                ingestion,
                success=False,
                error_summary=str(exc)[:2000],
            )
        raise
    finally:
        log.finished_at = timezone.now()
        log.duration_seconds = round(time.perf_counter() - t0, 2)
        log.save()

    return bool(log.success), log, skipped
