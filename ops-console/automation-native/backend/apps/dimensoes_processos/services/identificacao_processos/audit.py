"""Auditoria read-only: xlsx vs DB, duplicatas vigentes, blockers Capacity, snapshots."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db.models import Q
from django.utils import timezone

from apps.dimensoes_processos.models import DimCliente, DimWorkflow, ProjecaoSla
from apps.dimensoes_processos.services.capacity import calculate_daily_capacity
from apps.dimensoes_processos.services.capacity_observability import inspect_snapshot_health
from apps.dimensoes_processos.services.identificacao_processos.cleanup_vigencia import (
    list_duplicate_vigente_groups,
)
from apps.dimensoes_processos.services.identificacao_processos.reader import load_workbook_data


def _sla_business_key(
    *,
    cliente_id: int,
    workflow_id: int,
    nivel_hierarquico_id: int,
    dias_semana: str,
) -> tuple[int, int, int, str]:
    return (cliente_id, workflow_id, nivel_hierarquico_id, str(dias_semana or "").strip())


def _xlsx_sla_volume_by_workflow(rows: list[dict[str, Any]]) -> dict[tuple[int, int], Decimal]:
    totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    for row in rows:
        volume = row.get("volume")
        if volume is None:
            continue
        key = (int(row["id_cliente"]), int(row["id_workflow"]))
        totals[key] += Decimal(str(volume))
    return dict(totals)


def _db_sla_volume_by_workflow(on_date: date) -> dict[tuple[int, int], Decimal]:
    totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: Decimal("0"))
    rows = ProjecaoSla.objects.filter(
        data_inicio__lte=on_date,
    ).filter(Q(data_fim__isnull=True) | Q(data_fim__gte=on_date))
    for sla in rows.iterator():
        if sla.volume is None:
            continue
        key = (sla.cliente_id, sla.workflow_id)
        totals[key] += Decimal(str(sla.volume))
    return dict(totals)


def models_q_fim_gte(on_date: date):
    from django.db.models import Q

    return Q(data_fim__isnull=True) | Q(data_fim__gte=on_date)


def compare_xlsx_db_volumes(
    path: Path | str,
    *,
    on_date: date,
    top_n: int = 20,
) -> dict[str, Any]:
    data = load_workbook_data(path)
    xlsx_totals = _xlsx_sla_volume_by_workflow(data.projecao_sla)
    db_totals = _db_sla_volume_by_workflow(on_date)

    cliente_names = {
        row.id_cliente: row.nome for row in DimCliente.objects.filter(pk__in={k[0] for k in xlsx_totals})
    }
    workflow_names = {
        row.id_workflow: row.nome for row in DimWorkflow.objects.filter(pk__in={k[1] for k in xlsx_totals})
    }

    diffs: list[dict[str, Any]] = []
    for key, xlsx_volume in sorted(xlsx_totals.items(), key=lambda item: item[1], reverse=True)[:top_n]:
        db_volume = db_totals.get(key, Decimal("0"))
        delta = db_volume - xlsx_volume
        diffs.append(
            {
                "id_cliente": key[0],
                "id_workflow": key[1],
                "cliente_nome": cliente_names.get(key[0], ""),
                "workflow_nome": workflow_names.get(key[1], ""),
                "xlsx_volume": str(xlsx_volume.quantize(Decimal("0.01"))),
                "db_volume": str(db_volume.quantize(Decimal("0.01"))),
                "delta": str(delta.quantize(Decimal("0.01"))),
            }
        )

    mismatches = [item for item in diffs if Decimal(item["delta"]) != 0]
    return {
        "on_date": on_date.isoformat(),
        "top_n": top_n,
        "compared": len(diffs),
        "mismatches": len(mismatches),
        "items": diffs,
    }


def capacity_blockers_for_dates(
    dates: list[date],
    *,
    scenario_id: str = "planejamento",
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for on_date in dates:
        try:
            payload = calculate_daily_capacity(
                on_date,
                scenario_id=scenario_id,
                include_details=False,
                include_hourly=False,
                include_dax=False,
            )
        except Exception as exc:
            samples.append(
                {
                    "date": on_date.isoformat(),
                    "error": str(exc),
                    "blockers": [],
                }
            )
            continue
        blockers = payload.get("blockers") or []
        samples.append(
            {
                "date": on_date.isoformat(),
                "blockers": blockers,
                "volume_adjustments_count": len(payload.get("volume_adjustments") or []),
                "max_daily_exact_fte": payload.get("summary", {}).get("max_daily_exact_fte"),
            }
        )
    return samples


def build_audit_report(
    path: Path | str | None = None,
    *,
    on_date: date | None = None,
    sample_dates: list[date] | None = None,
    include_snapshot_health: bool = True,
) -> dict[str, Any]:
    on_date = on_date or timezone.localdate()
    if sample_dates is None:
        sample_dates = [on_date, on_date - timedelta(days=7), on_date - timedelta(days=30)]

    duplicate_groups = list_duplicate_vigente_groups()
    volume_compare = None
    if path is not None:
        volume_compare = compare_xlsx_db_volumes(path, on_date=on_date)

    capacity_samples = capacity_blockers_for_dates(sample_dates)
    snapshot_health = None
    if include_snapshot_health:
        date_from = min(sample_dates)
        date_to = max(sample_dates)
        snapshot_health = inspect_snapshot_health(date_from, date_to, scenario_id="planejamento")

    healthy = duplicate_groups["total_groups"] == 0
    if volume_compare is not None:
        healthy = healthy and volume_compare["mismatches"] == 0

    return {
        "generated_at": timezone.now().isoformat(),
        "on_date": on_date.isoformat(),
        "healthy": healthy,
        "duplicate_vigentes": duplicate_groups,
        "volume_compare": volume_compare,
        "capacity_blockers": capacity_samples,
        "snapshot_health": snapshot_health,
    }
