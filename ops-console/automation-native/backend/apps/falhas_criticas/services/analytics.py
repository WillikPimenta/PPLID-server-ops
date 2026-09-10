# -*- coding: utf-8 -*-
"""Analytics do portal — reutiliza lógica validada do report legado."""
from datetime import datetime, date

import pandas as pd

from report_falhas.io.data_loader import norm_matricula, safe_str
from report_falhas.analytics import build_rank_delta, build_quarterly_counts
from report_falhas.charts import build_charts_bundle
from report_falhas.config_report import COL_CENARIO, COL_MATRICULA
from report_falhas.filters import aplicar_recorte_oficial, uses_dual_metric_mode
from report_falhas.html_pages import build_ult3m_agent_table
from report_falhas.periods import (
    april_start,
    filter_by_date_range,
    months_from_to,
)
from report_falhas.reincidence import reincidencia_table_full, novos_no_mes
from report_falhas.kpis import make_kpis

from apps.falhas_criticas.services.dataframes import (
    failures_qs_to_legacy_df,
    load_period_frames,
    resolve_portal_comparativo_window,
)
from apps.falhas_criticas.utils_metrics import metric_block, pct


def _parse_dates(params):
    start_s = params.get('start_date')
    end_s = params.get('end_date')
    if not start_s or not end_s:
        return None, None
    return (
        datetime.strptime(start_s, '%Y-%m-%d').date(),
        datetime.strptime(end_s, '%Y-%m-%d').date(),
    )


def _top_cenarios(df, n=3):
    col = 'Cenário Unificado' if 'Cenário Unificado' in df.columns else 'Novo cenário'
    if df.empty or col not in df.columns:
        return []
    vc = df[col].fillna('').replace('', 'Sem cenário').value_counts()
    return [(str(k), int(v)) for k, v in vc.head(n).items()]


def _build_turno_map(df_cur):
    out = {}
    if df_cur.empty or 'Matrícula Agente' not in df_cur.columns:
        return out
    turno_col = 'Turno' if 'Turno' in df_cur.columns else None
    for mat, sub in df_cur.groupby('Matrícula Agente'):
        if turno_col:
            out[norm_matricula(mat)] = safe_str(sub[turno_col].iloc[0])
        elif 'Turno' in sub.columns:
            out[norm_matricula(mat)] = safe_str(sub['Turno'].iloc[0])
    return out


def _build_nome_map(df_cur):
    out = {}
    if df_cur.empty or 'Matrícula Agente' not in df_cur.columns:
        return out
    name_col = 'Nome Agente' if 'Nome Agente' in df_cur.columns else None
    if not name_col:
        return out
    for mat, sub in df_cur.groupby('Matrícula Agente'):
        out[norm_matricula(mat)] = safe_str(sub[name_col].iloc[0])
    return out


def _enrich_reinc_rows(df_reinc, df_cur):
    if df_reinc is None or df_reinc.empty:
        return []

    total_occ = int(pd.to_numeric(df_reinc.get('Ocorrências Mês Atual', 0), errors='coerce').fillna(0).sum())
    etapa_map = {}
    dif_map = {}
    turno_map = {}
    atividade_map = {}
    tempo_etapa_map = {}
    tempo_casa_map = {}
    nome_map = {}

    if not df_cur.empty and 'Matrícula Agente' in df_cur.columns:
        for mat, sub in df_cur.groupby('Matrícula Agente'):
            mat_s = safe_str(mat)
            etapa_map[mat_s] = safe_str(sub['Etapa'].iloc[0]) if 'Etapa' in sub.columns else ''
            turno_map[mat_s] = safe_str(sub['Turno'].iloc[0]) if 'Turno' in sub.columns else ''
            atividade_map[mat_s] = safe_str(sub['Atividade HC'].iloc[0]) if 'Atividade HC' in sub.columns else ''
            tempo_etapa_map[mat_s] = safe_str(sub['Tempo de etapa'].iloc[0]) if 'Tempo de etapa' in sub.columns else ''
            tempo_casa_map[mat_s] = safe_str(sub['Tempo de casa'].iloc[0]) if 'Tempo de casa' in sub.columns else ''
            nome_map[mat_s] = safe_str(sub['Nome Agente'].iloc[0]) if 'Nome Agente' in sub.columns else ''
            dif_col = 'Nível de Dificuldade' if 'Nível de Dificuldade' in sub.columns else 'Categoria falha'
            if dif_col in sub.columns:
                vc = sub[dif_col].fillna('').astype(str)
                vc = vc[vc != ''].value_counts()
                dif_map[mat_s] = ', '.join(f"{k} ({v})" for k, v in vc.head(3).items())

    rows = []
    for _, r in df_reinc.iterrows():
        mat = safe_str(r.get('Matrícula Agente', ''))
        occ = int(r.get('Ocorrências Mês Atual', 0) or 0)
        rows.append({
            'matricula': mat,
            'nome': nome_map.get(mat, ''),
            'reincidente': safe_str(r.get('Reincidente', '')),
            'ocorrencias': occ,
            'percentual': pct(occ, total_occ),
            'cenario': safe_str(r.get('Cenário (Mês Atual)', '')),
            'protocolos': safe_str(r.get('Protocolos (Mês Atual)', '')),
            'dificuldade_contagem': dif_map.get(mat, ''),
            'etapa': etapa_map.get(mat, ''),
            'atividade_hc': atividade_map.get(mat, ''),
            'tempo_etapa': tempo_etapa_map.get(mat, '—'),
            'tempo_casa': tempo_casa_map.get(mat, '—'),
            'turno': turno_map.get(mat, 'Sem turno') or 'Sem turno',
        })
    return rows


