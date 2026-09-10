# -*- coding: utf-8 -*-
"""Limites de datas disponíveis no portal (todas as fontes)."""
from datetime import date

from django.db.models import Max, Min

from apps.falhas_criticas.models import Failure, Support, Training


def get_portal_data_bounds():
    """Min/max de datas considerando falhas, suporte e treinamentos."""
    f = Failure.objects.aggregate(mn=Min('data_analise'), mx=Max('data_analise'))
    s = Support.objects.aggregate(mx=Max('data'))
    t = Training.objects.aggregate(mx=Max('data_limite'))

    mins = [d for d in [f.get('mn')] if d]
    maxs = [d for d in [f.get('mx'), s.get('mx'), t.get('mx')] if d]

    min_date = min(mins) if mins else None
    max_date = max(maxs) if maxs else None
    today = date.today()
    # MTD como padrão: janela de 7 dias costuma ficar vazia após sync (poucas falhas recentes).
    default_start = today.replace(day=1)
    if min_date and default_start < min_date:
        default_start = min_date

    return {
        'min_date': min_date,
        'max_date': max_date,
        'date_picker_max': today,
        'default_end_date': today,
        'default_start_date': default_start,
    }
