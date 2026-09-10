# -*- coding: utf-8 -*-
"""Analytics Suporte TEAMS — paridade com report legado."""
from collections import Counter
from datetime import datetime

import pandas as pd

from report_falhas.io.data_loader import compute_conformidade_suporte, normalize_text, safe_str
from report_falhas.periods import (
    filter_by_date_range_on,
    get_comparativo_by_same_period_on,
    month_last_day,
    prev_month_first_day,
)

from apps.falhas_criticas.services.formatters import fmt_protocolo
from apps.falhas_criticas.services.narrative import build_support_narrative, trend_from_series
from apps.falhas_criticas.utils_metrics import pct


def _is_nc(val):
    return normalize_text(safe_str(val)) == 'nao conforme'


def _is_conforme(val):
    return normalize_text(safe_str(val)) == 'conforme'


def _is_r3_row(row):
    crit = normalize_text(safe_str(row.get('Crítico', '')))
    dif = normalize_text(safe_str(row.get('Grau de dificuldade', '')))
    return crit == 'sim' and dif == 'facil'


def _recalc_conformidade(df):
    if df.empty:
        return pd.Series(dtype=str)
    return df.apply(
        lambda r: compute_conformidade_suporte({
            'Dúvida': r.get('Dúvida', ''),
            'Conclusão': r.get('Conclusão', ''),
            'Grau de dificuldade': r.get('Grau de dificuldade', ''),
            'Crítico': r.get('Crítico', ''),
        }),
        axis=1,
    )


def _agent_label(row):
    nome = safe_str(row.get('Nome Agente', '')) or safe_str(row.get('Agente nome planilha', ''))
    mat = safe_str(row.get('Agente', ''))
    return nome or mat, mat


def _fmt_date_br(d):
    if d is None:
        return ''
    if hasattr(d, 'strftime'):
        return d.strftime('%d/%m/%Y')
    return str(d)


def _fmt_period_label(start_d, end_d):
    if not start_d or not end_d:
        return ''
    return f"{_fmt_date_br(start_d)} a {_fmt_date_br(end_d)}"


def _value_counts_col(df, col_name):
    if df is None or df.empty or col_name not in df.columns:
        return pd.Series(dtype=int)
    s = df[col_name].fillna('').astype(str).apply(safe_str)
    s = s[s != '']
    return s.value_counts()


def _build_texto_comparativo(df_cur, df_prev, col_name, limit=50):
    vc_cur = _value_counts_col(df_cur, col_name)
    vc_prev = _value_counts_col(df_prev, col_name)
    all_keys = set(vc_cur.index) | set(vc_prev.index)
    rows = []
    for k in all_keys:
        atual = int(vc_cur.get(k, 0))
        anterior = int(vc_prev.get(k, 0))
        delta = atual - anterior
        if anterior == 0 and atual > 0:
            status = 'novo'
        elif delta > 0:
            status = 'piora'
        elif delta < 0:
            status = 'melhora'
        else:
            status = 'estavel'
        if anterior:
            delta_pct = f"{'+' if delta >= 0 else ''}{pct(delta, anterior) or 0}%"
        elif atual and delta > 0:
            delta_pct = 'N/A'
        else:
            delta_pct = '—'
        rows.append({
            'texto': k,
            'atual': atual,
            'anterior': anterior,
            'delta': delta,
            'delta_pct': delta_pct,
            'status': status,
        })

    def _sort_key(r):
        order = {'piora': 0, 'novo': 1, 'estavel': 2, 'melhora': 3}
        if r['status'] == 'piora':
            return (order[r['status']], -r['delta'])
        return (order.get(r['status'], 9), -r['atual'])

    rows.sort(key=_sort_key)
    return rows[:limit]


def _piora_subset(rows, limit=10):
    piora = [r for r in rows if r['status'] in ('piora', 'novo') and r['delta'] > 0]
    piora.sort(key=lambda r: r['delta'], reverse=True)
    return piora[:limit]


