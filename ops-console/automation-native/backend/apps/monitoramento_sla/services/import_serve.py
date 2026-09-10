# -*- coding: utf-8 -*-
"""Gate para preview/apply de import Parquet (evita empilhar scans pesados no DB)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from apps.common.heavy_request_gate import BuildTimeoutError, HeavyRequestGate, QueueTimeoutError

T = TypeVar("T")

_gate = HeavyRequestGate(
    label="monitoramento_sla_import",
    max_concurrent_setting="MONITORAMENTO_SLA_IMPORT_MAX_CONCURRENT",
    queue_wait_ms_setting="MONITORAMENTO_SLA_IMPORT_QUEUE_WAIT_MS",
    build_wait_s_setting="MONITORAMENTO_SLA_IMPORT_BUILD_WAIT_S",
    default_max_concurrent=1,
    default_queue_wait_ms=15_000,
    default_build_wait_s=120,
)


def reset_import_gate_for_tests() -> None:
    global _gate
    _gate = HeavyRequestGate(
        label="monitoramento_sla_import",
        max_concurrent_setting="MONITORAMENTO_SLA_IMPORT_MAX_CONCURRENT",
        queue_wait_ms_setting="MONITORAMENTO_SLA_IMPORT_QUEUE_WAIT_MS",
        build_wait_s_setting="MONITORAMENTO_SLA_IMPORT_BUILD_WAIT_S",
        default_max_concurrent=1,
        default_queue_wait_ms=15_000,
        default_build_wait_s=120,
    )


def run_gated_import(key: str, builder: Callable[[], T]) -> tuple[T | None, str | None]:
    try:
        return _gate.run(key, builder), None
    except QueueTimeoutError:
        return None, "queue_timeout"
    except BuildTimeoutError:
        return None, "build_timeout"


def busy_response_detail(err: str) -> tuple[dict, int, dict[str, str]]:
    if err == "queue_timeout":
        return (
            {
                "detail": "Servidor ocupado com importação SLA. Tente novamente em instantes.",
                "retry_after": 5,
            },
            503,
            {"Retry-After": "5"},
        )
    return (
        {
            "detail": "Timeout aguardando importação SLA. Tente novamente em instantes.",
            "retry_after": 10,
        },
        503,
        {"Retry-After": "10"},
    )
