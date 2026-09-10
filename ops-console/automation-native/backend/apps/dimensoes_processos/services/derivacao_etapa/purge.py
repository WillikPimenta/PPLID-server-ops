# -*- coding: utf-8 -*-
"""Remove toda a base derivacao_etapa (diária, scans, uploads, aliases)."""
from __future__ import annotations

from typing import Any, Callable

from django.db import transaction
from django.utils import timezone

from apps.dimensoes_processos.models import (
    CapacityDailySnapshot,
    CapacityHourlyProfileSnapshot,
    DerivacaoEtapaComparativo,
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DerivacaoEtapaUploadBatch,
    DimNomeAlias,
)
from apps.dimensoes_processos.services.derivacao_etapa.import_recovery import (
    recover_stale_derivacao_import_runs,
)
from apps.dimensoes_processos.services.derivacao_etapa.upload_staging import delete_upload_batch

PURGE_KIND = "purge"

PURGE_STEPS: tuple[tuple[str, str], ...] = (
    ("recover_stale", "Recuperando scan/import presos…"),
    ("counting", "Contando registros…"),
    ("diaria", "Apagando linhas diárias…"),
    ("comparativo", "Apagando scans comparativos…"),
    ("import_runs", "Apagando histórico scan/import…"),
    ("uploads", "Removendo uploads em staging…"),
    ("aliases", "Apagando associações manuais…"),
    ("capacity", "Apagando snapshots Capacity…"),
    ("done", "Concluído"),
)


def _step_total(include_aliases: bool, include_capacity_snapshots: bool) -> int:
    total = 6  # recover, counting, diaria, comparativo, import_runs, uploads
    if include_aliases:
        total += 1
    if include_capacity_snapshots:
        total += 1
    return total


def _save_progress(
    run: DerivacaoEtapaImportRun | None,
    *,
    phase: str,
    step_index: int,
    step_total: int,
    step_label: str,
    deleted: dict[str, int],
    stale_recovered: int = 0,
) -> None:
    if run is None:
        return
    metrics: dict[str, Any] = dict(run.metrics or {})
    metrics.update(
        {
            "kind": PURGE_KIND,
            "phase": phase,
            "step_index": step_index,
            "step_total": step_total,
            "step_label": step_label,
            "deleted": deleted,
            "stale_recovered": stale_recovered,
        }
    )
    run.metrics = metrics
    run.save(update_fields=["metrics"])


def purge_derivacao_etapa(
    *,
    include_aliases: bool = True,
    include_capacity_snapshots: bool = True,
    run: DerivacaoEtapaImportRun | None = None,
    progress_hook: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Apaga dados derivacao_etapa. Dimensões Megazord (cliente/WF/etapa) são preservadas."""
    deleted: dict[str, int] = {
        "diaria": 0,
        "comparativo": 0,
        "import_runs": 0,
        "upload_batches": 0,
        "aliases": 0,
        "capacity_daily_snapshots": 0,
        "capacity_hourly_snapshots": 0,
    }
    step_total = _step_total(include_aliases, include_capacity_snapshots)
    step_index = 0
    stale_recovered = 0

    def _progress(phase: str, label: str) -> None:
        nonlocal step_index
        step_index += 1
        _save_progress(
            run,
            phase=phase,
            step_index=step_index,
            step_total=step_total,
            step_label=label,
            deleted=deleted,
            stale_recovered=stale_recovered,
        )
        if progress_hook:
            progress_hook()

    step_index += 1
    stale_recovered = recover_stale_derivacao_import_runs()
    _save_progress(
        run,
        phase="recover_stale",
        step_index=step_index,
        step_total=step_total,
        step_label=PURGE_STEPS[0][1],
        deleted=deleted,
        stale_recovered=stale_recovered,
    )
    if progress_hook:
        progress_hook()

    _progress("counting", PURGE_STEPS[1][1])
    deleted["diaria"] = DerivacaoEtapaDiaria.objects.count()
    deleted["comparativo"] = DerivacaoEtapaComparativo.objects.count()
    deleted["import_runs"] = DerivacaoEtapaImportRun.objects.filter(
        run_kind__in=(
            DerivacaoEtapaImportRun.KIND_SCAN,
            DerivacaoEtapaImportRun.KIND_IMPORT,
        ),
    ).count()
    deleted["upload_batches"] = DerivacaoEtapaUploadBatch.objects.count()
    if include_aliases:
        deleted["aliases"] = DimNomeAlias.objects.count()
    if include_capacity_snapshots:
        deleted["capacity_daily_snapshots"] = CapacityDailySnapshot.objects.count()
        deleted["capacity_hourly_snapshots"] = CapacityHourlyProfileSnapshot.objects.count()

    _progress("diaria", PURGE_STEPS[2][1])
    with transaction.atomic():
        DerivacaoEtapaDiaria.objects.all().delete()

    _progress("comparativo", PURGE_STEPS[3][1])
    with transaction.atomic():
        DerivacaoEtapaComparativo.objects.all().delete()

    _progress("import_runs", PURGE_STEPS[4][1])
    with transaction.atomic():
        qs = DerivacaoEtapaImportRun.objects.filter(
            run_kind__in=(
                DerivacaoEtapaImportRun.KIND_SCAN,
                DerivacaoEtapaImportRun.KIND_IMPORT,
            ),
        )
        qs.filter(reviewed_scan__isnull=False).update(reviewed_scan=None)
        qs.delete()

    _progress("uploads", PURGE_STEPS[5][1])
    for batch in DerivacaoEtapaUploadBatch.objects.all().iterator():
        delete_upload_batch(batch)

    if include_aliases:
        _progress("aliases", PURGE_STEPS[6][1])
        with transaction.atomic():
            DimNomeAlias.objects.all().delete()

    if include_capacity_snapshots:
        label = PURGE_STEPS[7][1]
        _progress("capacity", label)
        with transaction.atomic():
            CapacityDailySnapshot.objects.all().delete()
            CapacityHourlyProfileSnapshot.objects.all().delete()

    if run is not None:
        run.status = DerivacaoEtapaImportRun.STATUS_OK
        run.message = "Base de derivação de etapas removida."
        run.finished_at = timezone.now()
        _save_progress(
            run,
            phase="done",
            step_index=step_total,
            step_total=step_total,
            step_label=PURGE_STEPS[-1][1],
            deleted=deleted,
            stale_recovered=stale_recovered,
        )
        run.save(update_fields=["status", "message", "finished_at"])

    return deleted
