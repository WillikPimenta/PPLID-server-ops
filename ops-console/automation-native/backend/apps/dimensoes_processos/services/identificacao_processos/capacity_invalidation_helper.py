"""Invalidação de snapshots Capacity após import Identificação dos Processos."""

from __future__ import annotations

from datetime import date

from apps.dimensoes_processos.services.derivacao_etapa.capacity_invalidation import (
    invalidate_capacity_snapshots_from_date,
)
from apps.dimensoes_processos.services.identificacao_processos.sync import ImportResult


def import_touched_capacity_sources(result: ImportResult) -> bool:
    sla = result.entities.get("projecao_sla")
    meta = result.entities.get("metas_etapa")
    sla_touched = bool(sla and (sla.created + sla.updated > 0))
    meta_touched = bool(meta and (meta.created + meta.updated > 0))
    return sla_touched or meta_touched


def invalidate_capacity_after_import(result: ImportResult) -> int:
    if not import_touched_capacity_sources(result):
        return 0
    candidates = [value for value in (result.min_projecao_sla_date, result.min_meta_etapa_date) if value]
    invalidate_from = min(candidates) if candidates else date.today()
    return invalidate_capacity_snapshots_from_date(invalidate_from)
