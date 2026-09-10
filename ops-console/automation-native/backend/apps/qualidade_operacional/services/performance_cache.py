"""Cache versionado e single-flight das consultas pesadas de Qualidade."""
from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections.abc import Callable
from typing import Any, TypeVar

from django.conf import settings
from django.core.cache import cache

from apps.common.heavy_request_gate import BuildTimeoutError, HeavyRequestGate, QueueTimeoutError
from apps.common.versioned_cache import VersionedCache

T = TypeVar("T")

_CACHE = VersionedCache(
    "qualidade_operacional",
    ttl_setting="QUALIDADE_OPERACIONAL_CACHE_TTL",
    default_ttl=180,
)
_GATE = HeavyRequestGate(
    label="Qualidade Operacional",
    max_concurrent_setting="QUALIDADE_OPERACIONAL_MAX_CONCURRENT",
    queue_wait_ms_setting="QUALIDADE_OPERACIONAL_QUEUE_WAIT_MS",
    build_wait_s_setting="QUALIDADE_OPERACIONAL_BUILD_WAIT_S",
    default_max_concurrent=2,
    default_queue_wait_ms=15_000,
    default_build_wait_s=120,
)
_PAYLOAD_SCHEMA = (
    "v37-auditor-withdrawn-failures-leader-temporal-unattributed-"
    "intranet-analise-origem-official-metric-contestacao-id-tiebreak"
)


def busy_response_detail(err: str) -> tuple[dict, int, dict[str, str]]:
    """Espelha produtividade/SLA: fila cheia → 503 + Retry-After (não 500)."""
    if err == "queue_timeout":
        return (
            {
                "detail": (
                    "Servidor ocupado processando Qualidade Operacional. "
                    "Tente novamente em instantes."
                ),
                "retry_after": 3,
            },
            503,
            {"Retry-After": "3"},
        )
    return (
        {
            "detail": (
                "Timeout aguardando Qualidade Operacional. "
                "Tente novamente em instantes."
            ),
            "retry_after": 5,
        },
        503,
        {"Retry-After": "5"},
    )


def classify_gate_error(exc: BaseException) -> str | None:
    if isinstance(exc, QueueTimeoutError):
        return "queue_timeout"
    if isinstance(exc, BuildTimeoutError):
        return "build_timeout"
    return None


def bump_quality_cache_version() -> int:
    try:
        from apps.qualidade_operacional.services.contestacao_metrics import (
            clear_contestacao_tipo_cache,
        )

        clear_contestacao_tipo_cache()
    except Exception:  # noqa: BLE001
        pass
    try:
        from apps.qualidade_operacional.services.workforce_scope import (
            clear_responsibility_index_cache,
        )

        clear_responsibility_index_cache()
    except Exception:  # noqa: BLE001
        pass
    return _CACHE.bump_cache_version()


def _normalized_params(params) -> dict[str, Any]:
    if hasattr(params, "lists"):
        pairs = params.lists()
    elif hasattr(params, "items"):
        pairs = params.items()
    else:
        pairs = []

    normalized: dict[str, Any] = {}
    for key, raw in pairs:
        if key in {"_", "cache_bust"}:
            continue
        if isinstance(raw, (list, tuple, set)):
            values = sorted(str(value) for value in raw if value not in (None, ""))
            normalized[str(key)] = values
        elif raw not in (None, ""):
            normalized[str(key)] = str(raw)
    return normalized


def _stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def cache_key_parts(route: str, params) -> tuple[str, ...]:
    return route, _PAYLOAD_SCHEMA, _stable_hash(_normalized_params(params))


def get_cached(route: str, params):
    return _CACHE.get(*cache_key_parts(route, params))


def set_cached(route: str, params, payload, *, timeout: int | None = None) -> None:
    _CACHE.set(payload, *cache_key_parts(route, params), timeout=timeout)


def _distributed_build(
    parts: tuple[str, ...],
    builder: Callable[[], T],
    timeout: int | None,
) -> T:
    """Evita builds duplicados entre processos quando o cache e compartilhado."""
    effective_timeout = _CACHE.cache_ttl() if timeout is None else timeout
    if effective_timeout <= 0:
        return builder()

    # Congela a versao durante este build. Se uma importacao fizer bump no meio,
    # o resultado antigo nunca sera gravado na nova versao.
    payload_key = _CACHE.key(*parts)
    lock_digest = hashlib.sha256(payload_key.encode("utf-8")).hexdigest()
    lock_key = f"qualidade_operacional:build:{lock_digest}"
    token = uuid.uuid4().hex
    build_wait_s = max(1.0, _GATE._build_wait_s())  # noqa: SLF001
    queue_wait_s = max(0.1, _GATE._queue_wait_s())  # noqa: SLF001
    poll_s = max(
        0.01,
        int(getattr(settings, "QUALIDADE_OPERACIONAL_LOCK_POLL_MS", 50) or 50)
        / 1000.0,
    )
    # O lock sobrevive ao prazo dos followers; assim um lider lento nao apaga
    # acidentalmente um lock ja reassumido por outro processo.
    lock_ttl = int(math.ceil((build_wait_s * 2) + queue_wait_s + 30))
    deadline = time.monotonic() + build_wait_s

    while True:
        if cache.add(lock_key, token, timeout=lock_ttl):
            try:
                second_read = cache.get(payload_key)
                if second_read is not None:
                    return second_read
                payload = builder()
                cache.set(payload_key, payload, timeout=effective_timeout)
                return payload
            finally:
                # Compare antes de remover: nao apaga lock de outro lider.
                if cache.get(lock_key) == token:
                    cache.delete(lock_key)

        shared_result = cache.get(payload_key)
        if shared_result is not None:
            return shared_result
        if time.monotonic() >= deadline:
            raise BuildTimeoutError(
                "Timeout aguardando build distribuido de Qualidade Operacional."
            )
        time.sleep(poll_s)


def get_or_build(
    route: str,
    params,
    builder: Callable[[], T],
    *,
    timeout: int | None = None,
) -> T:
    cached = get_cached(route, params)
    if cached is not None:
        return cached

    parts = cache_key_parts(route, params)
    flight_key = ":".join(parts)

    return _GATE.run(
        flight_key,
        lambda: _distributed_build(parts, builder, timeout),
    )


def run_gated_export(kind: str, params, builder: Callable[[], T]) -> T:
    """Limita concorrência de exports CSV (sem cache do binário)."""
    parts = cache_key_parts(f"export-{kind}", params)
    flight_key = ":".join(parts)
    return _GATE.run(flight_key, builder)


def reset_qualidade_gate_for_tests() -> None:
    """Reseta semáforo/flights entre testes (espelha produtividade)."""
    _GATE._flights.clear()  # noqa: SLF001
    _GATE._semaphore = None  # noqa: SLF001
    _GATE._sem_capacity = None  # noqa: SLF001


def get_workforce_cached(params):
    return get_cached("workforce", _workforce_params(params))


def set_workforce_cached(params, payload) -> None:
    set_cached("workforce", _workforce_params(params), payload, timeout=300)


def _workforce_params(params) -> dict[str, Any]:
    normalized = _normalized_params(params)
    return {
        key: normalized[key]
        for key in (
            "start_date",
            "end_date",
            "lider",
            "equipe",
            "matricula",
            "workforce_only",
            "responsibility_scope",
            "date_axis",
        )
        if key in normalized
    }
