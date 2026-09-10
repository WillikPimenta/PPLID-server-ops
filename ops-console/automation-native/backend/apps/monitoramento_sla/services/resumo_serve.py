# -*- coding: utf-8 -*-
"""Gate, range de datas e cache do Resumo SLA."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any, TypeVar

from apps.common.heavy_request_gate import BuildTimeoutError, HeavyRequestGate, QueueTimeoutError
from apps.monitoramento_sla.services.resumo_cache import get_cached, set_cached, cache_key_parts

T = TypeVar("T")

MAX_RANGE_DAYS = 366

_gate = HeavyRequestGate(
    label="monitoramento_sla_resumo",
    max_concurrent_setting="MONITORAMENTO_SLA_MAX_CONCURRENT",
    queue_wait_ms_setting="MONITORAMENTO_SLA_QUEUE_WAIT_MS",
    build_wait_s_setting="MONITORAMENTO_SLA_BUILD_WAIT_S",
    default_max_concurrent=1,
    default_queue_wait_ms=15_000,
    default_build_wait_s=90,
)


def reset_resumo_gate_for_tests() -> None:
    global _gate
    _gate = HeavyRequestGate(
        label="monitoramento_sla_resumo",
        max_concurrent_setting="MONITORAMENTO_SLA_MAX_CONCURRENT",
        queue_wait_ms_setting="MONITORAMENTO_SLA_QUEUE_WAIT_MS",
        build_wait_s_setting="MONITORAMENTO_SLA_BUILD_WAIT_S",
        default_max_concurrent=1,
        default_queue_wait_ms=15_000,
        default_build_wait_s=90,
    )


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def resolve_date_range(params: dict) -> tuple[date | None, date | None, str | None]:
    """
    Aceita start_date+end_date ou year.
    Retorna (start, end, error_message).
    """
    year_raw = params.get("year")
    if year_raw and not (params.get("start_date") and params.get("end_date")):
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            return None, None, "year inválido."
        if year < 2000 or year > 2100:
            return None, None, "year fora da faixa permitida."
        return date(year, 1, 1), date(year, 12, 31), None

    start = _parse_date(params.get("start_date"))
    end = _parse_date(params.get("end_date"))
    if start is None or end is None:
        return None, None, "Informe start_date e end_date (ou year)."
    if end < start:
        return None, None, "end_date deve ser maior ou igual a start_date."
    span = (end - start).days + 1
    if span > MAX_RANGE_DAYS:
        return None, None, f"Intervalo máximo de {MAX_RANGE_DAYS} dias (recebido {span})."
    return start, end, None


def resolve_gated_payload(
    *,
    route: str,
    params: dict,
    builder: Callable[[], T],
    extra: dict | None = None,
    use_cache: bool = True,
) -> tuple[T | None, str | None]:
    if use_cache:
        cached = get_cached(route, params, extra=extra)
        if cached is not None:
            return cached, None

    key = ":".join(cache_key_parts(route, params, extra=extra))

    def _build() -> T:
        if use_cache:
            again = get_cached(route, params, extra=extra)
            if again is not None:
                return again
        payload = builder()
        if use_cache:
            set_cached(route, params, payload, extra=extra)
        return payload

    try:
        return _gate.run(key, _build), None
    except QueueTimeoutError:
        return None, "queue_timeout"
    except BuildTimeoutError:
        return None, "build_timeout"


def busy_response_detail(err: str) -> tuple[dict, int, dict[str, str]]:
    if err == "queue_timeout":
        return (
            {
                "detail": "Servidor ocupado processando Resumo SLA. Tente novamente em instantes.",
                "retry_after": 3,
            },
            503,
            {"Retry-After": "3"},
        )
    return (
        {
            "detail": "Timeout aguardando Resumo SLA. Tente novamente em instantes.",
            "retry_after": 5,
        },
        503,
        {"Retry-After": "5"},
    )
