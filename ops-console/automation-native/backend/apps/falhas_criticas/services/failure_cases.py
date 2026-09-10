# -*- coding: utf-8 -*-
"""Lista de casos (falhas) para drill-down do Diagnóstico."""
from __future__ import annotations

from django.db.models import Q

from apps.falhas_criticas.models import Failure
from apps.falhas_criticas.services.dataframes import apply_failure_filters
from apps.falhas_criticas.services.formatters import fmt_protocolo


def _param_str(params, key, default=''):
    val = (params or {}).get(key, default)
    if isinstance(val, (list, tuple)):
        val = val[0] if val else default
    return default if val is None else val


def _match_blank_or_value(qs, field: str, value: str):
    """Matrix trata vazio como 'Desconhecido' — espelha isso no filtro."""
    val = (value or '').strip()
    if not val:
        return qs
    if val.lower() == 'desconhecido':
        return qs.filter(Q(**{field: ''}) | Q(**{f'{field}__isnull': True}) | Q(**{f'{field}__iexact': 'Desconhecido'}))
    return qs.filter(**{f'{field}__iexact': val})


def build_failure_cases(user, query_params, *, limit: int = 50) -> dict:
    from apps.falhas_criticas.scoping import scoped_query_params

    params, scope = scoped_query_params(user, query_params)
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 50

    qs = apply_failure_filters(Failure.objects.select_related('agent'), params)

    cenario = _param_str(params, 'cenario')
    uf = _param_str(params, 'uf_documento') or _param_str(params, 'uf')
    tipo_doc = _param_str(params, 'tipo_documento')

    if cenario:
        qs = _match_blank_or_value(qs, 'cenario', cenario)
    if uf:
        qs = _match_blank_or_value(qs, 'uf_documento', uf)
    if tipo_doc:
        qs = _match_blank_or_value(qs, 'tipo_documento', tipo_doc)

    total = qs.count()
    rows = list(qs.order_by('-data_analise')[:limit])

    items = []
    for f in rows:
        items.append({
            'protocolo': fmt_protocolo(f.protocolo),
            'data_analise': f.data_analise,
            'cenario': f.cenario or '',
            'uf_documento': f.uf_documento or '',
            'tipo_documento': f.tipo_documento or '',
            'cliente': f.cliente or '',
            'localidade': f.localidade or '',
            'tipo_falha': f.tipo_falha or '',
            'matricula': f.agent.matricula_norm if f.agent_id else '',
            'nome_agente': (f.agent.name if f.agent_id else '') or '',
        })

    return {
        'count': total,
        'returned': len(items),
        'limit': limit,
        'filters': {
            'cenario': cenario or None,
            'uf_documento': uf or None,
            'tipo_documento': tipo_doc or None,
            'start_date': params.get('start_date'),
            'end_date': params.get('end_date'),
            'localidade': params.get('localidade') or 'Geral',
            'oficial': params.get('oficial'),
        },
        'items': items,
        'scope': scope,
    }
