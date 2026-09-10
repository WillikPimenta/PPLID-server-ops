# -*- coding: utf-8 -*-
"""Resolve MetaEtapa.meta_dia → stage_goal da produtividade (fonte Megazord)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Iterable

from django.db.models import Q, QuerySet

from apps.dimensoes_processos.models import DimEtapa, MetaEtapa

log = logging.getLogger(__name__)


def normalize_etapa_nome(nome: str | None) -> str:
    """Strip + casefold + colapso de espaços (chave estável etapa HxH ↔ DimEtapa)."""
    return " ".join((nome or "").strip().casefold().split())


def meta_dia_as_stage_goal(meta: MetaEtapa | None) -> Decimal | None:
    if meta is None:
        return None
    try:
        value = Decimal(meta.meta_dia)
    except Exception:
        return None
    if value <= 0:
        return None
    return value


def build_etapa_nome_index() -> dict[str, int]:
    """Mapa nome normalizado → id_etapa (primeiro id se houver colisão de nome)."""
    index: dict[str, int] = {}
    collisions: set[str] = set()
    for etapa_id, nome in DimEtapa.objects.values_list("id_etapa", "nome"):
        key = normalize_etapa_nome(nome)
        if not key:
            continue
        if key in index and index[key] != etapa_id:
            collisions.add(key)
            continue
        index[key] = int(etapa_id)
    if collisions:
        log.warning(
            "DimEtapa: %s nome(s) normalizado(s) duplicado(s); usando primeiro id",
            len(collisions),
        )
    return index


@dataclass
class MetaEtapaLookupCache:
    """Cache por sync: evita N+1 em DimEtapa/MetaEtapa."""

    etapa_index: dict[str, int] = field(default_factory=dict)
    # (etapa_id, on_date_iso) -> MetaEtapa | None
    resolved: dict[tuple[int, str], MetaEtapa | None] = field(default_factory=dict)
    metas_by_etapa: dict[int, list[MetaEtapa]] = field(default_factory=dict)
    warned_missing: set[str] = field(default_factory=set)

    @classmethod
    def build(
        cls,
        *,
        etapa_nomes: Iterable[str] | None = None,
        date_min: date | None = None,
        date_max: date | None = None,
    ) -> "MetaEtapaLookupCache":
        cache = cls(etapa_index=build_etapa_nome_index())
        etapa_ids: set[int] = set()
        if etapa_nomes is not None:
            for nome in etapa_nomes:
                eid = cache.etapa_index.get(normalize_etapa_nome(nome))
                if eid is not None:
                    etapa_ids.add(eid)
        else:
            etapa_ids = set(cache.etapa_index.values())

        if not etapa_ids:
            return cache

        qs: QuerySet[MetaEtapa] = MetaEtapa.objects.filter(etapa_id__in=etapa_ids)
        if date_min is not None:
            # Pode ter iniciado antes do range e ainda cobrir date_min
            qs = qs.filter(Q(data_fim__isnull=True) | Q(data_fim__gte=date_min))
        if date_max is not None:
            qs = qs.filter(data_inicio__lte=date_max)

        for meta in qs.select_related("etapa", "servico").order_by(
            "etapa_id", "-data_inicio", "servico_id"
        ):
            cache.metas_by_etapa.setdefault(int(meta.etapa_id), []).append(meta)
        return cache


def _covers_date(meta: MetaEtapa, on_date: date) -> bool:
    if meta.data_inicio > on_date:
        return False
    if meta.data_fim is not None and meta.data_fim < on_date:
        return False
    return True


def _sort_candidates(
    candidates: list[MetaEtapa],
    *,
    prefer_vigente: bool,
) -> list[MetaEtapa]:
    def key(m: MetaEtapa):
        servico_rank = 0 if m.servico_id is None else 1
        vigente_rank = 0 if (prefer_vigente and m.data_fim is None) else 1
        # -data_inicio → maior primeiro (invertemos com negação via timestamp)
        return (servico_rank, vigente_rank, -m.data_inicio.toordinal())

    return sorted(candidates, key=key)


def resolve_meta_etapa(
    *,
    etapa_nome: str | None,
    on_date: date,
    today: date | None = None,
    cache: MetaEtapaLookupCache | None = None,
) -> MetaEtapa | None:
    """
    Resolve MetaEtapa vigente no dia `on_date` para o nome da etapa.

    - Intervalo: data_inicio <= on_date <= data_fim (ou data_fim null).
    - Se on_date == hoje: prioriza ciclo vigente (data_fim null).
    - Prefere servico_id IS NULL.
    """
    key = normalize_etapa_nome(etapa_nome)
    if not key:
        return None

    today = today or date.today()
    prefer_vigente = on_date == today

    if cache is None:
        cache = MetaEtapaLookupCache.build(etapa_nomes=[etapa_nome or ""])

    etapa_id = cache.etapa_index.get(key)
    if etapa_id is None:
        if key not in cache.warned_missing:
            cache.warned_missing.add(key)
            log.warning(
                "MetaEtapa: etapa HxH sem DimEtapa correspondente | etapa=%r",
                (etapa_nome or "").strip(),
            )
        return None

    cache_key = (etapa_id, on_date.isoformat())
    if cache_key in cache.resolved:
        return cache.resolved[cache_key]

    candidates = [
        m for m in cache.metas_by_etapa.get(etapa_id, []) if _covers_date(m, on_date)
    ]
    if not candidates and not cache.metas_by_etapa.get(etapa_id):
        # Cache sem prefetch dessa etapa/data — query pontual
        qs = MetaEtapa.objects.filter(
            etapa_id=etapa_id,
            data_inicio__lte=on_date,
        ).filter(Q(data_fim__isnull=True) | Q(data_fim__gte=on_date))
        candidates = list(qs.select_related("etapa", "servico"))

    if not candidates:
        cache.resolved[cache_key] = None
        if key not in cache.warned_missing:
            cache.warned_missing.add(key)
            log.warning(
                "MetaEtapa: sem meta no intervalo | etapa=%r on_date=%s",
                (etapa_nome or "").strip(),
                on_date.isoformat(),
            )
        return None

    ordered = _sort_candidates(candidates, prefer_vigente=prefer_vigente)
    if len(ordered) > 1:
        log.warning(
            "MetaEtapa: %s candidatos para etapa_id=%s on_date=%s; usando id=%s meta_dia=%s",
            len(ordered),
            etapa_id,
            on_date.isoformat(),
            ordered[0].pk,
            ordered[0].meta_dia,
        )
    chosen = ordered[0]
    if chosen.servico_id is not None:
        log.warning(
            "MetaEtapa: usando meta com serviço (id_servico=%s) | etapa_id=%s on_date=%s",
            chosen.servico_id,
            etapa_id,
            on_date.isoformat(),
        )
    cache.resolved[cache_key] = chosen
    return chosen


def resolve_stage_goal(
    *,
    etapa_nome: str | None,
    on_date: date,
    today: date | None = None,
    cache: MetaEtapaLookupCache | None = None,
) -> Decimal | None:
    """Só Megazord (MetaEtapa.meta_dia); sem meta válida → None."""
    meta = resolve_meta_etapa(
        etapa_nome=etapa_nome,
        on_date=on_date,
        today=today,
        cache=cache,
    )
    return meta_dia_as_stage_goal(meta)
