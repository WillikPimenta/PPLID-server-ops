# -*- coding: utf-8 -*-
"""Import CSV derivacao_etapa → derivacao_etapa_diaria."""
from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from decimal import Decimal

from django.utils import timezone

from apps.common.chunked_bulk_sync import chunked_partition_reload
from apps.dimensoes_processos.models import DerivacaoEtapaDiaria, DerivacaoEtapaImportRun
from apps.dimensoes_processos.services.derivacao_etapa.lookup import MegazordLookup
from apps.dimensoes_processos.services.derivacao_etapa.preflight import (
    build_source_manifest,
    fingerprint_source_manifest,
)
from apps.dimensoes_processos.services.derivacao_etapa.reader import (
    ParsedDerivacaoRow,
    read_derivacao_csv,
)
from apps.dimensoes_processos.services.derivacao_etapa.source import (
    default_csv_dir,  # noqa: F401 re-export
    resolve_derivacao_source,
)
from apps.dimensoes_processos.services.derivacao_etapa.upload_staging import consume_upload_batch, get_active_upload_batch

log = logging.getLogger(__name__)

MAX_UNMATCHED_SAMPLES = 25


class PreflightMismatchError(RuntimeError):
    """A fonte atual não corresponde aos arquivos revisados na análise."""


class UnsafePartialImportError(RuntimeError):
    """A importação foi bloqueada antes de substituir uma partição parcialmente."""


@dataclass
class ImportMetrics:
    files_processed: int = 0
    rows_inserted: int = 0
    rows_skipped_total: int = 0
    rows_rejected: int = 0
    rows_skipped_invalid: int = 0
    date_mismatch_files: list[str] = field(default_factory=list)
    unmatched_cliente: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    unmatched_workflow: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    unmatched_etapa: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def as_dict(self) -> dict[str, Any]:
        def _sample(d: dict[str, int]) -> list[dict[str, Any]]:
            items = sorted(d.items(), key=lambda x: (-x[1], x[0]))[:MAX_UNMATCHED_SAMPLES]
            return [{"nome": nome, "count": count} for nome, count in items]

        return {
            "files_processed": self.files_processed,
            "rows_inserted": self.rows_inserted,
            "rows_skipped_total": self.rows_skipped_total,
            "rows_rejected": self.rows_rejected,
            "rows_skipped_invalid": self.rows_skipped_invalid,
            "date_mismatch_files": self.date_mismatch_files,
            "unmatched_cliente": _sample(self.unmatched_cliente),
            "unmatched_workflow": _sample(self.unmatched_workflow),
            "unmatched_etapa": _sample(self.unmatched_etapa),
            "unmatched_cliente_count": sum(self.unmatched_cliente.values()),
            "unmatched_workflow_count": sum(self.unmatched_workflow.values()),
            "unmatched_etapa_count": sum(self.unmatched_etapa.values()),
        }


def _resolve_rows(
    rows: Iterable[ParsedDerivacaoRow],
    lookup: MegazordLookup,
    metrics: ImportMetrics,
) -> list[DerivacaoEtapaDiaria]:
    resolved: list[DerivacaoEtapaDiaria] = []
    for row in rows:
        cliente_res = lookup.resolve_cliente(row.cliente)
        if not cliente_res.ok:
            metrics.rows_rejected += 1
            metrics.unmatched_cliente[row.cliente] += 1
            continue
        workflow_res = lookup.resolve_workflow(row.workflow)
        if not workflow_res.ok:
            metrics.rows_rejected += 1
            metrics.unmatched_workflow[row.workflow] += 1
            continue
        etapa_res = lookup.resolve_etapa(row.etapa, workflow_id=workflow_res.id)
        if not etapa_res.ok:
            metrics.rows_rejected += 1
            metrics.unmatched_etapa[row.etapa] += 1
            continue

        resolved.append(
            DerivacaoEtapaDiaria(
                data=row.data,
                cliente_id=cliente_res.id,
                workflow_id=workflow_res.id,
                etapa_id=etapa_res.id,
                registros=row.registros,
                percentual=row.percentual,
                cliente_nome_origem=row.cliente,
                workflow_nome_origem=row.workflow,
                etapa_nome_origem=row.etapa,
                source_file=row.source_file,
            )
        )
    return resolved


def _consolidate_diaria_rows(rows: list[DerivacaoEtapaDiaria]) -> list[DerivacaoEtapaDiaria]:
    """Soma linhas que colidem na chave (data, cliente, workflow, etapa).

    Necessário quando vários nomes CSV de etapa automática apontam para o mesmo
    id_etapa via alias (ex.: OCR - Cliente A e OCR - Cliente B → OCR automática).
    """

    merged: dict[tuple, DerivacaoEtapaDiaria] = {}
    for row in rows:
        key = (row.data, row.cliente_id, row.workflow_id, row.etapa_id)
        existing = merged.get(key)
        if existing is None:
            merged[key] = row
            continue
        existing.registros += row.registros
        existing.percentual = (existing.percentual or Decimal("0")) + (row.percentual or Decimal("0"))
        if row.etapa_nome_origem and row.etapa_nome_origem not in (existing.etapa_nome_origem or ""):
            joined = " | ".join(filter(None, [existing.etapa_nome_origem, row.etapa_nome_origem]))
            existing.etapa_nome_origem = joined[:512]
    return list(merged.values())


