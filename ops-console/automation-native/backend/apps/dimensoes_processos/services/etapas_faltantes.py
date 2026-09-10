# -*- coding: utf-8 -*-
"""Etapas da produtividade sem DimEtapa ou sem MetaEtapa vigente (Megazord)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Count, Max
from django.utils import timezone

from apps.dimensoes_processos.models import MetaEtapa
from apps.dimensoes_processos.services.meta_etapa_lookup import (
    build_etapa_nome_index,
    normalize_etapa_nome,
)
from apps.produtividade.models import ProductivityRecord

MOTIVO_SEM_DIM = "sem_dim_etapa"
MOTIVO_SEM_META_VIGENTE = "sem_meta_vigente"
DEFAULT_DAYS = 90


def list_etapas_faltantes(*, days: int = DEFAULT_DAYS) -> list[dict[str, Any]]:
    """
    Distinct etapas HxH (últimos `days`) que:
    - não têm DimEtapa correspondente (nome normalizado), ou
    - têm DimEtapa mas nenhuma MetaEtapa vigente (data_fim IS NULL).
    """
    days = max(1, min(int(days or DEFAULT_DAYS), 3650))
    since = timezone.now() - timedelta(days=days)

    aggregates = (
        ProductivityRecord.objects.filter(recorded_at__gte=since)
        .exclude(etapa="")
        .values("etapa")
        .annotate(
            records_count=Count("id"),
            last_recorded_at=Max("recorded_at"),
        )
        .order_by("-records_count")
    )

    etapa_index = build_etapa_nome_index()
    vigentes = set(
        MetaEtapa.objects.filter(data_fim__isnull=True).values_list("etapa_id", flat=True)
    )

    gaps: list[dict[str, Any]] = []
    for row in aggregates:
        nome = (row.get("etapa") or "").strip()
        if not nome:
            continue
        key = normalize_etapa_nome(nome)
        if not key:
            continue
        id_etapa = etapa_index.get(key)
        if id_etapa is None:
            motivo = MOTIVO_SEM_DIM
        elif id_etapa not in vigentes:
            motivo = MOTIVO_SEM_META_VIGENTE
        else:
            continue

        last_at = row.get("last_recorded_at")
        gaps.append(
            {
                "etapa_nome": nome,
                "motivo": motivo,
                "id_etapa": id_etapa,
                "records_count": int(row.get("records_count") or 0),
                "last_recorded_at": last_at.isoformat() if last_at is not None else None,
            }
        )

    # Dedupar por nome normalizado (mantém o de maior volume)
    by_norm: dict[str, dict[str, Any]] = {}
    for item in gaps:
        nkey = normalize_etapa_nome(item["etapa_nome"])
        prev = by_norm.get(nkey)
        if prev is None or item["records_count"] > prev["records_count"]:
            by_norm[nkey] = item

    result = list(by_norm.values())
    result.sort(key=lambda x: (-x["records_count"], x["etapa_nome"].casefold()))
    return result
