# -*- coding: utf-8 -*-
"""Reload seguro de partições/tabelas em sync bot → banco.

Evita uma única transaction.atomic() com delete + insert de volume grande
(WAL longo, locks, pico de memória no PostgreSQL).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

from django.db import models, transaction
from django.db.models import QuerySet

DEFAULT_CHUNK_SIZE = 2000
DEFAULT_BATCH_SIZE = 2000

T = TypeVar("T", bound=models.Model)


def _resolve_sizes(chunk_size: int | None, batch_size: int | None) -> tuple[int, int]:
    from apps.common.bot_db_sync_runtime import get_bot_db_sync_runtime_config

    cfg = get_bot_db_sync_runtime_config()
    return (
        int(chunk_size if chunk_size is not None else cfg.chunk_size),
        int(batch_size if batch_size is not None else cfg.batch_size),
    )


def chunked_partition_reload(
    *,
    model: type[T],
    delete_queryset: QuerySet,
    records: Sequence[T],
    chunk_size: int | None = None,
    batch_size: int | None = None,
) -> int:
    """Apaga a partição/tabela e reinsere em chunks com atomics curtas.

    Delete usa _raw_delete (DELETE SQL direto, sem coletor ORM). Modelos com
    tabelas filhas CASCADE devem apagar os filhos antes (ex.: detalhado).

    Se falhar no meio, a partição fica incompleta — o log de sync marca falha
    e um re-sync com --force regrava.

    chunk_size/batch_size None → usa BotDbSyncRuntimeConfig (ou defaults).
    """
    chunk_size, batch_size = _resolve_sizes(chunk_size, batch_size)
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")

    with transaction.atomic():
        # _raw_delete: um DELETE SQL sem coletor ORM/cascade (tabelas flat sem filhos).
        delete_queryset._raw_delete(using=delete_queryset.db)

    if not records:
        return 0

    total = 0
    for i in range(0, len(records), chunk_size):
        chunk = list(records[i : i + chunk_size])
        with transaction.atomic():
            model.objects.bulk_create(chunk, batch_size=batch_size)
        total += len(chunk)
    return total
