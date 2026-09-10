"""Hooks que conectam o bot Produção (GED irregularidade) ao sync de reinspeção."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def on_production_ged_irregularidade_saved(file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob

    rm = get_robot_manager()
    mode = "production"
    try:
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_REINSPECAO_GED,
            source_path=file_path,
            force=True,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[reinspecao-ged-sync] Job #{job.pk} {verb}; drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("reinspecao_ged sync enqueue after production GED save failed")
        rm._append_log(mode, f"[reinspecao-ged-sync] ERRO ao enfileirar: {exc}", notify=False)
        raise


def register_production_ged_irregularidade_sync_hook() -> None:
    from apps.automacoes.services import get_robot_manager

    get_robot_manager().register_production_ged_irregularidade_saved_callback(
        on_production_ged_irregularidade_saved
    )
    log.info("Hook de sync reinspecao_ged registrado para bot Produção")
