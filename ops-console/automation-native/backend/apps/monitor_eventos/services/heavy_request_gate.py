# -*- coding: utf-8 -*-
"""Gate da tabela-monitor (pool separado via settings TABELA_MONITOR_*)."""
from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from apps.common.heavy_request_gate import (
    BuildTimeoutError,
    HeavyRequestGate,
    QueueTimeoutError,
)

T = TypeVar("T")

__all__ = [
    "BuildTimeoutError",
    "QueueTimeoutError",
    "HeavyRequestGate",
    "run_gated",
    "reset_gate_for_tests",
]

_gate = HeavyRequestGate(
    label="tabela-monitor",
    max_concurrent_setting="TABELA_MONITOR_MAX_CONCURRENT",
    queue_wait_ms_setting="TABELA_MONITOR_QUEUE_WAIT_MS",
    build_wait_s_setting="TABELA_MONITOR_BUILD_WAIT_S",
)


def run_gated(key: str, builder: Callable[[], T]) -> T:
    return _gate.run(key, builder)


def reset_gate_for_tests() -> None:
    """Recria o gate (apenas testes)."""
    global _gate
    _gate = HeavyRequestGate(
        label="tabela-monitor",
        max_concurrent_setting="TABELA_MONITOR_MAX_CONCURRENT",
        queue_wait_ms_setting="TABELA_MONITOR_QUEUE_WAIT_MS",
        build_wait_s_setting="TABELA_MONITOR_BUILD_WAIT_S",
    )
