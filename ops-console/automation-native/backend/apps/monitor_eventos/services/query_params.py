# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date

from django.db.models import QuerySet

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.monitor_eventos.services.tabela_monitor import jornada_window


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def parse_query_params(query_params) -> dict:
    return {
        "data_jornada": query_params.get("data_jornada") or None,
        "data": query_params.get("data") or None,
        "matricula": query_params.get("matricula") or None,
    }


def apply_record_filters(
    qs: QuerySet[MonitorEventoRecord],
    params: dict,
) -> QuerySet[MonitorEventoRecord]:
    data_jornada = _parse_date(params.get("data_jornada"))
    data = _parse_date(params.get("data"))
    matricula = params.get("matricula")

    if data_jornada:
        start, end = jornada_window(data_jornada)
        qs = qs.filter(data_evento__gte=start, data_evento__lte=end)
    elif data:
        qs = qs.filter(data=data)

    if matricula:
        qs = qs.filter(matricula_usuario__iexact=matricula.strip())

    return qs


def filter_results_by_data_jornada(
    results: list[dict],
    data_jornada: date,
) -> list[dict]:
    target = data_jornada.isoformat()
    return [row for row in results if row.get("data_jornada") == target]
