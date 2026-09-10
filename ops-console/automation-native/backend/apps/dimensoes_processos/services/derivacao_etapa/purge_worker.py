# -*- coding: utf-8 -*-
"""Worker assíncrono para purge derivacao_etapa."""
from __future__ import annotations

import logging
import threading

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone

from apps.dimensoes_processos.models import DerivacaoEtapaImportRun
from apps.dimensoes_processos.services.derivacao_etapa.purge import (
    PURGE_KIND,
    purge_derivacao_etapa,
)

log = logging.getLogger(__name__)


def _purge_sync_enabled() -> bool:
    return bool(getattr(settings, "DERIVACAO_ETAPA_PURGE_SYNC", False))


def _run_purge(
    run_id: int,
    *,
    include_aliases: bool,
    include_capacity_snapshots: bool,
) -> None:
    run = DerivacaoEtapaImportRun.objects.filter(pk=run_id).first()
    if run is None:
        return

    try:
        purge_derivacao_etapa(
            include_aliases=include_aliases,
            include_capacity_snapshots=include_capacity_snapshots,
            run=run,
        )
    except Exception as exc:
        log.exception("purge derivacao_etapa failed run_id=%s", run_id)
        run.refresh_from_db()
        if run.status == DerivacaoEtapaImportRun.STATUS_RUNNING:
            metrics = dict(run.metrics or {})
            metrics["kind"] = PURGE_KIND
            metrics["phase"] = "error"
            metrics["error"] = str(exc)[:500]
            run.status = DerivacaoEtapaImportRun.STATUS_ERROR
            run.message = str(exc)[:500]
            run.finished_at = timezone.now()
            run.metrics = metrics
            run.save(update_fields=["status", "message", "finished_at", "metrics"])


def _run_purge_threadsafe(
    run_id: int,
    *,
    include_aliases: bool,
    include_capacity_snapshots: bool,
) -> None:
    close_old_connections()
    try:
        _run_purge(
            run_id,
            include_aliases=include_aliases,
            include_capacity_snapshots=include_capacity_snapshots,
        )
    finally:
        close_old_connections()


def schedule_derivacao_etapa_purge(
    run_id: int,
    *,
    include_aliases: bool = True,
    include_capacity_snapshots: bool = True,
) -> None:
    if _purge_sync_enabled():
        _run_purge(
            run_id,
            include_aliases=include_aliases,
            include_capacity_snapshots=include_capacity_snapshots,
        )
        return
    threading.Thread(
        target=_run_purge_threadsafe,
        kwargs={
            "run_id": run_id,
            "include_aliases": include_aliases,
            "include_capacity_snapshots": include_capacity_snapshots,
        },
        daemon=True,
    ).start()
