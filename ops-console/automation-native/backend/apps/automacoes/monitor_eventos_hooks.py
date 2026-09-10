"""Hooks que conectam o bot Produção ao sync de monitor de eventos no PostgreSQL."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import uuid

log = logging.getLogger(__name__)


def _snapshot_monitor_source(file_path: str) -> Path:
    """Copia o parquet para um caminho imutavel, identificado pelo conteudo."""
    from django.conf import settings

    source = Path(file_path).resolve(strict=True)
    snapshot_dir = Path(
        getattr(
            settings,
            "BOT_DB_SYNC_SNAPSHOT_DIR",
            Path(settings.BASE_DIR) / "tmp" / "bot_db_sync_sources",
        )
    ) / "monitor_eventos"
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    for _attempt in range(3):
        before = source.stat()
        tmp = snapshot_dir / f".{source.stem}-{uuid.uuid4().hex}.tmp"
        digest = hashlib.sha256()
        try:
            with source.open("rb") as src, tmp.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    digest.update(chunk)
                    dst.write(chunk)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                tmp.unlink(missing_ok=True)
                continue

            destination = snapshot_dir / f"{source.stem}-{digest.hexdigest()[:20]}.parquet"
            if destination.exists():
                tmp.unlink(missing_ok=True)
                return destination
            os.replace(tmp, destination)
            return destination
        finally:
            tmp.unlink(missing_ok=True)

    raise RuntimeError(f"Monitor alterado durante a criacao do snapshot: {source}")


def on_monitor_eventos_saved(file_path: str) -> None:
    from apps.automacoes.services import get_robot_manager
    from apps.common.bot_db_sync_queue import enqueue_bot_db_sync
    from apps.common.models import BotDbSyncJob

    rm = get_robot_manager()
    mode = "production"
    try:
        snapshot_path = _snapshot_monitor_source(file_path)
        job, created = enqueue_bot_db_sync(
            domain=BotDbSyncJob.DOMAIN_MONITOR_EVENTOS,
            source_path=str(snapshot_path),
            force=False,
            spawn=True,
        )
        verb = "enfileirado" if created else "já na fila"
        rm._append_log(
            mode,
            f"[monitor-eventos-sync] Job #{job.pk} {verb}; drain em subprocesso.",
            notify=False,
        )
    except Exception as exc:
        log.exception("monitor eventos sync enqueue after production save failed")
        rm._append_log(mode, f"[monitor-eventos-sync] ERRO ao enfileirar: {exc}", notify=False)


def register_monitor_eventos_sync_hook() -> None:
    from apps.automacoes.services import get_robot_manager

    get_robot_manager().register_monitor_eventos_saved_callback(on_monitor_eventos_saved)
    log.info("Hook de sync monitor eventos registrado para bot Produção")
