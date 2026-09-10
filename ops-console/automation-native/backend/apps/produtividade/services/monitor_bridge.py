# -*- coding: utf-8 -*-
"""
Uma única montagem de tabela-monitor por request de produtividade.

1) Reusa LocMem/snapshot HTTP por data_jornada quando disponível.
2) Senão, faz um build completo e guarda em cache thread-local.

Importante: intervalos usam data de jornada (corte 05:15), não data civil —
registros de madrugada (00:00–05:14) pertencem à jornada do dia anterior.
"""
from __future__ import annotations

import threading
from datetime import date, timedelta
from typing import Any

from django.db.models import Count, Max, QuerySet
from django.db.models.functions import Lower

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.monitor_eventos.services.tabela_monitor import (
    build_tabela_monitor_from_records,
    jornada_from_recorded_at,
    jornada_window,
    records_queryset_to_dicts,
)
from apps.monitor_eventos.services.tabela_monitor_cache import (
    get_cached_payload,
    get_snapshot_payload,
)
from apps.produtividade.services.hourly_forecast import _matriculas_from_queryset

_local = threading.local()


def clear_monitor_bridge_cache_for_tests() -> None:
    _local.cache = {}
    _local.monitor_watermark = None


def _monitor_watermark() -> tuple[int, int]:
    """Fingerprint que invalida montagens antigas depois de uma nova carga."""
    state = MonitorEventoRecord.objects.aggregate(n=Count("id"), mx=Max("id"))
    return int(state["n"] or 0), int(state["mx"] or 0)


def _thread_cache() -> dict[str, list[dict[str, Any]]]:
    """Cache de montagem válido somente enquanto o Monitor não mudar."""
    watermark = _monitor_watermark()
    cached_watermark = getattr(_local, "monitor_watermark", None)
    if cached_watermark != watermark:
        _local.cache = {}
        _local.monitor_watermark = watermark
    cache = getattr(_local, "cache", None)
    if cache is None:
        cache = {}
        _local.cache = cache
    return cache


def _jornada_bounds(qs: QuerySet) -> tuple[date, date] | None:
    """Min/max data_jornada do filtro e/ou dos recorded_at (cutoff 05:15)."""
    filter_start = getattr(qs, "_pplid_jornada_start", None)
    filter_end = getattr(qs, "_pplid_jornada_end", None)
    if isinstance(filter_start, date) and isinstance(filter_end, date):
        if filter_start <= filter_end:
            return filter_start, filter_end

    jornadas: set[date] = set()
    for recorded_at in qs.values_list("recorded_at", flat=True).distinct():
        jornada = jornada_from_recorded_at(recorded_at)
        if jornada is not None:
            jornadas.add(jornada)
    if not jornadas:
        return None
    return min(jornadas), max(jornadas)


def _cache_key(qs: QuerySet) -> str | None:
    matriculas = sorted(_matriculas_from_queryset(qs))
    bounds = _jornada_bounds(qs)
    if not matriculas or not bounds:
        return None
    min_jornada, max_jornada = bounds
    return f"{','.join(matriculas)}|{min_jornada.isoformat()}|{max_jornada.isoformat()}"


def _days_inclusive(start: date, end: date) -> list[date]:
    days: list[date] = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    return days


def _try_http_cache_rows(min_jornada: date, max_jornada: date) -> list[dict[str, Any]] | None:
    """Junta resultados cacheados por data_jornada; None se algum dia faltar."""
    merged: list[dict[str, Any]] = []
    for day in _days_inclusive(min_jornada, max_jornada):
        params = {"data_jornada": day.isoformat(), "data": None, "matricula": None}
        payload = get_cached_payload(params) or get_snapshot_payload(day)
        if payload is None:
            return None
        merged.extend(payload.get("results") or [])
    return merged


def _build_from_records(
    qs: QuerySet,
    matriculas: list[str],
    min_jornada: date,
    max_jornada: date,
) -> list[dict[str, Any]]:
    monitor_qs = MonitorEventoRecord.objects.annotate(
        mat_lower=Lower("matricula_usuario")
    ).filter(mat_lower__in=matriculas)

    start, _ = jornada_window(min_jornada)
    _, end = jornada_window(max_jornada)
    monitor_qs = monitor_qs.filter(data_evento__gte=start, data_evento__lte=end)

    records = records_queryset_to_dicts(monitor_qs)
    if not records:
        return []
    return build_tabela_monitor_from_records(records)


def get_monitor_tabela_rows_for_qs(qs: QuerySet) -> list[dict[str, Any]]:
    """Linhas da tabela-monitor (hora + totais dia) para as jornadas do queryset."""
    key = _cache_key(qs)
    if key is None:
        return []

    cache = _thread_cache()
    if key in cache:
        return cache[key]

    matriculas = sorted(_matriculas_from_queryset(qs))
    bounds = _jornada_bounds(qs)
    assert bounds is not None
    min_jornada, max_jornada = bounds

    rows = _try_http_cache_rows(min_jornada, max_jornada)
    # Cache None (miss) ou [] vazio: monta a partir dos registros do monitor.
    if not rows:
        rows = _build_from_records(qs, matriculas, min_jornada, max_jornada)

    cache[key] = rows
    return rows
