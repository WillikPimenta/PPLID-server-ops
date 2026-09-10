"""Execução assíncrona do import Identificação dos Processos."""

from __future__ import annotations

import logging
import threading

from django.db import close_old_connections
from django.utils import timezone

from apps.dimensoes_processos.models import IdentificacaoProcessosImportRun, IdentificacaoProcessosUpload
from apps.dimensoes_processos.services.identificacao_processos.capacity_invalidation_helper import (
    invalidate_capacity_after_import,
)
from apps.dimensoes_processos.services.identificacao_processos.import_scope import normalize_import_scope
from apps.dimensoes_processos.services.identificacao_processos.reader import load_workbook_data
from apps.dimensoes_processos.services.identificacao_processos.sync import persist_workbook_data
from apps.dimensoes_processos.services.identificacao_processos.upload_staging import mark_upload_consumed

log = logging.getLogger(__name__)


def _execute_import_run(run_id: int) -> None:
    run = IdentificacaoProcessosImportRun.objects.select_related("upload").get(pk=run_id)
    upload: IdentificacaoProcessosUpload = run.upload
    try:
        data = load_workbook_data(upload.file.path)
        scope = normalize_import_scope(getattr(run, "import_scope", None) or "full")
        result = persist_workbook_data(data, mode=run.mode, import_scope=scope)
        summary = result.as_summary()

        snapshots_deleted = invalidate_capacity_after_import(result)
        summary["capacity_snapshots_invalidated"] = snapshots_deleted

        run.status = IdentificacaoProcessosImportRun.STATUS_OK
        run.summary = summary
        run.message = "Importação concluída."
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "summary", "message", "finished_at"])
        mark_upload_consumed(upload)
        log.info("Import Identificação dos Processos run=%s concluído", run_id)
    except Exception as exc:
        log.exception("Import Identificação dos Processos run=%s falhou", run_id)
        run.status = IdentificacaoProcessosImportRun.STATUS_ERROR
        run.message = str(exc) or "Falha na importação."
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "finished_at"])


def _run_import_threadsafe(run_id: int) -> None:
    close_old_connections()
    try:
        _execute_import_run(run_id)
    finally:
        close_old_connections()


def schedule_import_run(run_id: int) -> None:
    threading.Thread(target=_run_import_threadsafe, args=(run_id,), daemon=True).start()
