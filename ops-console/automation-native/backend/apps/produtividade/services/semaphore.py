# -*- coding: utf-8 -*-
"""Semáforo de atingimento da meta (distinto das faixas de criticidade)."""
from __future__ import annotations

from typing import Literal

MetaPerformanceTone = Literal["ok", "near", "below", "neutral"]

# Amarelo quando realizado >= NEAR_RATIO * meta e < meta.
NEAR_RATIO = 0.95


def meta_performance_tone(
    realized_pct: float | None,
    applied_meta: float | None,
    *,
    higher_is_better: bool = True,
) -> MetaPerformanceTone:
    """
    Semáforo comparativo com meta.

    higher_is_better=True (padrão): verde >= meta; amarelo >= 95% da meta; vermelho abaixo.
    higher_is_better=False (menor é melhor): verde <= meta; amarelo <= meta/0.95; vermelho acima.
    Neutro quando realizado ou meta ausentes.
    """
    if realized_pct is None or applied_meta is None:
        return "neutral"
    try:
        realized = float(realized_pct)
        meta = float(applied_meta)
    except (TypeError, ValueError):
        return "neutral"
    if meta <= 0:
        return "neutral"

    if higher_is_better:
        if realized >= meta:
            return "ok"
        if realized >= meta * NEAR_RATIO:
            return "near"
        return "below"

    # Menor é melhor: meta é o teto desejado.
    if realized <= meta:
        return "ok"
    # Faixa amarela: até meta / NEAR_RATIO (simétrico ao 95%).
    if realized <= meta / NEAR_RATIO:
        return "near"
    return "below"


def meta_tone_label(tone: MetaPerformanceTone | str | None) -> str:
    if tone == "ok":
        return "Na meta"
    if tone == "near":
        return "Próximo da meta"
    if tone == "below":
        return "Abaixo da meta"
    return "Meta não identificada"
