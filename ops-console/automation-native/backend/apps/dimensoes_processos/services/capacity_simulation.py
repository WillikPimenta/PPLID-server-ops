"""Comparacao batch da simulacao de Capacity com contexto compartilhado."""

from __future__ import annotations

import hashlib
import json
from datetime import date

from django.core.cache import cache

from apps.dimensoes_processos.services.capacity import (
    CAPACITY_PERIOD_METRIC_VERSION,
    calculate_capacity_period,
)
from apps.dimensoes_processos.services.capacity_fingerprint import (
    current_capacity_source_fingerprint,
)


SIMULATION_CACHE_TTL_SECONDS = 120


def calculate_capacity_simulation_comparison(
    date_from: date,
    date_to: date,
    *,
    manual_overrides: dict | None,
) -> dict:
    """Calcula baseline e efeitos cumulativos sem reler as mesmas fontes."""

    overrides = manual_overrides or {}
    source_fingerprint = current_capacity_source_fingerprint(
        date_from,
        date_to,
        scenario_id="planejamento",
    )
    overrides_hash = hashlib.sha256(
        json.dumps(
            overrides,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    cache_key = (
        f"capacity:simulation:{CAPACITY_PERIOD_METRIC_VERSION}:"
        f"{source_fingerprint}:{date_from}:{date_to}:{overrides_hash}"
    )
    cached = cache.get(cache_key)
    if isinstance(cached, dict):
        return {**cached, "cache": {"hit": True, "ttl_seconds": SIMULATION_CACHE_TTL_SECONDS}}
    variants = {
        "baseline": {},
        "volume": {"workflows": overrides.get("workflows") or []},
        "derivation": {
            "workflows": overrides.get("workflows") or [],
            "derivations": overrides.get("derivations") or [],
        },
        "full": {
            "workflows": overrides.get("workflows") or [],
            "derivations": overrides.get("derivations") or [],
            "metas": overrides.get("metas") or [],
        },
    }
    shared_context: dict = {}
    results: dict[str, dict] = {}
    resolved_by_key: dict[tuple, dict] = {}
    for name, variant in variants.items():
        normalized = tuple(
            (collection, repr(variant.get(collection) or []))
            for collection in ("workflows", "derivations", "metas")
        )
        if normalized not in resolved_by_key:
            resolved_by_key[normalized] = calculate_capacity_period(
                date_from,
                date_to,
                scenario_id="manual",
                manual_overrides=variant,
                force_live=bool(variant),
                calculation_cache=shared_context,
                expected_source_fingerprint=source_fingerprint,
            )
        results[name] = resolved_by_key[normalized]
    payload = {
        "ok": True,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "scenario": "manual",
        "results": results,
        "source_fingerprint": source_fingerprint,
        "overrides_hash": overrides_hash,
        "cache": {"hit": False, "ttl_seconds": SIMULATION_CACHE_TTL_SECONDS},
    }
    cache.set(cache_key, payload, timeout=SIMULATION_CACHE_TTL_SECONDS)
    return payload