def _get_period_frames(df_all, cur_start, cur_end):
    """Retorna df_prev MTD equivalente e df_prev mês cheio."""
    prev_start = prev_month_first_day(cur_end)
    _, _, prev_mtd_start, prev_mtd_end = get_comparativo_by_same_period_on(
        df_all, cur_start, cur_end, prev_start, 'Data',
    )
    df_prev_mtd = filter_by_date_range_on(df_all, prev_mtd_start, prev_mtd_end, 'Data')
    prev_full_end = month_last_day(prev_start)
    df_prev_full = filter_by_date_range_on(df_all, prev_start, prev_full_end, 'Data')
    return df_prev_mtd, df_prev_full, prev_mtd_start, prev_mtd_end, prev_start, prev_full_end


def _quality_mtd_metrics(df_prev):
    if df_prev is None or df_prev.empty:
        return 0, 0
    conf = _recalc_conformidade(df_prev)
    total = len(df_prev)
    nc_pct = pct(int(conf.apply(_is_nc).sum()), total) or 0
    r3_pct = pct(int(df_prev.apply(_is_r3_row, axis=1).sum()), total) or 0
    return nc_pct, r3_pct


def _fmt_pp_delta(cur_val, prev_val):
    if prev_val is None:
        return '—'
    d = round(float(cur_val) - float(prev_val), 1)
    return f"{'+' if d >= 0 else ''}{d} pp"


def _build_top_agentes_volume(df_cur, df_prev_mtd, agent_counts, limit=12):
    """Ranking por volume com comparativo MTD e cenário dominante por pessoa."""
    prev_qtd = {}
    if df_prev_mtd is not None and not df_prev_mtd.empty:
        for _, row in df_prev_mtd.iterrows():
            nome, mat = _agent_label(row)
            key = mat or nome
            if key:
                prev_qtd[key] = prev_qtd.get(key, 0) + 1

    rows = []
    for key, data in agent_counts.items():
        atual = int(data.get('qtd') or 0)
        anterior = int(prev_qtd.get(key, 0))
        delta = atual - anterior
        duvidas = [d for d in data.get('_duvidas', []) if d]
        top_cenario = ''
        if duvidas:
            top_cenario = Counter(duvidas).most_common(1)[0][0]
        if anterior:
            delta_pct = f"{'+' if delta >= 0 else ''}{pct(delta, anterior) or 0}%"
        elif atual and delta > 0:
            delta_pct = 'N/A'
        else:
            delta_pct = '—'
        if anterior == 0 and atual > 0:
            status = 'novo'
        elif delta > 0:
            status = 'piora'
        elif delta < 0:
            status = 'melhora'
        else:
            status = 'estavel'
        rows.append({
            'matricula': data.get('matricula') or '',
            'nome': data.get('nome') or key,
            'atual': atual,
            'anterior': anterior,
            'delta': delta,
            'delta_pct': delta_pct,
            'top_cenario': top_cenario,
            'status': status,
        })

    rows.sort(key=lambda r: (r['atual'], r['delta']), reverse=True)
    return rows[:limit]


def _top_duvidas_conformes(df_sup, conform_calc, limit=5):
    if df_sup.empty or 'Dúvida' not in df_sup.columns:
        return []
    mask = conform_calc.apply(_is_conforme)
    df_conf = df_sup[mask]
    if df_conf.empty:
        return []
    vd = df_conf['Dúvida'].fillna('').astype(str).apply(safe_str)
    vd = vd[vd != ''].value_counts().head(limit)
    total_conf = len(df_conf)
    return [{'texto': k, 'qtd': int(v), 'percentual': pct(v, total_conf)} for k, v in vd.items()]