def build_executive_payload(df_all, df_cur, df_prev, params):
    cur_start, cur_end = _parse_dates(params)
    if not cur_start or not cur_end:
        cur_start = df_cur['Data de Análise'].min().date() if not df_cur.empty else date.today()
        cur_end = df_cur['Data de Análise'].max().date() if not df_cur.empty else date.today()

    prev_eq_start, prev_eq_end, _mode = resolve_portal_comparativo_window(cur_start, cur_end)
    total_atual = len(df_cur) if df_cur is not None else 0
    if df_prev is not None and not getattr(df_prev, 'empty', True):
        total_prev = len(df_prev)
    elif prev_eq_start and prev_eq_end and df_all is not None and not df_all.empty:
        total_prev = len(filter_by_date_range(df_all, prev_eq_start, prev_eq_end, 'Data de Análise'))
    else:
        total_prev = 0

    df_reinc = reincidencia_table_full(df_prev, df_cur)
    novos = novos_no_mes(df_prev, df_cur)
    top3 = _top_cenarios(df_cur, 3)
    kpis = make_kpis(
        cur_start, cur_end, total_atual, total_prev,
        prev_eq_start, prev_eq_end, df_reinc, top3, novos,
    )

    total = int(kpis['fail_mtd_atual'])
    prev = int(kpis['fail_prev_equal'])
    share_top1 = pct(kpis['top1_cenario_qtd'], total) if total else None

    insights = _simple_insights(df_cur, df_all, cur_start, cur_end, kpis)

    return {
        'periodo': kpis['periodo_mtd'],
        'periodo_comparativo': kpis['periodo_equal_prev'],
        'resumo_30s': {
            'falhas': metric_block(total, total=total, delta_abs=total - prev if prev else total,
                                   delta_pct=kpis['variacao_perc'], label='Falhas no período'),
            'reincidentes': metric_block(kpis['reinc_total'], total=total, label='Agentes reincidentes'),
            'novos_mes': metric_block(kpis['novos_mes_count'], total=total, label='Novos no período'),
            'top_cenario': {
                'nome': kpis['top1_cenario_nome'],
                'valor': kpis['top1_cenario_qtd'],
                'percentual': share_top1,
            },
        },
        'leitura_executiva': {
            'variacao_delta': kpis['variacao_delta'],
            'variacao_perc': kpis['variacao_perc'],
            'reinc_total': kpis['reinc_total'],
            'reinc_pct': pct(kpis['reinc_total'], total),
            'novos_mes_count': kpis['novos_mes_count'],
            'novos_pct': pct(kpis['novos_mes_count'], total),
            'top1_cenario_nome': kpis['top1_cenario_nome'],
            'top1_cenario_qtd': kpis['top1_cenario_qtd'],
            'top1_cenario_pct': share_top1,
        },
        'insights': insights,
    }


