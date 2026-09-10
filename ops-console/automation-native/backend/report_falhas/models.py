# -*- coding: utf-8 -*-

"""Data models for report_falhas pipeline."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ReportContext:
    # Dates
    today: Any
    cur_start: Any
    cur_end: Any
    prev_equal_start: Any
    prev_equal_end: Any

    # Volumes
    total_atual: int
    total_prev_equal: int

    # Pairs for charts
    pares_mes: Optional[list] = None
    pares_diario: Optional[list] = None
    pares_facil: Optional[list] = None

    # Insights
    top3_cenarios: Optional[list] = None
