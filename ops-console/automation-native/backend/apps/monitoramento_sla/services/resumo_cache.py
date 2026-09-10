# -*- coding: utf-8 -*-
"""Cache versionado do painel Resumo (Monitoramento SLA)."""
from __future__ import annotations

import hashlib
import json

from apps.common.versioned_cache import VersionedCache

_CACHE = VersionedCache(
    "monitoramento_sla_resumo",
    ttl_setting="MONITORAMENTO_SLA_CACHE_TTL",
    default_ttl=120,
)

_PAYLOAD_SCHEMA = "v3-resumo-heatmap"


def bump_resumo_cache_version() -> int:
    from apps.monitoramento_sla.services.status_counts import invalidate_status_counts

    invalidate_status_counts()
    return _CACHE.bump_cache_version()


def _stable_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def cache_key_parts(route: str, params: dict, *, extra: dict | None = None) -> tuple[str, ...]:
    body = {k: params.get(k) for k in sorted(params.keys())}
    if extra:
        body.update(extra)
    return (route, _PAYLOAD_SCHEMA, _stable_hash(body))


def get_cached(route: str, params: dict, *, extra: dict | None = None):
    return _CACHE.get(*cache_key_parts(route, params, extra=extra))


def set_cached(route: str, params: dict, payload, *, extra: dict | None = None) -> None:
    _CACHE.set(payload, *cache_key_parts(route, params, extra=extra))