def _simple_insights(df_cur, df_all, cur_start, cur_end, kpis):
    insights = []
    _, _, mode = resolve_portal_comparativo_window(cur_start, cur_end)
    cmp_hint = (
        'mesmos dias no mês anterior'
        if mode == 'mtd'
        else 'mesmo número de dias imediatamente anteriores'
    )
    insights.append(
        f"Período {kpis['periodo_mtd']}: {kpis['fail_mtd_atual']} falhas "
        f"({kpis['variacao_perc']} vs {kpis['periodo_equal_prev']}, {cmp_hint})."
    )
    if kpis['reinc_total']:
        insights.append(f"{kpis['reinc_total']} agentes reincidentes no período.")
    if kpis['novos_mes_count']:
        insights.append(f"{kpis['novos_mes_count']} agentes com primeira falha no período.")
    if kpis['top1_cenario_nome'] and kpis['top1_cenario_nome'] != 'Sem dados':
        share = pct(kpis['top1_cenario_qtd'], kpis['fail_mtd_atual'])
        insights.append(
            f"Cenário dominante: {kpis['top1_cenario_nome']} "
            f"({kpis['top1_cenario_qtd']} — {share}% do total)."
        )
    if not df_cur.empty and 'Turno' in df_cur.columns:
        vc = df_cur['Turno'].fillna('Sem turno').replace('', 'Sem turno').value_counts()
        if not vc.empty:
            top_t = str(vc.index[0])
            insights.append(f"Turno com mais falhas: {top_t} ({int(vc.iloc[0])} ocorrências).")
    return insights[:8]


def build_consolidado_payload(df_all, df_cur, df_prev, params):
    exec_data = build_executive_payload(df_all, df_cur, df_prev, params)
    total = exec_data['resumo_30s']['falhas']['valor']
    cur_start, _cur_end = _parse_dates(params)

    df_of = df_cur.copy()
    if 'Módulo' in df_of.columns:
        df_of = aplicar_recorte_oficial(df_of, cur_start)
    total_of = len(df_of)
    dual_metric_mode = uses_dual_metric_mode(cur_start)

    reinc_rows = _enrich_reinc_rows(reincidencia_table_full(df_prev, df_cur), df_cur)
    reinc_sim = sum(1 for r in reinc_rows if r['reincidente'].lower() == 'sim')

    tiles = {
        'oficial_periodo': metric_block(
            total_of, total=total if dual_metric_mode else total_of, label='Oficial do período',
        ),
        'reincidentes': metric_block(reinc_sim, total=total, label='Reincidentes'),
        'novos': exec_data['resumo_30s']['novos_mes'],
        'top_cenario': exec_data['resumo_30s']['top_cenario'],
    }
    if dual_metric_mode:
        tiles['total_periodo'] = metric_block(total, label='Total do período')
    return {'tiles': tiles, 'exec': exec_data}


def build_rank_delta_payload(df_cur, df_prev, col='Cenário Unificado'):
    if col not in df_cur.columns and 'Novo cenário' in df_cur.columns:
        col = 'Novo cenário'
    rank = build_rank_delta(df_cur, df_prev, col)
    total_periodo = len(df_cur) if df_cur is not None and not getattr(df_cur, 'empty', True) else 0
    for row in rank.get('impacto', []):
        cur = int(row.get('cur') or 0)
        share = (cur / total_periodo * 100.0) if total_periodo else float(row.get('share') or 0)
        row['share_pct'] = round(share, 1)
        row['share_fmt'] = f"{row['share_pct']:.1f}%"
        row['participacao_pct'] = row['share_pct']
        row['share_denominador'] = total_periodo
    for row in rank.get('crescimento', []):
        dp = row.get('delta_pct')
        row['delta_pct_fmt'] = f"{'+' if dp and dp > 0 else ''}{dp:.1f}%" if dp is not None else '—'
    rank['total_valid'] = total_periodo or rank.get('total_valid') or 0
    return rank


def build_reincidence_by_turno(df_reinc, df_cur):
    rows = _enrich_reinc_rows(df_reinc, df_cur)
    by_turno = {}
    for row in rows:
        turno = row.get('turno') or 'Sem turno'
        by_turno.setdefault(turno, []).append(row)
    summary = []
    for turno, items in sorted(by_turno.items()):
        total_occ = sum(i['ocorrencias'] for i in items)
        summary.append({
            'turno': turno,
            'agentes': len(items),
            'ocorrencias': total_occ,
            'reincidentes': sum(1 for i in items if i['reincidente'].lower() == 'sim'),
        })
    return {'turnos': summary, 'tabelas': by_turno}


