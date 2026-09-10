# -*- coding: utf-8 -*-
"""Faixas de SLA (§15)."""
from __future__ import annotations

_BANDS: list[tuple[float, str]] = [
    (1, "1 minuto"),
    (2, "2 minutos"),
    (5, "5 minutos"),
    (10, "10 minutos"),
    (15, "15 minutos"),
    (20, "20 minutos"),
    (30, "30 minutos"),
    (60, "1 hora"),
    (90, "1 hora e 30 minutos"),
    (120, "2 horas"),
    (180, "3 horas"),
    (360, "6 horas"),
    (720, "12 horas"),
]


def classificar_faixa(sla_segundos: int | None) -> str | None:
    if sla_segundos is None:
        return None
    minutos = sla_segundos / 60.0
    for limit, label in _BANDS:
        if minutos <= limit:
            return label
    return "Mais de 12h"