def _empty_support_payload():
    return {
        'total': 0,
        'nc_oficial_pct': 0,
        'nc_count': 0,
        'conforme_count': 0,
        'conforme_pct': 0,
        'regra3_count': 0,
        'regra3_pct': 0,
        'conformidade': {},
        'dificuldade': {},
        'critico': {},
        'top_workflows': [],
        'top_agentes': [],
        'top_agentes_volume': [],
        'top_agentes_r3': [],
        'casos_r3': [],
        'top_duvidas': [],
        'top_conclusoes': [],
        'top_duvidas_conformes': [],
        'volume_anterior': 0,
        'volume_delta_pct': '—',
        'nc_anterior_pct': 0,
        'nc_delta_pct': '—',
        'r3_anterior_pct': 0,
        'r3_delta_pct': '—',
        'comparativo_duvidas_mtd': [],
        'comparativo_duvidas_mes_cheio': [],
        'comparativo_conclusoes_mtd': [],
        'comparativo_conclusoes_mes_cheio': [],
        'duvidas_piora_mtd': [],
        'duvidas_piora_mes_cheio': [],
        'duvidas_piora_count_mtd': 0,
        'duvidas_piora_count_mes_cheio': 0,
        'periodo_atual_label': '',
        'periodo_mtd_anterior_label': '',
        'periodo_mes_cheio_anterior_label': '',
        'periodo_parcial': False,
        'modo_comparativo_default': 'mtd',
        'serie_diaria': [],
        'tendencia_diaria': '',
        'pre_diagnostico': build_support_narrative({'total': 0}),
        'bridge_falhas': None,
    }


