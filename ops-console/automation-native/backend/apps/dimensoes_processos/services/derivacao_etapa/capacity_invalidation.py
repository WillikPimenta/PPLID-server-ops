# -*- coding: utf-8 -*-
"""Invalidação de snapshots Capacity após alteração manual de derivação."""
from __future__ import annotations

from datetime import date

from apps.dimensoes_processos.models import CapacityDailySnapshot


def invalidate_capacity_snapshots_from_date(from_date: date) -> int:
    deleted, _ = CapacityDailySnapshot.objects.filter(calculation_date__gte=from_date).delete()
    return deleted
