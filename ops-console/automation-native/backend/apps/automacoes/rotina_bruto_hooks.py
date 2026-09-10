"""Hooks que conectam o bot Rotina ao sync de relatórios brutos no PostgreSQL."""

from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

# Partes quinzenais do GED detalhado — o bot consolida em mensal antes do sync.
_GED_DETALHADO_INTERMEDIATE = re.compile(
    r"^ged-detalhado-tratado_\d{6}_[12]\.parquet$",
    re.IGNORECASE,
)


def is_intermediate_rotina_bruto_file(report_type: str, file_path: str) -> bool:
    """True se o arquivo é intermediário e não deve ir para a fila bot→DB."""
    name = Path(file_path or "").name
    if not name:
        return False
    if str(report_type or "").strip().lower() == "ged_detalhado":
        return bool(_GED_DETALHADO_INTERMEDIATE.match(name))
    return False


def on_rotina_bruto_saved(report_type: str, file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob

    rm = get_robot_manager()
    mode = "rotina"
    if is_intermediate_rotina_bruto_file(report_type, file_path):
        rm._append_log(
            mode,
            f"[rotina-bruto-sync] Ignorado intermediário ({report_type}): {Path(file_path).name}",
            notify=False,
        )
        return
    try:
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_ROTINA_BRUTO,
            source_path=file_path,
            report_type=report_type,
            force=True,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[rotina-bruto-sync] Job #{job.pk} ({report_type}) {verb}; drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("rotina bruto sync enqueue after save failed")
        rm._append_log(
            mode,
            f"[rotina-bruto-sync] ERRO ao enfileirar ({report_type}): {exc}",
            notify=False,
        )


def register_rotina_bruto_sync_hook() -> None:
    from apps.automacoes.services import get_robot_manager

    get_robot_manager().register_rotina_bruto_saved_callback(on_rotina_bruto_saved)
    log.info("Hook de sync rotina bruto registrado para bot Rotina")
