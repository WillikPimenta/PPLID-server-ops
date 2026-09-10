# -*- coding: utf-8 -*-
"""Cache e invalidação da tabela-monitor (VersionedCache)."""
from __future__ import annotations

from datetime import date

from apps.common.versioned_cache import VersionedCache

_CACHE = VersionedCache(
    "tabela_monitor",
    ttl_setting="TABELA_MONITOR_CACHE_TTL",
    default_ttl=120,
)

CACHE_PREFIX = _CACHE.prefix
CACHE_VERSION_KEY = _CACHE.version_key


def cache_ttl() -> int:
    return _CACHE.cache_ttl()


def get_cache_version() -> int:
    return _CACHE.get_cache_version()


def bump_cache_version() -> int:
    return _CACHE.bump_cache_version()


def cache_key_for_params(params: dict) -> str:
    data_jornada = (params.get("data_jornada") or "").strip()
    data = (params.get("data") or "").strip()
    matricula = (params.get("matricula") or "").strip().lower()
    return _CACHE.key(data_jornada, data, matricula)


def snapshot_key_for_jornada(data_jornada: date | str) -> str:
    day = data_jornada.isoformat() if isinstance(data_jornada, date) else str(data_jornada)[:10]
    return _CACHE.key("snapshot", day)


def get_cached_payload(params: dict) -> dict | None:
    data_jornada = (params.get("data_jornada") or "").strip()
    data = (params.get("data") or "").strip()
    matricula = (params.get("matricula") or "").strip().lower()
    return _CACHE.get(data_jornada, data, matricula)


def set_cached_payload(params: dict, payload: dict) -> None:
    data_jornada = (params.get("data_jornada") or "").strip()
    data = (params.get("data") or "").strip()
    matricula = (params.get("matricula") or "").strip().lower()
    _CACHE.set(payload, data_jornada, data, matricula)


def get_snapshot_payload(data_jornada: date | str) -> dict | None:
    day = data_jornada.isoformat() if isinstance(data_jornada, date) else str(data_jornada)[:10]
    return _CACHE.get("snapshot", day)


def set_snapshot_payload(data_jornada: date | str, payload: dict) -> None:
    day = data_jornada.isoformat() if isinstance(data_jornada, date) else str(data_jornada)[:10]
    _CACHE.set_persistent(payload, "snapshot", day)


def invalidate_tabela_monitor_cache() -> int:
    return bump_cache_version()
