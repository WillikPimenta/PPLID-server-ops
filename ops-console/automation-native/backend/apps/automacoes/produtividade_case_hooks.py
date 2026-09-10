# -*- coding: utf-8 -*-
"""Hooks que conectam o bot produtividade_case ao sync PostgreSQL."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def on_produtividade_case_saved(report_type: str, file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob

    rm = get_robot_manager()
    mode = "produtividade_case"
    try:
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE_CASE,
            source_path=file_path,
            report_type=(report_type or "").strip(),
            force=True,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[produtividade-case-sync] Job #{job.pk} {verb} ({report_type}); drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("produtividade_case sync enqueue failed")
        rm._append_log(
            mode,
            f"[produtividade-case-sync] ERRO ao enfileirar: {exc}",
            notify=False,
        )


def register_produtividade_case_sync_hook() -> None:
    from apps.automacoes.services import get_robot_manager

    get_robot_manager().register_produtividade_case_saved_callback(on_produtividade_case_saved)
    log.info("Hook de sync produtividade_case registrado")
