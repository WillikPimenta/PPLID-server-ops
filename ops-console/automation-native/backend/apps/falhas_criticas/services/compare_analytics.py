# -*- coding: utf-8 -*-
"""Comparativo executivo Brasília × São Carlos."""
from django.db.models import Count

from apps.falhas_criticas.models import Failure, Support
from apps.falhas_criticas.services.analytics import build_executive_payload
from apps.falhas_criticas.services.dataframes import (
    apply_failure_filters,
    apply_support_filters,
    load_period_frames,
    supports_qs_to_legacy_df,
)
from apps.falhas_criticas.services.narrative import build_comparativo_narrative
from apps.falhas_criticas.services.support_analytics import build_support_full_payload
from apps.falhas_criticas.services.training_analytics import _kpis_from_qs, _training_qs_filtered
from apps.falhas_criticas.services.formatters import round_num
from apps.falhas_criticas.utils_metrics import pct

def _failure_block(user, params, localidade):
    p = dict(params)
    p['localidade'] = localidade
    df_all, df_cur, df_prev, _, _ = load_period_frames(user, p)
    if df_cur.empty:
        return {'total': 0, 'delta_pct': '—', 'reinc_pct': 0, 'novos_pct': 0, 'top_cenario': {}}

    exec_p = build_executive_payload(df_all, df_cur, df_prev, p)
    le = exec_p.get('leitura_executiva', {})
    resumo = exec_p.get('resumo_30s', {})
    total = resumo.get('falhas', {}).get('valor') or len(df_cur)
    return {
        'total': total,
        'delta_pct': le.get('variacao_perc', '—'),
        'reinc_total': le.get('reinc_total', 0),
        'reinc_pct': le.get('reinc_pct'),
        'novos_count': le.get('novos_mes_count', 0),
        'novos_pct': le.get('novos_pct'),
        'top_cenario': resumo.get('top_cenario', {}),
    }


def _support_block(params, localidade):
    p = dict(params)
    p['localidade'] = localidade
    qs = Support.objects.select_related('agent')
    qs_cur = apply_support_filters(qs, p)
    p_scope = dict(p)
    p_scope.pop('start_date', None)
    p_scope.pop('end_date', None)
    qs_all = apply_support_filters(qs, p_scope)
    df_cur = supports_qs_to_legacy_df(qs_cur)
    df_all = supports_qs_to_legacy_df(qs_all)
    supl = build_support_full_payload(df_cur, p, df_all)
    total = int(supl.get('total') or 0)
    return {
        'total': total,
        'nc_oficial_pct': None if total == 0 else supl.get('nc_oficial_pct'),
        'nc_disponivel': total > 0,
        'regra3_count': 0 if total == 0 else (supl.get('regra3_count', 0) or 0),
        'regra3_pct': None if total == 0 else supl.get('regra3_pct'),
        'top_agente_r3': (supl.get('top_agentes_r3') or [{}])[0] if total and supl.get('top_agentes_r3') else {},
    }


def _training_block(params, localidade):
    p = dict(params)
    p['localidade'] = localidade
    qs = _training_qs_filtered(p)
    k = _kpis_from_qs(qs)
    return {
        'total': k.get('total', 0),
        'previstos': k.get('previstos', 0),
        'vencidos': k.get('vencidos', 0),
        'assinados': k.get('assinados', 0),
        'a_vencer_7d': k.get('a_vencer_7d', 0),
    }


def _drivers(params, user):
    drivers = []
    p_bsb = dict(params)
    p_bsb['localidade'] = 'Brasília'
    p_sc = dict(params)
    p_sc['localidade'] = 'São Carlos'

    for label, col in [('Cenário Unificado', 'cenario'), ('Workflow', 'workflow')]:
        qs_b = Failure.objects.all()
        qs_s = Failure.objects.all()
        qs_b = apply_failure_filters(qs_b, p_bsb)
        qs_s = apply_failure_filters(qs_s, p_sc)
        if col == 'cenario':
            vb = qs_b.values(col).annotate(q=Count('id')).order_by('-q')[:5]
            vs = {r[col] or '—': r['q'] for r in qs_s.values(col).annotate(q=Count('id'))}
        else:
            vb = qs_b.exclude(**{f'{col}__exact': ''}).values(col).annotate(q=Count('id')).order_by('-q')[:5]
            vs = {r[col] or '—': r['q'] for r in qs_s.exclude(**{f'{col}__exact': ''}).values(col).annotate(q=Count('id'))}
        for row in vb:
            nome = row[col] or '—'
            bq = row['q']
            sq = vs.get(nome, 0)
            delta = bq - sq
            if delta != 0:
                drivers.append({
                    'label': nome,
                    'tipo': label,
                    'bsb_qtd': bq,
                    'sc_qtd': sq,
                    'delta': delta,
                    'delta_pct': pct(abs(delta), max(sq, 1)),
                })
    drivers.sort(key=lambda x: abs(x['delta']), reverse=True)
    return drivers[:5]