def build_turno_pct_chart(df_cur):
    if df_cur.empty:
        return {}
    df = df_cur.copy()
    df['turno'] = df.get('Turno', pd.Series(dtype=str)).fillna('Sem turno').replace('', 'Sem turno')

    def cat_dif(val):
        s = str(val).lower()
        if 'facil' in s or 'fácil' in s:
            return 'Fácil'
        if 'dificil' in s or 'difícil' in s or 'complex' in s:
            return 'Difícil'
        return 'Médio'

    dif_col = 'Nível de Dificuldade' if 'Nível de Dificuldade' in df.columns else 'Categoria falha'
    df['dif'] = df[dif_col].apply(cat_dif)
    out = {}
    for turno, sub in df.groupby('turno'):
        total = len(sub)
        counts = sub['dif'].value_counts()
        out[str(turno)] = {
            k: {'qtd': int(counts.get(k, 0)), 'pct': pct(counts.get(k, 0), total)}
            for k in ['Fácil', 'Médio', 'Difícil']
        }
        out[str(turno)]['_total'] = total
    return out


def build_charts_payload(df_cur, df_all, params):
    cur_start, cur_end = _parse_dates(params)
    if not cur_start:
        cur_start = date.today().replace(day=1)
    if not cur_end:
        cur_end = date.today()

    meses_br = ['Jan', 'Fev', 'Mar', 'Abr', 'Mai', 'Jun', 'Jul', 'Ago', 'Set', 'Out', 'Nov', 'Dez']

    def _monthly_series(df, start_april=None):
        if df.empty:
            return []
        dfx = df.copy()
        if start_april:
            dfx = dfx[dfx['Data de Análise'].dt.date >= start_april]
        dfx['mes_ano'] = dfx['Data de Análise'].apply(lambda d: d.strftime('%Y-%m'))
        grouped = dfx.groupby('mes_ano').size().sort_index()
        out = []
        running = 0
        for mes_ano, size in grouped.items():
            dt_obj = datetime.strptime(mes_ano, '%Y-%m')
            running += int(size)
            out.append({
                'mes_ano': f"{meses_br[dt_obj.month - 1]}/{dt_obj.year}",
                'quantidade': int(size),
                'acumulado': running,
                'percentual': None,
            })
        total = sum(x['quantidade'] for x in out) or 1
        for x in out:
            x['percentual'] = pct(x['quantidade'], total)
        return out

    def _facil_series(df):
        if df.empty:
            return []
        dfx = df.copy()
        dif_col = 'Nível de Dificuldade' if 'Nível de Dificuldade' in dfx.columns else 'Categoria falha'
        mask = dfx[dif_col].astype(str).str.lower().str.contains('facil|fácil', regex=True, na=False)
        return _monthly_series(dfx[mask])

    def _daily_series(df):
        if df.empty or not cur_start or not cur_end:
            return []
        dfx = filter_by_date_range(df, cur_start, cur_end, 'Data de Análise')
        if dfx.empty:
            return []
        vc = dfx['Data de Análise'].dt.strftime('%d/%m').value_counts().sort_index()
        total = int(vc.sum()) or 1
        return [{'dia': k, 'quantidade': int(v), 'percentual': pct(v, total)} for k, v in vc.items()]

    quarter = []
    if not df_cur.empty and cur_end:
        try:
            quarter_raw = build_quarterly_counts(df_cur, april_start(cur_end), cur_end)
            total_q = sum(q for _, q in quarter_raw) or 1
            quarter = [{'label': lbl, 'qtd': q, 'percentual': pct(q, total_q)} for lbl, q in quarter_raw]
        except Exception:
            quarter = []
    turno_pct = build_turno_pct_chart(df_cur)

    pressao = []
    if not df_cur.empty:
        df = df_cur.copy()
        df['turno'] = df.get('Turno', pd.Series(dtype=str)).fillna('Sem turno').replace('', 'Sem turno')
        falhas_por_turno = df['turno'].value_counts()
        agentes_por_turno = df.groupby('turno')['Matrícula Agente'].nunique()
        for t_name in falhas_por_turno.index:
            f_count = int(falhas_por_turno[t_name])
            a_count = int(agentes_por_turno.get(t_name, 1)) or 1
            pressao.append({
                'turno': str(t_name),
                'falhas': f_count,
                'agentes': a_count,
                'pressao': round(f_count / a_count, 2),
                'pct_do_total': pct(f_count, len(df)),
            })
        pressao.sort(key=lambda x: x['pressao'], reverse=True)

    return {
        'evolucao_fy': _monthly_series(df_all, april_start(cur_end)),
        'evolucao_mensal': _monthly_series(df_cur),
        'evolucao_facil': _facil_series(df_cur),
        'evolucao_diaria': _daily_series(df_cur),
        'quarter': quarter,
        'qualidade_turno_pct': turno_pct,
        'pressao_turno': pressao,
    }


