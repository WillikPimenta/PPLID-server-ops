# -*- coding: utf-8 -*-
"""Filtro opcional de prazo (120 dias) aplicado somente em falhas."""
from __future__ import annotations

from datetime import timedelta

from django.db.models import F, QuerySet


DEADLINE_DAYS = 120


def parse_bool(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def prazo_filter_enabled(params) -> bool:
    if not hasattr(params, "get"):
        return False
    return parse_bool(params.get("exclude_out_of_deadline"))


def apply_falhas_prazo_filter(qs: QuerySet, params) -> QuerySet:
    """Exclui falhas com prazo conhecido >= 120 dias (auditoria - origem).

    Datas ausentes ou invertidas permanecem no recorte (filtro não se aplica).
    """
    if not prazo_filter_enabled(params):
        return qs
    return qs.exclude(
        data__isnull=False,
        data_analise__isnull=False,
        data__gte=F("data_analise") + timedelta(days=DEADLINE_DAYS),
    )
