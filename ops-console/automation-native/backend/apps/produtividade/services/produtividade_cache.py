# -*- coding: utf-8 -*-
"""Cache LocMem versionado das rotas pesadas de produtividade."""
from __future__ import annotations

import hashlib
import json

from apps.common.versioned_cache import VersionedCache

_CACHE = VersionedCache(
    "produtividade",
    ttl_setting="PRODUTIVIDADE_CACHE_TTL",
    default_ttl=120,
)


def bump_produtividade_cache_version() -> int:
    return _CACHE.bump_cache_version()


def invalidate_produtividade_cache() -> int:
    return bump_produtividade_cache_version()


def _stable_hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _data_watermark() -> str:
    """Marca o estado do banco — LocMem do runserver não vê bump de outro processo (drain).

    Inclui monitor de eventos: pace/aproveitamento depende do tempo logado.
    """
    from django.db.models import Count, Max

    from apps.monitor_eventos.models import MonitorEventoRecord
    from apps.produtividade.models import ProductivityRecord

    prod = ProductivityRecord.objects.aggregate(n=Count("id"), mx=Max("id"))
    mon = MonitorEventoRecord.objects.aggregate(n=Count("id"), mx=Max("id"))
    return (
        f"p{prod['n'] or 0}-{prod['mx'] or 0}"
        f"|m{mon['n'] or 0}-{mon['mx'] or 0}"
    )


# Bump ao mudar shape/cálculo das responses cacheadas (ex.: % volume / potencial por hora).
_PAYLOAD_SCHEMA = "v29-potential-time-over-meta"


def cache_key_parts(route: str, params: dict, *, extra: dict | None = None) -> tuple[str, ...]:
    body = {k: params.get(k) for k in sorted(params.keys())}
    if extra:
        body.update(extra)
    return (route, _PAYLOAD_SCHEMA, _data_watermark(), _stable_hash(body))


def get_cached(route: str, params: dict, *, extra: dict | None = None):
    return _CACHE.get(*cache_key_parts(route, params, extra=extra))


def set_cached(route: str, params: dict, payload, *, extra: dict | None = None) -> None:
    _CACHE.set(payload, *cache_key_parts(route, params, extra=extra))
