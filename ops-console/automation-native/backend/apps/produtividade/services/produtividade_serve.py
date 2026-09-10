# -*- coding: utf-8 -*-
"""Orquestra datas obrigatórias, range, gate e cache das rotas pesadas."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any, TypeVar

from apps.common.heavy_request_gate import BuildTimeoutError, HeavyRequestGate, QueueTimeoutError
from apps.produtividade.services.produtividade_cache import get_cached, set_cached

T = TypeVar("T")

MAX_RANGE_DAYS = 31

_gate = HeavyRequestGate(
    label="produtividade",
    max_concurrent_setting="PRODUTIVIDADE_MAX_CONCURRENT",
    queue_wait_ms_setting="PRODUTIVIDADE_QUEUE_WAIT_MS",
    build_wait_s_setting="PRODUTIVIDADE_BUILD_WAIT_S",
    default_max_concurrent=1,
    default_queue_wait_ms=15_000,
    default_build_wait_s=60,
)


def reset_produtividade_gate_for_tests() -> None:
    global _gate
    _gate = HeavyRequestGate(
        label="produtividade",
        max_concurrent_setting="PRODUTIVIDADE_MAX_CONCURRENT",
        queue_wait_ms_setting="PRODUTIVIDADE_QUEUE_WAIT_MS",
        build_wait_s_setting="PRODUTIVIDADE_BUILD_WAIT_S",
        default_max_concurrent=1,
        default_queue_wait_ms=15_000,
        default_build_wait_s=60,
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


def require_date_range(params: dict, *, max_days: int = MAX_RANGE_DAYS) -> str | None:
    """Exige start_date e end_date; valida range máximo. None se OK."""
    start = _parse_date(params.get("start_date"))
    end = _parse_date(params.get("end_date"))
    if start is None or end is None:
        return "Informe start_date e end_date."
    if end < start:
        return "end_date deve ser maior ou igual a start_date."
    span = (end - start).days + 1
    if span > max_days:
        return f"Intervalo máximo de {max_days} dias (recebido {span})."
    return None


def resolve_gated_payload(
    *,
    route: str,
    params: dict,
    builder: Callable[[], T],
    extra: dict | None = None,
    use_cache: bool = True,
) -> tuple[T | None, str | None, int | None]:
    """
    Retorna (payload, error_code, http_status_hint).
    error_code: 'queue_timeout' | 'build_timeout' | None
    """
    if use_cache:
        cached = get_cached(route, params, extra=extra)
        if cached is not None:
            return cached, None, None

    from apps.produtividade.services.produtividade_cache import cache_key_parts

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
        return _gate.run(key, _build), None, None
    except QueueTimeoutError:
        return None, "queue_timeout", 503
    except BuildTimeoutError:
        return None, "build_timeout", 503


def busy_response_detail(err: str) -> tuple[dict, int, dict[str, str]]:
    if err == "queue_timeout":
        return (
            {
                "detail": "Servidor ocupado processando produtividade. Tente novamente em instantes.",
                "retry_after": 3,
            },
            503,
            {"Retry-After": "3"},
        )
    return (
        {
            "detail": "Timeout aguardando produtividade. Tente novamente em instantes.",
            "retry_after": 5,
        },
        503,
        {"Retry-After": "5"},
    )
