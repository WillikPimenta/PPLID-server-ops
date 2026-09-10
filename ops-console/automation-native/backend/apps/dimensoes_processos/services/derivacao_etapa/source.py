# -*- coding: utf-8 -*-
"""Resolução da fonte CSV (upload staging ou pasta legada)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from django.conf import settings

from apps.dimensoes_processos.models import DerivacaoEtapaUploadBatch
from apps.dimensoes_processos.services.derivacao_etapa.reader import (
    filter_derivacao_files,
    list_derivacao_files,
)
from apps.dimensoes_processos.services.derivacao_etapa.upload_staging import (
    batch_file_paths,
    get_active_upload_batch,
)


def default_csv_dir() -> Path:
    return Path(getattr(settings, "DERIVACAO_ETAPA_CSV_DIR", "") or "")


def resolve_derivacao_source(
    *,
    directory: Path | None = None,
    upload_batch_id: str | None = None,
    file_name: str = "",
    from_date: date | None = None,
    to_date: date | None = None,
) -> tuple[list[Path], str | None]:
    """Retorna paths dos CSVs e o upload_batch_id quando aplicável."""

    if upload_batch_id:
        batch = get_active_upload_batch(upload_batch_id)
        paths = batch_file_paths(batch)
        if not paths:
            raise FileNotFoundError("O lote de upload não possui arquivos válidos.")
        files = filter_derivacao_files(
            paths,
            file_name=file_name,
            from_date=from_date,
            to_date=to_date,
        )
        if not files:
            raise FileNotFoundError("Nenhum FINALIZADO_*.csv no lote para o período informado.")
        return files, str(batch.token)

    csv_dir = directory or default_csv_dir()
    if not csv_dir.is_dir():
        raise FileNotFoundError(f"Diretório não encontrado: {csv_dir}")

    files = list_derivacao_files(csv_dir, file_name=file_name, from_date=from_date, to_date=to_date)
    if not files:
        raise FileNotFoundError(f"Nenhum FINALIZADO_*.csv em {csv_dir}")
    return files, None


def upload_batch_id_from_scan(scan_run) -> str | None:
    metrics = scan_run.metrics if scan_run else {}
    if not isinstance(metrics, dict):
        return None
    raw = metrics.get("upload_batch_id")
    return str(raw).strip() if raw else None