def _build_dimensions(bsb, sc):
    """Linhas de comparação estruturadas (rótulo, valores, delta, sentido)."""
    def _row(grupo, label, b, s, fmt='int', menor_melhor=True, unidade=''):
        # Preserve None for unavailable metrics (e.g. NC sem base).
        b_raw, s_raw = b, s
        b_num = b if isinstance(b, (int, float)) else (0 if b is None and fmt != 'pct' else b)
        s_num = s if isinstance(s, (int, float)) else (0 if s is None and fmt != 'pct' else s)
        if fmt == 'pct' and (b_raw is None or s_raw is None):
            return {
                'grupo': grupo,
                'label': label,
                'bsb': b_raw,
                'sc': s_raw,
                'delta': None,
                'fmt': fmt,
                'unidade': unidade,
                'menor_melhor': menor_melhor,
                'pior': None,
                'maior': 1,
                'sem_dados': True,
            }
        b = b_num if b_num is not None else 0
        s = s_num if s_num is not None else 0
        delta = round_num(b - s, 1) if isinstance(b, (int, float)) and isinstance(s, (int, float)) else None
        pior = None
        if delta is not None and delta != 0:
            if menor_melhor:
                pior = 'bsb' if b > s else 'sc'
            else:
                pior = 'bsb' if b < s else 'sc'
        return {
            'grupo': grupo,
            'label': label,
            'bsb': b,
            'sc': s,
            'delta': delta,
            'fmt': fmt,
            'unidade': unidade,
            'menor_melhor': menor_melhor,
            'pior': pior,
            'maior': max(abs(b) if isinstance(b, (int, float)) else 0,
                         abs(s) if isinstance(s, (int, float)) else 0) or 1,
        }

    bf, sf = bsb['falhas'], sc['falhas']
    bsup, ssup = bsb['suporte'], sc['suporte']
    bt, st = bsb['treinamentos'], sc['treinamentos']

    return [
        _row('Falhas', 'Total de falhas', bf.get('total'), sf.get('total'), 'int', True),
        _row('Falhas', 'Reincidência', bf.get('reinc_pct'), sf.get('reinc_pct'), 'pct', True, '%'),
        _row('Falhas', 'Novos no período', bf.get('novos_pct'), sf.get('novos_pct'), 'pct', True, '%'),
        _row('Suporte TEAMS', 'Volume de solicitações', bsup.get('total'), ssup.get('total'), 'int', False),
        _row('Suporte TEAMS', 'Não conformidade', bsup.get('nc_oficial_pct'), ssup.get('nc_oficial_pct'), 'pct', True, '%'),
        _row('Suporte TEAMS', 'Regra 3 (críticos)', bsup.get('regra3_pct'), ssup.get('regra3_pct'), 'pct', True, '%'),
        _row('Capacitação', 'Treinamentos previstos', bt.get('previstos'), st.get('previstos'), 'int', False),
        _row('Capacitação', 'Vencidos', bt.get('vencidos'), st.get('vencidos'), 'int', True),
        _row('Capacitação', 'A vencer (7d)', bt.get('a_vencer_7d'), st.get('a_vencer_7d'), 'int', True),
        _row('Capacitação', 'Assinados', bt.get('assinados'), st.get('assinados'), 'int', False),
    ]


def _placar(dimensions):
    """Conta em quantas dimensões cada lado está melhor."""
    bsb_melhor = sum(1 for d in dimensions if d['pior'] == 'sc')
    sc_melhor = sum(1 for d in dimensions if d['pior'] == 'bsb')
    return {'bsb_melhor': bsb_melhor, 'sc_melhor': sc_melhor, 'empates': len(dimensions) - bsb_melhor - sc_melhor}


def _localidades_no_periodo(params):
    p = dict(params)
    p.pop('localidade', None)
    qs = Failure.objects.all()
    qs = apply_failure_filters(qs, p)
    return sorted({loc for loc in qs.values_list('localidade', flat=True).distinct() if loc})


def build_comparativo_bsb_sc(user, params):
    bsb = {
        'localidade': 'Brasília',
        'falhas': _failure_block(user, params, 'Brasília'),
        'suporte': _support_block(params, 'Brasília'),
        'treinamentos': _training_block(params, 'Brasília'),
    }
    sc = {
        'localidade': 'São Carlos',
        'falhas': _failure_block(user, params, 'São Carlos'),
        'suporte': _support_block(params, 'São Carlos'),
        'treinamentos': _training_block(params, 'São Carlos'),
    }
    drivers = _drivers(params, user)
    narrative = build_comparativo_narrative(bsb, sc, drivers)
    dimensions = _build_dimensions(bsb, sc)
    placar = _placar(dimensions)

    def _delta(a, b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return round_num(a - b, 1)
        return None

    b_total = bsb['falhas']['total'] or 0
    s_total = sc['falhas']['total'] or 0
    resumo = {
        'falhas_delta': _delta(b_total, s_total),
        'nc_delta': _delta(bsb['suporte']['nc_oficial_pct'], sc['suporte']['nc_oficial_pct']),
        'r3_delta': _delta(bsb['suporte']['regra3_pct'], sc['suporte']['regra3_pct']),
    }

    return {
        'brasilia': bsb,
        'sao_carlos': sc,
        'drivers': drivers,
        'dimensions': dimensions,
        'placar': placar,
        'resumo_delta': resumo,
        'pre_diagnostico': narrative,
        'meta': {
            'totais_falhas': {'brasilia': b_total, 'sao_carlos': s_total},
            'sc_sem_dados': s_total == 0,
            'bsb_sem_dados': b_total == 0,
            'localidades_no_periodo': _localidades_no_periodo(params),
        },
    }