def import_derivacao_etapa_csv(
    *,
    directory: Path | None = None,
    upload_batch_id: str | None = None,
    file_name: str = "",
    from_date: date | None = None,
    to_date: date | None = None,
    dry_run: bool = False,
    scan_run: DerivacaoEtapaImportRun | None = None,
    user=None,
) -> tuple[DerivacaoEtapaImportRun | None, ImportMetrics]:
    metrics = ImportMetrics()

    if scan_run is not None and not upload_batch_id:
        scan_metrics = scan_run.metrics if isinstance(scan_run.metrics, dict) else {}
        raw_batch = scan_metrics.get("upload_batch_id")
        upload_batch_id = str(raw_batch).strip() if raw_batch else None

    files, resolved_batch_id = resolve_derivacao_source(
        directory=directory,
        upload_batch_id=upload_batch_id,
        file_name=file_name,
        from_date=from_date,
        to_date=to_date,
    )

    current_manifest = build_source_manifest(files)
    current_fingerprint = fingerprint_source_manifest(current_manifest)
    if scan_run is not None:
        if not scan_run.source_fingerprint or current_fingerprint != scan_run.source_fingerprint:
            raise PreflightMismatchError(
                "Os arquivos mudaram desde a análise. Analise o período novamente antes de importar."
            )

    if dry_run:
        lookup = MegazordLookup.build()
        for path in files:
            result = read_derivacao_csv(path)
            metrics.files_processed += 1
            metrics.rows_skipped_total += result.skipped_total
            metrics.rows_skipped_invalid += result.skipped_invalid
            if result.date_mismatch:
                metrics.date_mismatch_files.append(path.name)
            resolved = _resolve_rows(result.rows, lookup, metrics)
            metrics.rows_inserted += len(resolved)
        return None, metrics

    run = DerivacaoEtapaImportRun.objects.create(
        run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
        status=DerivacaoEtapaImportRun.STATUS_RUNNING,
        message=f"Import derivacao_etapa: {len(files)} arquivo(s)",
        period_from=from_date,
        period_to=to_date,
        source_file_filter=file_name,
        source_manifest=current_manifest,
        source_fingerprint=current_fingerprint,
        reviewed_scan=scan_run,
        triggered_by=user,
        metrics={"upload_batch_id": resolved_batch_id} if resolved_batch_id else {},
    )

    try:
        lookup = MegazordLookup.build()
        rows_by_date: dict[date, list[DerivacaoEtapaDiaria]] = defaultdict(list)

        for path in files:
            result = read_derivacao_csv(path)
            metrics.files_processed += 1
            metrics.rows_skipped_total += result.skipped_total
            metrics.rows_skipped_invalid += result.skipped_invalid
            if result.date_mismatch:
                metrics.date_mismatch_files.append(path.name)

            resolved = _resolve_rows(result.rows, lookup, metrics)
            for row in resolved:
                row.import_run = run
                rows_by_date[row.data].append(row)

        if metrics.rows_rejected:
            raise UnsafePartialImportError(
                f"Importação bloqueada: {metrics.rows_rejected} linha(s) possuem pendências."
            )

        for ref_date, records in sorted(rows_by_date.items()):
            consolidated = _consolidate_diaria_rows(records)
            inserted = chunked_partition_reload(
                model=DerivacaoEtapaDiaria,
                delete_queryset=DerivacaoEtapaDiaria.objects.filter(data=ref_date),
                records=consolidated,
            )
            metrics.rows_inserted += inserted

        run.status = DerivacaoEtapaImportRun.STATUS_OK
        run.files_processed = metrics.files_processed
        run.rows_inserted = metrics.rows_inserted
        run.rows_skipped_total = metrics.rows_skipped_total
        run.rows_rejected = metrics.rows_rejected
        run.message = (
            f"Import OK: {metrics.files_processed} arquivo(s), "
            f"{metrics.rows_inserted} linha(s), {metrics.rows_rejected} rejeitada(s)"
        )
        run.metrics = metrics.as_dict()
        if resolved_batch_id:
            run.metrics["upload_batch_id"] = resolved_batch_id
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "files_processed",
                "rows_inserted",
                "rows_skipped_total",
                "rows_rejected",
                "message",
                "metrics",
                "finished_at",
            ]
        )
        if resolved_batch_id:
            batch = get_active_upload_batch(resolved_batch_id)
            consume_upload_batch(batch)
        return run, metrics
    except UnsafePartialImportError as exc:
        run.status = DerivacaoEtapaImportRun.STATUS_ERROR
        run.rows_rejected = metrics.rows_rejected
        run.message = str(exc)
        run.metrics = metrics.as_dict()
        run.finished_at = timezone.now()
        run.save(
            update_fields=["status", "rows_rejected", "message", "metrics", "finished_at"]
        )
        raise
    except Exception as exc:
        log.exception("import derivacao_etapa failed")
        run.status = DerivacaoEtapaImportRun.STATUS_ERROR
        run.message = str(exc)
        run.metrics = metrics.as_dict()
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "metrics", "finished_at"])
        raise
