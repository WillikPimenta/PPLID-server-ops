# -*- coding: utf-8 -*-
"""Testes leves para validação de datas do slicer Power BI (virada de mês)."""
from __future__ import annotations

from datetime import date

import pytest

from app.bots.falhas_criticas.powerbi.filters import _dates_match


@pytest.mark.parametrize(
    ("current", "expected", "ok"),
    [
        ("01/07/2026", "01/07/2026", True),
        ("1/7/2026", "01/07/2026", True),
        ("31/06/2026", "01/07/2026", False),
        (None, "01/07/2026", False),
        ("", "01/07/2026", False),
    ],
)
def test_dates_match(current, expected, ok):
    assert _dates_match(current, expected) is ok


def test_dates_match_month_turn_start():
    """Dia 1 do mês deve bater exatamente após ajuste de trimestre + datas."""
    start = date(2026, 7, 1).strftime("%d/%m/%Y")
    assert _dates_match(start, start)
