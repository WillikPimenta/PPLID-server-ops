# -*- coding: utf-8 -*-
"""Scan comparativo CSV vs Megazord (distinct por dimensão)."""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.dimensoes_processos.models import (
    DerivacaoEtapaComparativo,
    DerivacaoEtapaImportRun,
    DimNomeAlias,
)
from apps.dimensoes_processos.services.derivacao_etapa.lookup import MegazordLookup
from apps.dimensoes_processos.services.derivacao_etapa.normalize import (
    normalize_csv_etapa_key,
    normalize_csv_name_key,
)
from apps.dimensoes_processos.services.derivacao_etapa.preflight import (
    build_source_manifest,
    fingerprint_source_manifest,
)
from apps.dimensoes_processos.services.derivacao_etapa.reader import (
    read_derivacao_csv,
)
from apps.dimensoes_processos.services.derivacao_etapa.source import resolve_derivacao_source

log = logging.getLogger(__name__)


@dataclass
class ScanMetrics:
    files_processed: int = 0
    rows_csv: int = 0
    rows_skipped_total: int = 0
    rows_skipped_invalid: int = 0
    date_mismatch_files: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files_processed": self.files_processed,
            "rows_csv": self.rows_csv,
            "rows_skipped_total": self.rows_skipped_total,
            "rows_skipped_invalid": self.rows_skipped_invalid,
            "date_mismatch_files": self.date_mismatch_files,
        }


def _agg_key(dimensao: str, nome: str) -> str:
    if dimensao == DimNomeAlias.DIM_ETAPA:
        return normalize_csv_etapa_key(nome)
    return normalize_csv_name_key(nome)


def scan_derivacao_etapa_comparativo(
    *,
    directory: Path | None = None,
    upload_batch_id: str | None = None,
    file_name: str = "",
    from_date: date | None = None,
    to_date: date | None = None,
    user=None,
) -> tuple[DerivacaoEtapaImportRun, ScanMetrics]:
    metrics = ScanMetrics()

    files, resolved_batch_id = resolve_derivacao_source(
        directory=directory,
        upload_batch_id=upload_batch_id,
        file_name=file_name,
        from_date=from_date,
        to_date=to_date,
    )
    source_manifest = build_source_manifest(files)
    source_fingerprint = fingerprint_source_manifest(source_manifest)

    agg: dict[str, dict[str, tuple[int, int]]] = {
        DimNomeAlias.DIM_CLIENTE: defaultdict(lambda: (0, 0)),
        DimNomeAlias.DIM_WORKFLOW: defaultdict(lambda: (0, 0)),
        DimNomeAlias.DIM_ETAPA: defaultdict(lambda: (0, 0)),
    }
    display_name: dict[str, dict[str, str]] = {
        DimNomeAlias.DIM_CLIENTE: {},
        DimNomeAlias.DIM_WORKFLOW: {},
        DimNomeAlias.DIM_ETAPA: {},
    }

    for path in files:
        result = read_derivacao_csv(path)
        metrics.files_processed += 1
        metrics.rows_skipped_total += result.skipped_total
        metrics.rows_skipped_invalid += result.skipped_invalid
        if result.date_mismatch:
            metrics.date_mismatch_files.append(path.name)

        for row in result.rows:
            metrics.rows_csv += 1
            for dimensao, value in (
                (DimNomeAlias.DIM_CLIENTE, row.cliente),
                (DimNomeAlias.DIM_WORKFLOW, row.workflow),
                (DimNomeAlias.DIM_ETAPA, row.etapa),
            ):
                key = _agg_key(dimensao, value)
                if not key:
                    continue
                linhas, registros = agg[dimensao][key]
                agg[dimensao][key] = (linhas + 1, registros + row.registros)
                display_name[dimensao].setdefault(key, value)

    lookup = MegazordLookup.build()
    source_label = f"upload:{resolved_batch_id}" if resolved_batch_id else f"dir:{directory or 'default'}"
    run = DerivacaoEtapaImportRun.objects.create(
        run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
        status=DerivacaoEtapaImportRun.STATUS_RUNNING,
        message=f"Scan comparativo: {source_label}",
        period_from=from_date,
        period_to=to_date,
        source_file_filter=file_name,
        source_manifest=source_manifest,
        source_fingerprint=source_fingerprint,
        triggered_by=user,
        metrics={"upload_batch_id": resolved_batch_id} if resolved_batch_id else {},
    )

    try:
        comparativo_rows: list[DerivacaoEtapaComparativo] = []
        for dimensao in (DimNomeAlias.DIM_CLIENTE, DimNomeAlias.DIM_WORKFLOW, DimNomeAlias.DIM_ETAPA):
            for key, (linhas, registros) in agg[dimensao].items():
                nome = display_name[dimensao][key]
                if dimensao == DimNomeAlias.DIM_CLIENTE:
                    resolved = lookup.resolve_cliente(nome)
                elif dimensao == DimNomeAlias.DIM_WORKFLOW:
                    resolved = lookup.resolve_workflow(nome)
                else:
                    resolved = lookup.resolve_etapa(nome)

                sugestoes: list[dict] = []
                comparativo_rows.append(
                    DerivacaoEtapaComparativo(
                        scan_run=run,
                        dimensao=dimensao,
                        nome_origem=nome,
                        nome_origem_key=key,
                        linhas_csv=linhas,
                        registros_total=registros,
                        id_resolvido=resolved.id,
                        nome_megazord=resolved.nome_megazord or "",
                        status=resolved.status,
                        match_strategy=resolved.match_strategy,
                        sugestoes=sugestoes,
                    )
                )

        with transaction.atomic():
            DerivacaoEtapaComparativo.objects.filter(scan_run=run).delete()
            DerivacaoEtapaComparativo.objects.bulk_create(comparativo_rows, batch_size=500)

        run.status = DerivacaoEtapaImportRun.STATUS_OK
        run.files_processed = metrics.files_processed
        run.rows_inserted = len(comparativo_rows)
        run.rows_skipped_total = metrics.rows_skipped_total
        run.message = (
            f"Scan OK: {metrics.files_processed} arquivo(s), "
            f"{metrics.rows_csv} linha(s) CSV, {len(comparativo_rows)} distinct"
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
                "message",
                "metrics",
                "finished_at",
            ]
        )
        return run, metrics
    except Exception as exc:
        log.exception("scan derivacao_etapa failed")
        run.status = DerivacaoEtapaImportRun.STATUS_ERROR
        run.message = str(exc)
        run.metrics = metrics.as_dict()
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "message", "metrics", "finished_at"])
        raise