def build_clientes_workflows(df_cur):
    if df_cur.empty:
        return {
            'total': 0,
            'clientes': [],
            'workflows': [],
            'top_clientes': [],
            'top_workflows': [],
            'clientes_distintos': 0,
            'workflows_distintos': 0,
            'top_cliente': None,
            'top_workflow': None,
            'por_modulo': [],
            'por_tipo_falha': [],
            'origem': {},
            'pre_diagnostico': None,
        }

    total = len(df_cur)
    clientes = []
    clientes_distintos = 0
    if 'Cliente' in df_cur.columns:
        cli_series = df_cur['Cliente'].fillna('').replace('', 'Sem cliente')
        clientes_distintos = int(cli_series.nunique())
        vc = cli_series.value_counts().head(15)
        clientes = [{'nome': str(k), 'qtd': int(v), 'percentual': pct(v, total)} for k, v in vc.items()]
    workflows = []
    workflows_distintos = 0
    if 'Workflow' in df_cur.columns:
        wf_series = df_cur['Workflow'].fillna('').replace('', 'Sem workflow')
        workflows_distintos = int(wf_series.nunique())
        vw = wf_series.value_counts().head(15)
        workflows = [{'nome': str(k), 'qtd': int(v), 'percentual': pct(v, total)} for k, v in vw.items()]

    por_modulo = []
    if 'Módulo' in df_cur.columns:
        vm = df_cur['Módulo'].fillna('').replace('', 'Sem módulo').value_counts()
        por_modulo = [{'nome': str(k), 'qtd': int(v), 'percentual': pct(v, total)} for k, v in vm.items()]

    por_tipo_falha = []
    if 'Tipo de Falha' in df_cur.columns:
        vt = df_cur['Tipo de Falha'].fillna('').replace('', 'OUTROS').value_counts()
        por_tipo_falha = [{'nome': str(k), 'qtd': int(v), 'percentual': pct(v, total)} for k, v in vt.items()]

    return {
        'total': total,
        'clientes': clientes,
        'workflows': workflows,
        'top_clientes': clientes[:10],
        'top_workflows': workflows[:10],
        'clientes_distintos': clientes_distintos,
        'workflows_distintos': workflows_distintos,
        'top_cliente': clientes[0] if clientes else None,
        'top_workflow': workflows[0] if workflows else None,
        'por_modulo': por_modulo,
        'por_tipo_falha': por_tipo_falha,
    }


