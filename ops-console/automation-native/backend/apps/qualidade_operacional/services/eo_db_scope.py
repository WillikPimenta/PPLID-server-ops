# -*- coding: utf-8 -*-
"""Escopo de leitura EO — paridade Indicadores (Qualidade Operacional) × report BRB."""
from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import QuerySet


def build_eo_filter_params(
    id_cliente: int,
    *,
    start: date | None = None,
    end: date | None = None,
    date_axis: str = "auditoria",
) -> dict[str, Any]:
    """Query params equivalentes ao dashboard EO (população integral + source mode + filtros)."""
    params: dict[str, Any] = {
        "id_cliente": str(id_cliente),
        "date_axis": date_axis,
        "metric_mode": "complete",
    }
    if start is not None:
        params["start_date"] = start.isoformat()
    if end is not None:
        params["end_date"] = end.isoformat()
    return params


def filtered_auditados_for_client(
    id_cliente: int,
    *,
    start: date | None = None,
    end: date | None = None,
    date_axis: str = "auditoria",
) -> QuerySet:
    from apps.qualidade_operacional.services.analytics import filtered_auditados

    return filtered_auditados(
        build_eo_filter_params(id_cliente, start=start, end=end, date_axis=date_axis)
    )


def filtered_falhas_for_client(
    id_cliente: int,
    *,
    start: date | None = None,
    end: date | None = None,
    date_axis: str = "auditoria",
) -> QuerySet:
    from apps.qualidade_operacional.services.analytics import filtered_falhas

    return filtered_falhas(
        build_eo_filter_params(id_cliente, start=start, end=end, date_axis=date_axis)
    )
