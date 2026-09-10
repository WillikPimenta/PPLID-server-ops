"""Exporta os recortes operacionais usados pelo Capacity."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal

from apps.dimensoes_processos.services.capacity import (
    HUNDRED,
    ZERO,
    _active_metas,
    _approved_import_run,
    _decimal,
    _projection_by_workflow,
    _reference_derivation_rows,
)
from apps.dimensoes_processos.services.capacity_volume_esperado import (
    build_volume_hora_contrato,
)
from apps.dimensoes_processos.services.capacity_distribution import (
    calculate_hourly_capacity_drilldown,
)


def _csv_response(rows: list[list[object]], filename: str):
    from django.http import HttpResponse

    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerows(rows)
    response = HttpResponse("\ufeff" + buffer.getvalue(), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def derivacoes_csv(on_date: date):
    run = _approved_import_run(on_date)
    reference_date, derivation_rows = _reference_derivation_rows(run, on_date)
    workflow_totals, _workflow_meta, _duplicates, _nulls = _projection_by_workflow(on_date)
    metas_by_stage = _active_metas(on_date)
    meta_by_stage: dict[int, Decimal | None] = {}
    for etapa_id, candidates in metas_by_stage.items():
        meta_by_stage[etapa_id] = (
            _decimal(candidates[0].meta_dia)
            if len(candidates) == 1 and _decimal(candidates[0].meta_dia) > ZERO
            else None
        )

    rows: list[list[object]] = [[
        "Data referência", "Cliente", "ID Cliente", "Workflow", "ID Workflow",
        "Etapa", "ID Etapa", "Meta/dia", "Volume Projeção", "Volume",
        "Derivação %", "Qnt FTE",
    ]]
    selected = [
        row for row in derivation_rows
        if row.workflow.ind_considerar and row.cliente.operations
    ]
    selected.sort(key=lambda row: (row.cliente.nome.casefold(), row.workflow.nome.casefold(), row.etapa.nome.casefold()))
    for row in selected:
        projected = workflow_totals.get((row.cliente_id, row.workflow_id), ZERO)
        derived_volume = projected * _decimal(row.percentual) / HUNDRED
        meta = meta_by_stage.get(row.etapa_id)
        fte = derived_volume / meta if meta and meta > ZERO else None
        rows.append([
            reference_date.isoformat(), row.cliente.nome, row.cliente_id,
            row.workflow.nome, row.workflow_id, row.etapa.nome, row.etapa_id,
            str(meta.quantize(Decimal("0.01"))) if meta is not None else "",
            str(projected.quantize(Decimal("0.01"))),
            str(derived_volume.quantize(Decimal("0.01"))),
            str(_decimal(row.percentual).quantize(Decimal("0.01"))),
            str(fte.quantize(Decimal("0.0001"))) if fte is not None else "",
        ])
    return _csv_response(rows, f"capacity-derivacoes-{on_date.isoformat()}.csv")


def volume_esperado_hora_csv(on_date: date):
    hourly = build_volume_hora_contrato(on_date)
    by_client_hour: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    for (cliente_id, _workflow_id, hour), volume in hourly.items():
        by_client_hour[(cliente_id, hour)] += volume
    client_names = dict(
        # A função de projeção já aplica as regras de vigência e de duplicidade.
        (cliente_id, meta["cliente_nome"])
        for (cliente_id, _workflow_id), meta in _projection_by_workflow(on_date)[1].items()
    )
    rows: list[list[object]] = [["Data", "Cliente", "ID Cliente", "Hora", "Volume esperado"]]
    for (cliente_id, hour), volume in sorted(by_client_hour.items(), key=lambda item: (client_names.get(item[0][0], "").casefold(), item[0][1])):
        rows.append([
            on_date.isoformat(), client_names.get(cliente_id, str(cliente_id)), cliente_id,
            f"{hour:02d}:00", str(volume.quantize(Decimal("0.01"))),
        ])
    return _csv_response(rows, f"volume-esperado-hora-{on_date.isoformat()}.csv")


def hourly_drilldown_csv(on_date: date, *, hour: int, level: str, **filters):
    payload = calculate_hourly_capacity_drilldown(
        on_date, hour=hour, level=level, **filters
    )
    rows: list[list[object]] = [[
        "Data", "Hora", "Nivel", "Familia", "Cliente", "ID Cliente",
        "Workflow", "ID Workflow", "Etapa", "ID Etapa", "Volume esperado",
        "Volume recebido", "Derivacao %", "Volume derivado esperado",
        "Meta/hora", "FTE simultaneo esperado", "FTE simultaneo recebido",
        "Referencia inteira", "Estimado/fallback", "Origem perfil",
    ]]
    for item in payload["rows"]:
        rows.append([
            on_date.isoformat(), f"{hour:02d}:00", level, item.get("familia"),
            item.get("cliente_nome"), item.get("id_cliente"), item.get("workflow_nome"),
            item.get("id_workflow"), item.get("etapa_nome"), item.get("id_etapa"),
            item["expected_volume"], item["received_volume"], item["derivation_pct"],
            item["expected_derived_volume"], item["goal_per_hour"],
            item["expected_simultaneous_fte"], item["received_simultaneous_fte"],
            item["people_reference"], "sim" if item["fallback"] else "nao",
            ";".join(item["sources"]["hourly_profile"]),
        ])
    return _csv_response(
        rows,
        f"capacity-recorte-{on_date.isoformat()}-{hour:02d}h-{level}.csv",
    )
