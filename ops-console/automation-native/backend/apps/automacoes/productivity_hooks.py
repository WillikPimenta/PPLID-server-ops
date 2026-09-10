"""Hooks que conectam o bot Produção ao sync de produtividade no PostgreSQL."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def on_production_detalhado_saved(file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob

    rm = get_robot_manager()
    mode = "production"
    try:
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_PRODUTIVIDADE,
            source_path=file_path,
            force=True,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[produtividade-sync] Job #{job.pk} {verb}; drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("produtividade sync enqueue after production save failed")
        rm._append_log(mode, f"[produtividade-sync] ERRO ao enfileirar: {exc}", notify=False)


def register_production_sync_hook() -> None:
    from apps.automacoes.services import get_robot_manager

    get_robot_manager().register_production_saved_callback(on_production_detalhado_saved)
    log.info("Hook de sync produtividade registrado para bot Produção")
