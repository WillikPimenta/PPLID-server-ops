"""Executor local resiliente para a sincronização de demandas Jira."""

from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.db import close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from apps.planejamento_demandas.models import JiraDemandaSyncRun
from apps.planejamento_demandas.services.sync import execute_sync_run

log = logging.getLogger(__name__)

_sync_lock = threading.Lock()
_active_run_id: int | None = None


def stale_after_seconds() -> int:
    try:
        return max(60, int(getattr(settings, "JIRA_DEMANDAS_SYNC_STALE_SECONDS", 120)))
    except (TypeError, ValueError):
        return 120


def reconcile_stale_runs() -> int:
    """Encerra leases sem heartbeat para que a UI nunca acompanhe job órfão em loop."""
    now = timezone.now()
    cutoff = now - timezone.timedelta(seconds=stale_after_seconds())
    stale = JiraDemandaSyncRun.objects.filter(
        status__in=[JiraDemandaSyncRun.STATUS_QUEUED, JiraDemandaSyncRun.STATUS_RUNNING]
    ).filter(Q(heartbeat_at__lt=cutoff) | Q(heartbeat_at__isnull=True, started_at__lt=cutoff))
    count = 0
    for run in stale.iterator():
        run.status = JiraDemandaSyncRun.STATUS_INTERRUPTED
        run.error_code = "stale_heartbeat"
        run.message = "Sincronização interrompida: o processo deixou de enviar heartbeat."
        run.finished_at = now
        run.duration_seconds = round((now - run.started_at).total_seconds(), 2)
        run.save(
            update_fields=[
                "status",
                "error_code",
                "message",
                "finished_at",
                "duration_seconds",
            ]
        )
        count += 1
    return count


def _run_sync_threadsafe(run_id: int) -> None:
    global _active_run_id
    close_old_connections()
    try:
        execute_sync_run(run_id)
    except Exception as exc:  # defesa para falhas fora do ciclo normal de execute_sync_run
        log.exception("Falha fatal no sync Jira demandas (run=%s)", run_id)
        now = timezone.now()
        JiraDemandaSyncRun.objects.filter(
            pk=run_id,
            status__in=[JiraDemandaSyncRun.STATUS_QUEUED, JiraDemandaSyncRun.STATUS_RUNNING],
        ).update(
            status=JiraDemandaSyncRun.STATUS_FAILED,
            error_code="worker_crash",
            message=str(exc)[:1000],
            finished_at=now,
        )
    finally:
        close_old_connections()
        with _sync_lock:
            if _active_run_id == run_id:
                _active_run_id = None


def schedule_sync_run(run_id: int) -> bool:
    """Inicia thread daemon local; a lease e o heartbeat permanecem persistidos no banco."""
    global _active_run_id
    reconcile_stale_runs()
    with _sync_lock:
        if _active_run_id is not None:
            return False
        _active_run_id = run_id
    try:
        threading.Thread(
            target=_run_sync_threadsafe,
            args=(run_id,),
            daemon=True,
            name=f"jira-demandas-sync-{run_id}",
        ).start()
    except Exception:
        with _sync_lock:
            _active_run_id = None
        raise
    return True


def request_sync_cancel(run_id: int) -> tuple[JiraDemandaSyncRun | None, bool]:
    now = timezone.now()
    with transaction.atomic():
        run = JiraDemandaSyncRun.objects.select_for_update().filter(pk=run_id).first()
        if run is None:
            return None, False
        if run.status == JiraDemandaSyncRun.STATUS_QUEUED:
            run.status = JiraDemandaSyncRun.STATUS_CANCELLED
            run.cancel_requested_at = now
            run.finished_at = now
            run.progress_percent = 0
            run.message = "Sincronização cancelada antes de iniciar."
            run.duration_seconds = round((now - run.started_at).total_seconds(), 2)
            run.save()
            return run, True
        if run.status != JiraDemandaSyncRun.STATUS_RUNNING:
            return run, False
        run.cancel_requested_at = now
        run.message = "Cancelamento solicitado; encerrando a página atual…"
        run.save(update_fields=["cancel_requested_at", "message"])
        return run, True


def active_run_id_in_process() -> int | None:
    with _sync_lock:
        return _active_run_id