def build_ult3m_payload(df_scope, params):
    cur_start, cur_end = _parse_dates(params)
    if not cur_start or not cur_end:
        return {'meta': {}, 'agentes': [], 'kpis': {}, 'columns': {}}

    from apps.falhas_criticas.services.formatters import parse_doc_uf_list, parse_scenario_list

    nome_map = _build_nome_map(df_scope)
    turno_map = _build_turno_map(df_scope)
    df_tab, meta = build_ult3m_agent_table(df_scope, cur_start, cur_end, nome_map, turno_map)
    if df_tab is None or df_tab.empty:
        return {'meta': meta, 'agentes': [], 'kpis': {}, 'columns': {}}

    ref_label = meta.get('ref', '')
    prev_labels = meta.get('prev') or ['', '', '']
    lab_m1, lab_m2, lab_m3 = prev_labels[0], prev_labels[1], prev_labels[2]

    col_ref = f'Falhas REF ({ref_label})'
    col_m1 = f'Falhas {lab_m1}'
    col_m2 = f'Falhas {lab_m2}'
    col_m3 = f'Falhas {lab_m3}'
    col_scen_ref = f'Top cenários REF ({ref_label})'
    col_doc_ref = f'Resumo Doc/UF REF ({ref_label})'

    agentes = []
    for _, row in df_tab.iterrows():
        rec = str(row.get('Recorrente (4M)', '')).lower() == 'sim'
        agentes.append({
            'matricula': safe_str(row.get('Matrícula', '')),
            'turno': safe_str(row.get('Turno', '')) or '—',
            'falhas': {
                'ref': int(row.get(col_ref, 0) or 0),
                'm1': int(row.get(col_m1, 0) or 0),
                'm2': int(row.get(col_m2, 0) or 0),
                'm3': int(row.get(col_m3, 0) or 0),
                'total': int(row.get('Total 4M (REF+3)', 0) or 0),
                'labels': {'ref': ref_label, 'm1': lab_m1, 'm2': lab_m2, 'm3': lab_m3},
            },
            'meses_com_falha': int(row.get('Meses com falha (4M)', 0) or 0),
            'recorrente': rec,
            'cenarios_ref': parse_scenario_list(row.get(col_scen_ref, '')),
            'cenarios_4m': parse_scenario_list(row.get('Top cenários (4M)', '')),
            'doc_uf_ref': parse_doc_uf_list(row.get(col_doc_ref, '')),
            'doc_uf_4m': parse_doc_uf_list(row.get('Resumo Doc/UF (4M)', '')),
            'ultima_ocorrencia': safe_str(row.get('Última ocorrência', '')) or '—',
        })

    recorrentes = sum(1 for a in agentes if a['recorrente'])
    return {
        'meta': meta,
        'agentes': agentes,
        'columns': {
            'ref': ref_label,
            'm1': lab_m1,
            'm2': lab_m2,
            'm3': lab_m3,
            'janela_txt': meta.get('janela_txt', ''),
        },
        'kpis': {
            'total_agentes_top': len(agentes),
            'recorrentes_4m': recorrentes,
            'recorrentes_pct': pct(recorrentes, len(agentes)) if agentes else 0,
        },
    }


def build_treinamentos_summary(qs):
    total = qs.count()
    if not total:
        return {'total': 0, 'por_status': [], 'por_tipo': []}
    from django.db.models import Count
    por_status = list(qs.values('status').annotate(q=Count('id')).order_by('-q'))
    por_tipo = list(qs.values('tipo_acao').annotate(q=Count('id')).order_by('-q'))
    return {
        'total': total,
        'por_status': [
            {'status': r['status'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_status
        ],
        'por_tipo': [
            {'tipo': r['tipo_acao'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_tipo
        ],
    }


def build_contestacoes_summary(qs):
    total = qs.count()
    if not total:
        return {
            'total': 0, 'por_fonte': [], 'por_status': [], 'por_localidade': [],
            'interna': 0, 'externa': 0, 'items': [], 'pre_diagnostico': None,
        }
    from django.db.models import Count
    por_fonte = list(qs.values('fonte').annotate(q=Count('id')).order_by('-q'))
    por_status = list(qs.values('status').annotate(q=Count('id')).order_by('-q'))
    por_localidade = list(qs.values('localidade').annotate(q=Count('id')).order_by('-q'))
    items = list(qs.order_by('-data')[:100].values('protocolo', 'data', 'fonte', 'status', 'localidade'))

    interna = qs.filter(fonte__iexact='Interna').count()
    externa = qs.filter(fonte__iexact='Externa').count()

    from apps.falhas_criticas.services.narrative import pre_diagnostico_block
    bullets = []
    if por_status:
        top_st = por_status[0]
        bullets.append(f"Status predominante: {top_st['status'] or '—'} ({top_st['q']} — {pct(top_st['q'], total)}%).")
    bullets.append(f"Origem: {interna} internas ({pct(interna, total)}%) e {externa} externas ({pct(externa, total)}%).")

    return {
        'total': total,
        'interna': interna,
        'externa': externa,
        'por_fonte': [
            {'fonte': r['fonte'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_fonte
        ],
        'por_status': [
            {'status': r['status'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_status
        ],
        'por_localidade': [
            {'localidade': r['localidade'] or '—', 'qtd': r['q'], 'percentual': pct(r['q'], total)}
            for r in por_localidade
        ],
        'items': items,
        'pre_diagnostico': pre_diagnostico_block(
            veredito=f"{total} contestações no período filtrado.",
            bullets=bullets,
            titulo='Leitura — Contestações',
        ),
    }