def build_support_full_payload(df_sup, params, df_all_sup=None):
    if df_sup is None:
        df_sup = pd.DataFrame()
    if df_all_sup is None:
        df_all_sup = df_sup

    total = len(df_sup)
    if total == 0:
        return _empty_support_payload()

    conform_calc = _recalc_conformidade(df_sup)
    mask_r3 = df_sup.apply(_is_r3_row, axis=1)
    nc_count = int(conform_calc.apply(_is_nc).sum())
    nc_pct = pct(nc_count, total) or 0
    r3_count = int(mask_r3.sum())
    r3_pct = pct(r3_count, total) or 0
    conforme_count = total - nc_count

    volume_anterior = 0
    volume_delta_pct = '—'
    nc_anterior_pct = 0
    nc_delta_pct = '—'
    r3_anterior_pct = 0
    r3_delta_pct = '—'
    periodo_atual_label = ''
    periodo_mtd_anterior_label = ''
    periodo_mes_cheio_anterior_label = ''
    periodo_parcial = False
    comparativo_duvidas_mtd = []
    comparativo_duvidas_mes_cheio = []
    comparativo_conclusoes_mtd = []
    comparativo_conclusoes_mes_cheio = []
    duvidas_piora_mtd = []
    duvidas_piora_mes_cheio = []
    df_prev_mtd = pd.DataFrame()

    start_s = params.get('start_date')
    end_s = params.get('end_date')
    if start_s and end_s and not df_all_sup.empty and 'Data' in df_all_sup.columns:
        try:
            cur_start = datetime.strptime(start_s, '%Y-%m-%d').date()
            cur_end = datetime.strptime(end_s, '%Y-%m-%d').date()
            prev_start = prev_month_first_day(cur_end)
            total_atual, total_prev, prev_mtd_start, prev_mtd_end = get_comparativo_by_same_period_on(
                df_all_sup, cur_start, cur_end, prev_start, 'Data',
            )
            volume_anterior = int(total_prev)
            if volume_anterior:
                d = total_atual - volume_anterior
                volume_delta_pct = f"{'+' if d >= 0 else ''}{pct(d, volume_anterior) or 0}%"
            elif total_atual:
                volume_delta_pct = 'N/A'

            periodo_atual_label = _fmt_period_label(cur_start, cur_end)
            periodo_parcial = cur_end < month_last_day(cur_start)

            df_prev_mtd, df_prev_full, _, _, prev_full_start, prev_full_end = _get_period_frames(
                df_all_sup, cur_start, cur_end,
            )
            periodo_mtd_anterior_label = _fmt_period_label(prev_mtd_start, prev_mtd_end)
            periodo_mes_cheio_anterior_label = _fmt_period_label(prev_full_start, prev_full_end)

            nc_anterior_pct, r3_anterior_pct = _quality_mtd_metrics(df_prev_mtd)
            nc_delta_pct = _fmt_pp_delta(nc_pct, nc_anterior_pct)
            r3_delta_pct = _fmt_pp_delta(r3_pct, r3_anterior_pct)

            comparativo_duvidas_mtd = _build_texto_comparativo(df_sup, df_prev_mtd, 'Dúvida')
            comparativo_duvidas_mes_cheio = _build_texto_comparativo(df_sup, df_prev_full, 'Dúvida')
            comparativo_conclusoes_mtd = _build_texto_comparativo(df_sup, df_prev_mtd, 'Conclusão')
            comparativo_conclusoes_mes_cheio = _build_texto_comparativo(df_sup, df_prev_full, 'Conclusão')
            duvidas_piora_mtd = _piora_subset(comparativo_duvidas_mtd)
            duvidas_piora_mes_cheio = _piora_subset(comparativo_duvidas_mes_cheio)
        except Exception:
            pass

    def _dict_pct(series):
        vc = series.fillna('').astype(str)
        vc = vc[vc != ''].value_counts()
        return {k: {'qtd': int(v), 'percentual': pct(v, total)} for k, v in vc.items()}

    top_workflows = []
    if 'Workflow' in df_sup.columns:
        vw = df_sup['Workflow'].fillna('').astype(str)
        vw = vw[vw != ''].value_counts().head(5)
        top_workflows = [{'workflow': k, 'qtd': int(v), 'percentual': pct(v, total)} for k, v in vw.items()]

    df_r3 = df_sup[mask_r3].copy()
    agent_counts = {}

    for _, row in df_sup.iterrows():
        nome, mat = _agent_label(row)
        key = mat or nome
        if not key:
            continue
        if key not in agent_counts:
            agent_counts[key] = {
                'matricula': mat,
                'nome': nome,
                'qtd': 0,
                'nc': 0,
                'r3': 0,
                'top_duvida_r3': '',
                'top_conclusao_r3': '',
                '_duvidas': [],
            }
        agent_counts[key]['qtd'] += 1
        duv = safe_str(row.get('Dúvida', ''))
        if duv:
            agent_counts[key]['_duvidas'].append(duv)
        conf = compute_conformidade_suporte({
            'Dúvida': row.get('Dúvida', ''),
            'Conclusão': row.get('Conclusão', ''),
            'Grau de dificuldade': row.get('Grau de dificuldade', ''),
            'Crítico': row.get('Crítico', ''),
        })
        if _is_nc(conf):
            agent_counts[key]['nc'] += 1
        if _is_r3_row(row):
            agent_counts[key]['r3'] += 1

    for k, v in agent_counts.items():
        t = v['qtd']
        v['nc_pct'] = pct(v['nc'], t)
        v['r3_pct'] = pct(v['r3'], t)

    top_agentes_volume = _build_top_agentes_volume(df_sup, df_prev_mtd, agent_counts)
    top_agentes = sorted(agent_counts.values(), key=lambda x: x['nc'], reverse=True)[:10]
    top_agentes_r3 = sorted(
        [a for a in agent_counts.values() if a['r3'] > 0],
        key=lambda x: (x['r3'], x['nc']),
        reverse=True,
    )[:20]

    casos_r3 = []
    for _, row in df_r3.head(80).iterrows():
        nome, mat = _agent_label(row)
        casos_r3.append({
            'protocolo': fmt_protocolo(row.get('Protocolo', '')),
            'data': row.get('Data'),
            'matricula': mat,
            'nome': nome,
            'cliente': safe_str(row.get('Cliente', '')),
            'workflow': safe_str(row.get('Workflow', '')),
            'duvida': safe_str(row.get('Dúvida', '')),
            'conclusao': safe_str(row.get('Conclusão', '')),
        })

    top_duvidas = []
    if 'Dúvida' in df_sup.columns:
        vd = df_sup['Dúvida'].fillna('').astype(str)
        vd = vd[vd != ''].value_counts().head(8)
        top_duvidas = [{'texto': k, 'qtd': int(v), 'percentual': pct(v, total)} for k, v in vd.items()]

    top_conclusoes = []
    if 'Conclusão' in df_sup.columns:
        vc = df_sup['Conclusão'].fillna('').astype(str)
        vc = vc[vc != ''].value_counts().head(8)
        top_conclusoes = [{'texto': k, 'qtd': int(v), 'percentual': pct(v, total)} for k, v in vc.items()]

    top_duvidas_conformes = _top_duvidas_conformes(df_sup, conform_calc)

    kpis = {
        'total': total,
        'nc_oficial_pct': nc_pct,
        'nc_count': nc_count,
        'conforme_count': conforme_count,
        'conforme_pct': pct(conforme_count, total),
        'regra3_count': r3_count,
        'regra3_pct': r3_pct,
        'volume_anterior': volume_anterior,
        'volume_delta_pct': volume_delta_pct,
        'nc_anterior_pct': nc_anterior_pct,
        'nc_delta_pct': nc_delta_pct,
        'r3_anterior_pct': r3_anterior_pct,
        'r3_delta_pct': r3_delta_pct,
    }

    daily = []
    serie_diaria = []
    if 'Data' in df_sup.columns:
        dfx = df_sup.copy()
        dfx['Data'] = pd.to_datetime(dfx['Data'], errors='coerce')
        vc = dfx.groupby(dfx['Data'].dt.date).size().sort_index()
        daily = [int(v) for v in vc.values]
        serie_diaria = [{'data': _fmt_date_br(k), 'qtd': int(v)} for k, v in vc.items()]

    payload = {
        **kpis,
        'conformidade': _dict_pct(conform_calc),
        'dificuldade': _dict_pct(df_sup.get('Grau de dificuldade', pd.Series(dtype=str))),
        'critico': _dict_pct(df_sup.get('Crítico', pd.Series(dtype=str))),
        'top_workflows': top_workflows,
        'top_agentes': top_agentes,
        'top_agentes_volume': top_agentes_volume,
        'top_agentes_r3': top_agentes_r3,
        'casos_r3': casos_r3,
        'top_duvidas': top_duvidas,
        'top_conclusoes': top_conclusoes,
        'top_duvidas_conformes': top_duvidas_conformes,
        'comparativo_duvidas_mtd': comparativo_duvidas_mtd,
        'comparativo_duvidas_mes_cheio': comparativo_duvidas_mes_cheio,
        'comparativo_conclusoes_mtd': comparativo_conclusoes_mtd,
        'comparativo_conclusoes_mes_cheio': comparativo_conclusoes_mes_cheio,
        'duvidas_piora_mtd': duvidas_piora_mtd,
        'duvidas_piora_mes_cheio': duvidas_piora_mes_cheio,
        'duvidas_piora_count_mtd': len(duvidas_piora_mtd),
        'duvidas_piora_count_mes_cheio': len(duvidas_piora_mes_cheio),
        'periodo_atual_label': periodo_atual_label,
        'periodo_mtd_anterior_label': periodo_mtd_anterior_label,
        'periodo_mes_cheio_anterior_label': periodo_mes_cheio_anterior_label,
        'periodo_parcial': periodo_parcial,
        'modo_comparativo_default': 'mtd',
        'serie_diaria': serie_diaria,
        'tendencia_diaria': trend_from_series(daily, unidade='solicitações/dia'),
        'pre_diagnostico': build_support_narrative(
            kpis,
            top_agentes_r3,
            top_workflows,
            duvidas_piora=duvidas_piora_mtd,
            periodo_mtd_label=periodo_mtd_anterior_label,
            top_agentes_volume=top_agentes_volume,
        ),
    }
    return payload
