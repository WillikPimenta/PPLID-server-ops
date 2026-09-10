# -*- coding: utf-8 -*-
"""Hooks que conectam bots Replicação D-1 / Rotina ao sync no PostgreSQL."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def on_replicacao_d1_saved(run_id: str, file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob

    rm = get_robot_manager()
    mode = "replicacao_auditoria_d1"
    try:
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_REPLICACAO_D1,
            source_path=file_path,
            report_type=(run_id or "").strip(),
            force=True,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[replicacao-d1-sync] Job #{job.pk} {verb}; drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("replicacao d1 sync enqueue after save failed")
        rm._append_log(mode, f"[replicacao-d1-sync] ERRO ao enfileirar: {exc}", notify=False)


def on_replicacao_d1_replicados_saved(file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob
    from apps.replicacao_d1.constants import REPORT_TYPE_REPLICADOS

    rm = get_robot_manager()
    mode = "rotina"
    try:
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_REPLICACAO_D1,
            source_path=file_path,
            report_type=REPORT_TYPE_REPLICADOS,
            force=True,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[replicacao-d1-replicados-sync] Job #{job.pk} {verb}; drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("replicacao d1 replicados sync enqueue after save failed")
        rm._append_log(
            mode,
            f"[replicacao-d1-replicados-sync] ERRO ao enfileirar: {exc}",
            notify=False,
        )


def register_replicacao_d1_sync_hook() -> None:
    from apps.automacoes.services import get_robot_manager

    rm = get_robot_manager()
    rm.register_replicacao_d1_saved_callback(on_replicacao_d1_saved)
    rm.register_replicacao_d1_replicados_saved_callback(on_replicacao_d1_replicados_saved)
    log.info("Hook de sync replicação D-1 (plano + replicados) registrado")
