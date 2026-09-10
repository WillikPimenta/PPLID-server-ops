# -*- coding: utf-8 -*-
"""Faixas de criticidade de produtividade (absolutas, distintas do semáforo de meta)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Metodologia versionada — validar com lideranças antes de liberar em produção.
CRITICALITY_METHODOLOGY_VERSION = "v1-2026-07-28"
CRITICALITY_METHODOLOGY_EFFECTIVE_DATE = "2026-07-28"

CriticalityBand = Literal[
    "critical",
    "high",
    "medium",
    "low",
    "expected",
    "unclassifiable",
]

# Códigos legados ainda presentes em payloads/FE antigo.
LEGACY_SEVERITY_MAP = {
    "critical": "critical",
    "moderate": "high",
    "light": "medium",
    "ok": "expected",
}


@dataclass(frozen=True)
class CriticalityBandDef:
    code: CriticalityBand
    label: str
    description: str
    # Intervalo inclusivo no limite superior (exceto expected, aberto acima de 90).
    min_exclusive: float | None
    max_inclusive: float | None


CRITICALITY_BANDS: tuple[CriticalityBandDef, ...] = (
    CriticalityBandDef(
        code="critical",
        label="Crítico",
        description="Produtividade de 0% até 50%, inclusive.",
        min_exclusive=None,
        max_inclusive=50.0,
    ),
    CriticalityBandDef(
        code="high",
        label="Alto",
        description="Produtividade acima de 50% até 60%, inclusive.",
        min_exclusive=50.0,
        max_inclusive=60.0,
    ),
    CriticalityBandDef(
        code="medium",
        label="Médio",
        description="Produtividade acima de 60% até 70%, inclusive.",
        min_exclusive=60.0,
        max_inclusive=70.0,
    ),
    CriticalityBandDef(
        code="low",
        label="Baixo",
        description="Produtividade acima de 70% até 90%, inclusive.",
        min_exclusive=70.0,
        max_inclusive=90.0,
    ),
    CriticalityBandDef(
        code="expected",
        label="Dentro do esperado",
        description="Produtividade acima de 90%.",
        min_exclusive=90.0,
        max_inclusive=None,
    ),
)


def classify_criticality(pct: float | None) -> CriticalityBand:
    """Classifica produtividade absoluta em faixa de criticidade."""
    if pct is None:
        return "unclassifiable"
    try:
        value = float(pct)
    except (TypeError, ValueError):
        return "unclassifiable"
    if value <= 50.0:
        return "critical"
    if value <= 60.0:
        return "high"
    if value <= 70.0:
        return "medium"
    if value <= 90.0:
        return "low"
    return "expected"


def criticality_label(band: CriticalityBand | str | None) -> str:
    if not band or band == "unclassifiable":
        return "Não classificável"
    # Compatibilidade com códigos legados no FE.
    mapped = LEGACY_SEVERITY_MAP.get(band, band)
    for item in CRITICALITY_BANDS:
        if item.code == mapped:
            return item.label
    return "Não classificável"


def criticality_methodology_payload() -> dict:
    return {
        "methodology_version": CRITICALITY_METHODOLOGY_VERSION,
        "effective_date": CRITICALITY_METHODOLOGY_EFFECTIVE_DATE,
        "bands": [
            {
                "code": b.code,
                "label": b.label,
                "description": b.description,
                "min_exclusive": b.min_exclusive,
                "max_inclusive": b.max_inclusive,
            }
            for b in CRITICALITY_BANDS
        ],
        "unclassifiable_label": "Não classificável",
        "note": (
            "Criticidade é independente do semáforo de atingimento da meta. "
            "Limites exemplo sujeitos a validação formal com lideranças."
        ),
    }
