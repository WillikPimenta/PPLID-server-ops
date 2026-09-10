# -*- coding: utf-8 -*-
"""Config runtime de sync bot→banco (singleton + fallback settings)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from django.conf import settings


@dataclass(frozen=True)
class BotDbSyncRuntimeValues:
    chunk_size: int
    batch_size: int
    stale_minutes: int
    drain_max_jobs: int
    queue_wait_s: int
    high_concurrency: int
    mid_concurrency: int
    low_concurrency: int


# Limites aceitos pela API / UI.
BOUNDS = {
    "chunk_size": (100, 20_000),
    "batch_size": (100, 20_000),
    "stale_minutes": (5, 240),
    "drain_max_jobs": (1, 50),
    "queue_wait_s": (30, 3600),
    "high_concurrency": (1, 10),
    "mid_concurrency": (1, 10),
    "low_concurrency": (1, 20),
}


def _defaults() -> BotDbSyncRuntimeValues:
    return BotDbSyncRuntimeValues(
        chunk_size=2000,
        batch_size=2000,
        stale_minutes=max(1, int(getattr(settings, "BOT_DB_SYNC_STALE_MINUTES", 45))),
        drain_max_jobs=10,
        queue_wait_s=max(1, int(getattr(settings, "BOT_DB_SYNC_QUEUE_WAIT_S", 900))),
        high_concurrency=1,
        mid_concurrency=1,
        low_concurrency=1,
    )


def clamp_runtime_value(field: str, value: int) -> int:
    lo, hi = BOUNDS[field]
    return max(lo, min(hi, int(value)))


def get_bot_db_sync_runtime_config() -> BotDbSyncRuntimeValues:
    """Lê o singleton; se a tabela ainda não existir / estiver vazia, usa defaults."""
    defaults = _defaults()
    try:
        from apps.common.models import BotDbSyncRuntimeConfig

        row = BotDbSyncRuntimeConfig.objects.filter(pk=BotDbSyncRuntimeConfig.SINGLETON_PK).first()
        if row is None:
            return defaults
        return BotDbSyncRuntimeValues(
            chunk_size=clamp_runtime_value("chunk_size", row.chunk_size),
            batch_size=clamp_runtime_value("batch_size", row.batch_size),
            stale_minutes=clamp_runtime_value("stale_minutes", row.stale_minutes),
            drain_max_jobs=clamp_runtime_value("drain_max_jobs", row.drain_max_jobs),
            queue_wait_s=clamp_runtime_value("queue_wait_s", row.queue_wait_s),
            high_concurrency=clamp_runtime_value(
                "high_concurrency", row.high_concurrency
            ),
            mid_concurrency=clamp_runtime_value("mid_concurrency", row.mid_concurrency),
            low_concurrency=clamp_runtime_value("low_concurrency", row.low_concurrency),
        )
    except Exception:
        # Migração ainda não aplicada, etc.
        return defaults


def update_bot_db_sync_runtime_config(**kwargs) -> BotDbSyncRuntimeValues:
    """Cria/atualiza o singleton com valores validados."""
    from apps.common.models import BotDbSyncRuntimeConfig

    current = get_bot_db_sync_runtime_config()
    data = asdict(current)
    for key in (
        "chunk_size",
        "batch_size",
        "stale_minutes",
        "drain_max_jobs",
        "queue_wait_s",
        "high_concurrency",
        "mid_concurrency",
        "low_concurrency",
    ):
        if key in kwargs and kwargs[key] is not None:
            data[key] = clamp_runtime_value(key, int(kwargs[key]))

    obj, _ = BotDbSyncRuntimeConfig.objects.update_or_create(
        pk=BotDbSyncRuntimeConfig.SINGLETON_PK,
        defaults=data,
    )
    return BotDbSyncRuntimeValues(
        chunk_size=obj.chunk_size,
        batch_size=obj.batch_size,
        stale_minutes=obj.stale_minutes,
        drain_max_jobs=obj.drain_max_jobs,
        queue_wait_s=obj.queue_wait_s,
        high_concurrency=obj.high_concurrency,
        mid_concurrency=obj.mid_concurrency,
        low_concurrency=obj.low_concurrency,
    )


def runtime_config_payload() -> dict:
    cfg = get_bot_db_sync_runtime_config()
    return {
        "chunk_size": cfg.chunk_size,
        "batch_size": cfg.batch_size,
        "stale_minutes": cfg.stale_minutes,
        "drain_max_jobs": cfg.drain_max_jobs,
        "queue_wait_s": cfg.queue_wait_s,
        "high_concurrency": cfg.high_concurrency,
        "mid_concurrency": cfg.mid_concurrency,
        "low_concurrency": cfg.low_concurrency,
        "bounds": {k: {"min": v[0], "max": v[1]} for k, v in BOUNDS.items()},
    }
