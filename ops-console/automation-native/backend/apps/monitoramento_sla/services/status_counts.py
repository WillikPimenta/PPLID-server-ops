# -*- coding: utf-8 -*-
"""Contagens de status leves — evita COUNT(*) em tabelas enormes a cada request."""
from __future__ import annotations

from django.conf import settings
from django.core.cache import cache

from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilDetalhe

CACHE_KEY = "monitoramento_sla:status_counts"


def _ttl_seconds() -> int:
    return int(getattr(settings, "MONITORAMENTO_SLA_STATUS_COUNTS_TTL", 300))


def invalidate_status_counts() -> None:
    cache.delete(CACHE_KEY)


def get_status_counts() -> dict[str, int]:
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    has_detalhe = SlaUtilDetalhe.objects.exists()
    has_consolidado = SlaUtilConsolidado.objects.exists()
    counts = {
        "detalhe_count": 1 if has_detalhe else 0,
        "consolidado_count": 1 if has_consolidado else 0,
        "abertos": 0,
    }
    if has_detalhe:
        counts["abertos"] = 1 if SlaUtilDetalhe.objects.filter(em_aberto=True).exists() else 0

    cache.set(CACHE_KEY, counts, _ttl_seconds())
    return counts
