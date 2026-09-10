# -*- coding: utf-8 -*-
"""Orquestra cache, gate e build da tabela-monitor."""
from __future__ import annotations

from datetime import date

from django.db.models import QuerySet

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.monitor_eventos.services.heavy_request_gate import (
    BuildTimeoutError,
    QueueTimeoutError,
    run_gated,
)
from apps.monitor_eventos.services.query_params import (
    apply_record_filters,
    filter_results_by_data_jornada,
)
from apps.monitor_eventos.services.tabela_monitor import build_tabela_monitor, jornada_from_recorded_at
from apps.monitor_eventos.services.tabela_monitor_cache import (
    cache_key_for_params,
    get_cached_payload,
    get_snapshot_payload,
    set_cached_payload,
    set_snapshot_payload,
)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def require_date_filter(params: dict) -> str | None:
    """Retorna mensagem de erro se faltar data; None se OK."""
    if params.get("data_jornada") or params.get("data"):
        return None
    return "Informe data_jornada ou data."


def _build_payload(qs: QuerySet[MonitorEventoRecord], params: dict) -> dict:
    results = build_tabela_monitor(qs)
    data_jornada = _parse_date(params.get("data_jornada"))
    if data_jornada:
        results = filter_results_by_data_jornada(results, data_jornada)
    return {"count": len(results), "results": results}


def resolve_tabela_monitor_payload(
    qs: QuerySet[MonitorEventoRecord],
    params: dict,
) -> tuple[dict | None, str | None, int | None]:
    """
    Retorna (payload, error_code, http_status_hint).
    error_code: 'queue_timeout' | 'build_timeout' | None
    """
    cached = get_cached_payload(params)
    if cached is not None:
        return cached, None, None

    data_jornada = _parse_date(params.get("data_jornada"))
    matricula = (params.get("matricula") or "").strip()
    if data_jornada and not matricula and not params.get("data"):
        snapshot = get_snapshot_payload(data_jornada)
        if snapshot is not None:
            set_cached_payload(params, snapshot)
            return snapshot, None, None

    key = cache_key_for_params(params)

    def builder() -> dict:
        # Re-check cache inside flight (outro follower pode ter preenchido).
        again = get_cached_payload(params)
        if again is not None:
            return again
        payload = _build_payload(qs, params)
        set_cached_payload(params, payload)
        return payload

    try:
        payload = run_gated(key, builder)
        return payload, None, None
    except QueueTimeoutError:
        return None, "queue_timeout", 503
    except BuildTimeoutError:
        return None, "build_timeout", 503


def warm_snapshots_after_sync() -> list[str]:
    """Pré-calcula snapshot por data_jornada presente nos registros (Fase 2)."""
    dates: set[date] = set()
    for recorded_at in MonitorEventoRecord.objects.values_list("data_evento", flat=True).iterator(
        chunk_size=2000
    ):
        if recorded_at is None:
            continue
        jornada = jornada_from_recorded_at(recorded_at)
        if jornada is not None:
            dates.add(jornada)

    warmed: list[str] = []
    for day in sorted(dates):
        params = {"data_jornada": day.isoformat(), "data": None, "matricula": None}
        qs = apply_record_filters(MonitorEventoRecord.objects.all(), params)
        payload = _build_payload(qs, params)
        set_snapshot_payload(day, payload)
        set_cached_payload(params, payload)
        warmed.append(day.isoformat())
    return warmed
