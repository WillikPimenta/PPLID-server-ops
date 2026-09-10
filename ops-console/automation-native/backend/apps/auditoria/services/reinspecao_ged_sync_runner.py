# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from pathlib import Path
from typing import Tuple

from apps.auditoria.services.reinspecao_ged_ingestion import (
    finish_ged_ingestion,
    start_ged_ingestion,
)
from apps.auditoria.services.reinspecao_ged_sync import sync_reinspecao_ged_to_db
from apps.common.models import BotDbSyncJob


def run_sync_with_audit(
    path: str | None = None,
    *,
    force: bool = False,
    sync_job: BotDbSyncJob | None = None,
) -> Tuple[bool, str, bool]:
    """Executa sync GED → fila reinspeção. Retorna (success, message, skipped)."""
    from apps.common.bot_db_sync_gate import bot_db_sync_slot
    from apps.common.bot_db_sync_lanes import LANE_LOW

    del force  # incremental; sempre processa arquivo informado pelo bot
    if not path:
        raise ValueError("reinspecao_ged exige source_path")
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Arquivo GED irregularidade não encontrado: {source}")

    with bot_db_sync_slot("reinspecao_ged", lane=LANE_LOW):
        t0 = time.perf_counter()
        attempt = start_ged_ingestion(source, sync_job=sync_job)
        ingestion = attempt.ingestion
        if attempt.duplicate_delivery:
            elapsed = round(time.perf_counter() - t0, 2)
            return (
                True,
                "Conteúdo GED já processado para este mesmo artefato de execução "
                f"(tentativa {ingestion.attempt_number}, {elapsed}s).",
                True,
            )
        try:
            result = sync_reinspecao_ged_to_db(path=source)
        except Exception as exc:
            finish_ged_ingestion(
                ingestion,
                success=False,
                error_summary=str(exc),
            )
            return False, str(exc), False

        rows_read = int(result.get("total_parsed") or 0) + int(
            result.get("skipped_filtered") or 0
        )
        rows_loaded = int(result.get("created") or 0)
        rows_rejected = int(result.get("skipped_filtered") or 0)
        finish_ged_ingestion(
            ingestion,
            success=True,
            rows_read=rows_read,
            rows_loaded=rows_loaded,
            rows_rejected=rows_rejected,
        )
        elapsed = round(time.perf_counter() - t0, 2)
        artifact_hash = ingestion.artifact.content_sha256[:12]
        message = (
            f"Sincronização concluída: {result['created']} protocolo(s) inserido(s), "
            f"{result['skipped_existing']} já existente(s), "
            f"{result['skipped_finalized_ged']} finalizado(s) no GED, "
            f"{result['skipped_filtered']} filtrada(s) "
            f"(tentativa {ingestion.attempt_number}, sha256={artifact_hash}, {elapsed}s)."
        )
        skipped = result["created"] == 0 and result["total_parsed"] == 0
        return True, message, skipped
