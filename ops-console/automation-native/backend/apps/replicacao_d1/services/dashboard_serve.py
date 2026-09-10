# -*- coding: utf-8 -*-
"""Proteções de concorrência para o dashboard D-1."""
from __future__ import annotations

import hashlib
import threading
from typing import Any

from django.conf import settings

from apps.common.heavy_request_gate import BuildTimeoutError, HeavyRequestGate, QueueTimeoutError
from apps.replicacao_d1.services.dashboard_cache import (
    cache_key_parts,
    get_cached_by_parts,
    set_cached_by_parts,
)


_admission_lock = threading.Lock()
_admission_semaphore: threading.BoundedSemaphore | None = None
_admission_capacity: int | None = None

_gate = HeavyRequestGate(
    label="replicacao_d1_dashboard",
    max_concurrent_setting="REPLICACAO_D1_DASHBOARD_MAX_CONCURRENT",
    queue_wait_ms_setting="REPLICACAO_D1_DASHBOARD_QUEUE_WAIT_MS",
    build_wait_s_setting="REPLICACAO_D1_DASHBOARD_BUILD_WAIT_S",
    default_max_concurrent=1,
    default_queue_wait_ms=1_000,
    default_build_wait_s=30,
)


def _get_admission_semaphore() -> threading.BoundedSemaphore:
    global _admission_capacity, _admission_semaphore
    capacity = max(
        1,
        int(getattr(settings, "REPLICACAO_D1_DASHBOARD_MAX_INFLIGHT", 4) or 4),
    )
    with _admission_lock:
        if _admission_semaphore is None or _admission_capacity != capacity:
            _admission_semaphore = threading.BoundedSemaphore(capacity)
            _admission_capacity = capacity
        return _admission_semaphore


def _uncached_request_key(params: dict[str, Any], namespace: str) -> str:
    normalized = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
    digest = hashlib.sha256(repr(normalized).encode("utf-8")).hexdigest()
    return f"replicacao_d1_dashboard:{namespace}:{digest}"


def resolve_dashboard_payload(
    params: dict[str, Any],
    builder,
    *,
    namespace: str = "dashboard",
    cacheable: bool = True,
):
    normalized_params = {
        "_namespace": namespace,
        **{str(k): v for k, v in (params or {}).items()},
    }
    parts = cache_key_parts(normalized_params) if cacheable else None
    cached = get_cached_by_parts(parts) if parts is not None else None
    if cached is not None:
        return cached, None, "hit"

    request_key = ":".join(parts) if parts is not None else _uncached_request_key(params, namespace)

    admission = _get_admission_semaphore()
    if not admission.acquire(blocking=False):
        return None, "queue_timeout", "miss"

    def build_and_cache():
        # Evita reconstrução caso outro líder tenha preenchido o cache antes
        # deste request adquirir o único slot pesado.
        cached_after_queue = get_cached_by_parts(parts) if parts is not None else None
        if cached_after_queue is not None:
            return cached_after_queue
        payload = builder()
        if parts is not None:
            set_cached_by_parts(parts, payload)
        return payload

    try:
        try:
            return _gate.run(request_key, build_and_cache), None, "miss"
        except QueueTimeoutError:
            return None, "queue_timeout", "miss"
        except BuildTimeoutError:
            return None, "build_timeout", "miss"
    finally:
        admission.release()


def dashboard_busy_response(error: str) -> tuple[dict[str, Any], int, dict[str, str]]:
    if error == "queue_timeout":
        return (
            {"detail": "Dashboard D-1 ocupado. Tente novamente em instantes.", "retry_after": 3},
            503,
            {"Retry-After": "3"},
        )
    return (
        {"detail": "Timeout ao consolidar o dashboard D-1. Tente novamente.", "retry_after": 5},
        503,
        {"Retry-After": "5"},
    )
