# -*- coding: utf-8 -*-
"""Hooks que conectam o bot prioridades_nh ao sync PostgreSQL."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def on_prioridades_nh_saved(file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob

    rm = get_robot_manager()
    mode = "prioridades_nh"
    try:
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_PRIORIDADES_NH,
            source_path=file_path,
            force=True,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[prioridades-nh-sync] Job #{job.pk} {verb}; drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("prioridades_nh sync enqueue failed")
        rm._append_log(
            mode,
            f"[prioridades-nh-sync] ERRO ao enfileirar: {exc}",
            notify=False,
        )


def register_prioridades_nh_sync_hook() -> None:
    from apps.automacoes.services import get_robot_manager

    get_robot_manager().register_prioridades_nh_saved_callback(on_prioridades_nh_saved)
    log.info("Hook de sync prioridades_nh registrado")