def build_comparativo_resumo(*, scan_run_id: int | None = None) -> dict[str, Any]:
    qs = DerivacaoEtapaComparativo.objects.all()
    if scan_run_id:
        qs = qs.filter(scan_run_id=scan_run_id)
    else:
        last_scan = (
            DerivacaoEtapaImportRun.objects.filter(
                run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
                status=DerivacaoEtapaImportRun.STATUS_OK,
            )
            .order_by("-started_at")
            .first()
        )
        if last_scan is None:
            return {"scan_run_id": None, "dimensoes": {}}
        scan_run_id = last_scan.pk
        qs = qs.filter(scan_run_id=scan_run_id)

    dimensoes: dict[str, Any] = {}
    ok_statuses = {
        DerivacaoEtapaComparativo.STATUS_OK,
        DerivacaoEtapaComparativo.STATUS_ALIAS,
    }
    for dim in (DimNomeAlias.DIM_CLIENTE, DimNomeAlias.DIM_WORKFLOW, DimNomeAlias.DIM_ETAPA):
        dim_qs = qs.filter(dimensao=dim)
        origens = dim_qs.count()
        ids_distintos = (
            dim_qs.filter(id_resolvido__isnull=False).values("id_resolvido").distinct().count()
        )
        ok_count = dim_qs.filter(status__in=ok_statuses).count()
        totals = dim_qs.aggregate(
            linhas_csv=Sum("linhas_csv"),
            registros_total=Sum("registros_total"),
        )
        resolved_totals = dim_qs.filter(status__in=ok_statuses).aggregate(
            linhas_csv=Sum("linhas_csv"),
            registros_total=Sum("registros_total"),
        )
        pending_totals = dim_qs.exclude(status__in=ok_statuses).aggregate(
            linhas_csv=Sum("linhas_csv"),
            registros_total=Sum("registros_total"),
        )
        linhas_csv = totals["linhas_csv"] or 0
        registros_total = totals["registros_total"] or 0
        registros_resolvidos = resolved_totals["registros_total"] or 0
        registros_pendentes = pending_totals["registros_total"] or 0
        dimensoes[dim] = {
            "origens_distintas": origens,
            "ids_megazord_distintos": ids_distintos,
            "ok_count": ok_count,
            "unmatched_count": dim_qs.filter(status=DerivacaoEtapaComparativo.STATUS_UNMATCHED).count(),
            "ambiguous_count": dim_qs.filter(status=DerivacaoEtapaComparativo.STATUS_AMBIGUOUS).count(),
            "taxa_ok_pct": round(100.0 * ok_count / origens, 2) if origens else None,
            "linhas_csv": linhas_csv,
            "linhas_resolvidas": resolved_totals["linhas_csv"] or 0,
            "linhas_pendentes": pending_totals["linhas_csv"] or 0,
            "registros_total": registros_total,
            "registros_resolvidos": registros_resolvidos,
            "registros_pendentes": registros_pendentes,
            "taxa_registros_ok_pct": (
                round(100.0 * registros_resolvidos / registros_total, 2)
                if registros_total
                else None
            ),
        }

    last_import = (
        DerivacaoEtapaImportRun.objects.filter(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            reviewed_scan_id=scan_run_id,
        )
        .order_by("-started_at")
        .first()
    )
    last_import_payload = None
    if last_import is not None:
        last_import_payload = {
            "id": last_import.pk,
            "status": last_import.status,
            "started_at": last_import.started_at.isoformat(),
            "finished_at": last_import.finished_at.isoformat() if last_import.finished_at else None,
            "rows_inserted": last_import.rows_inserted,
            "rows_rejected": last_import.rows_rejected,
        }

    return {
        "scan_run_id": scan_run_id,
        "dimensoes": dimensoes,
        "ultimo_import": last_import_payload,
    }
