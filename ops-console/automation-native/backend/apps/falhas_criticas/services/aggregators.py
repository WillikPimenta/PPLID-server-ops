# -*- coding: utf-8 -*-
"""Agregadores Fase 4/5 — dashboard-summary, diagnostico, reincidence-summary."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from report_falhas.reincidence import reincidencia_table_full

from apps.falhas_criticas.models import Contestation, Failure, Support
from apps.falhas_criticas.services.alerts import build_dashboard_alerts
from apps.falhas_criticas.services.analytics import (
    _enrich_reinc_rows,
    _top_cenarios,
    build_charts_payload,
    build_clientes_workflows,
    build_consolidado_payload,
    build_executive_payload,
    build_rank_delta_payload,
    build_reincidence_by_turno,
    build_ult3m_payload,
)
from apps.falhas_criticas.services.compare_analytics import build_comparativo_bsb_sc
from apps.falhas_criticas.services.dataframes import (
    apply_failure_filters,
    apply_support_filters,
    failures_qs_to_legacy_df,
    load_period_frames,
    supports_qs_to_legacy_df,
)
from apps.falhas_criticas.services.localidade import filter_qs_by_localidade
from apps.falhas_criticas.services.narrative import pre_diagnostico_block
from apps.falhas_criticas.services.priority import (
    PRIORITY_ALTA,
    PRIORITY_BAIXA,
    PRIORITY_MEDIA,
    agent_priority,
    build_dashboard_leitura,
    build_dashboard_prioridades,
    priority_item,
)
from apps.falhas_criticas.services.support_analytics import build_support_full_payload
from apps.falhas_criticas.services.support_falhas_bridge import (
    _empty_bridge as _empty_support_bridge,
    build_support_falhas_bridge,
)
from apps.falhas_criticas.services.training_analytics import _training_qs_filtered
from apps.falhas_criticas.services.training_reincidence_bridge import (
    _empty_bridge as _empty_training_bridge,
    build_training_reincidence_bridge,
)
from apps.falhas_criticas.utils_metrics import pct as _pct


def _meta_filters(params: dict) -> dict:
    keys = ('start_date', 'end_date', 'localidade', 'oficial', 'turno', 'matricula', 'meu_time')
    return {k: params.get(k) for k in keys if params.get(k) not in (None, '', False)}


def _meta_block(
    params: dict,
    scope: dict,
    warnings: list[str] | None = None,
    duration_ms: int | None = None,
) -> dict:
    meta = {
        'filters': _meta_filters(params),
        'scope': scope.get('scope'),
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'warnings': warnings or [],
    }
    if duration_ms is not None:
        meta['duration_ms'] = duration_ms
    return meta


def _suporte_resumo_from_params(params: dict) -> dict:
    suporte_resumo = {'total': 0, 'nc_oficial_pct': 0}
    try:
        qs_sup = apply_support_filters(Support.objects.all(), params)
        df_sup = supports_qs_to_legacy_df(qs_sup)
        sup_payload = build_support_full_payload(df_sup, params)
        suporte_resumo = {
            'total': sup_payload.get('total', 0),
            'nc_oficial_pct': sup_payload.get('nc_oficial_pct', 0),
        }
    except Exception:
        pass
    return suporte_resumo


def build_kpis_from_executive_payload(exec_payload: dict, params: dict) -> dict:
    le = exec_payload['leitura_executiva']
    res = exec_payload['resumo_30s']
    return {
        'total_falhas': res['falhas']['valor'],
        'total_anterior': res['falhas']['valor'] - (res['falhas'].get('delta_abs') or 0),
        'variacao_delta': le['variacao_delta'],
        'variacao_perc': le['variacao_perc'],
        'reinc_total': le['reinc_total'],
        'reinc_pct': le.get('reinc_pct'),
        'novos_mes_count': le['novos_mes_count'],
        'novos_pct': le.get('novos_pct'),
        'top1_cenario_nome': le['top1_cenario_nome'],
        'top1_cenario_qtd': le['top1_cenario_qtd'],
        'top1_cenario_pct': le.get('top1_cenario_pct'),
        'resumo_30s': res,
        'suporte_resumo': _suporte_resumo_from_params(params),
        'pre_diagnostico': pre_diagnostico_block(
            veredito=exec_payload.get('insights', [''])[0],
            bullets=exec_payload.get('insights', [])[1:4],
        ),
    }


def build_executive_from_payload(exec_payload: dict, scope: dict) -> dict:
    payload = dict(exec_payload)
    payload['scope'] = scope
    insights = payload.get('insights', [])
    payload['pre_diagnostico'] = pre_diagnostico_block(
        veredito=insights[0] if insights else 'Sem dados no período.',
        bullets=insights[1:5],
    )
    return payload


def build_charts_preview(df_cur, df_all, params: dict) -> dict:
    charts = build_charts_payload(df_cur, df_all, params)
    turno_qualidade = charts.get('qualidade_turno_pct', {})
    legacy_qualidade = {}
    for turno, data in turno_qualidade.items():
        if turno.startswith('_'):
            continue
        legacy_qualidade[turno] = {
            'Fácil': data.get('Fácil', {}).get('qtd', 0),
            'Médio': data.get('Médio', {}).get('qtd', 0),
            'Difícil': data.get('Difícil', {}).get('qtd', 0),
        }
    return {
        'evolucao_mensal': charts.get('evolucao_mensal', []),
        'qualidade_turno_pct': turno_qualidade,
        'qualidade_turno': legacy_qualidade,
        'pressao_turno': charts.get('pressao_turno', []),
    }


def build_charts_full(df_cur, df_all, params: dict) -> dict:
    charts = build_charts_payload(df_cur, df_all, params)
    preview = build_charts_preview(df_cur, df_all, params)
    charts['qualidade_turno'] = preview['qualidade_turno']
    return charts


def build_top_reincidentes(df_cur, df_prev, limit: int = 5) -> list[dict]:
    df_reinc = reincidencia_table_full(df_prev, df_cur)
    rows = _enrich_reinc_rows(df_reinc, df_cur)
    rows = [r for r in rows if (r.get('reincidente') or '').lower() == 'sim']
    rows.sort(key=lambda x: x['ocorrencias'], reverse=True)
    return [{
        'matricula': r['matricula'],
        'name': r.get('nome') or 'N/A',
        'ocorrencias': r['ocorrencias'],
        'reincidente': r.get('reincidente', ''),
        'percentual': r.get('percentual'),
        'top_cenario': r.get('cenario', ''),
        'tempo_casa': r.get('tempo_casa', '—'),
        'tempo_etapa': r.get('tempo_etapa', '—'),
        'atividade': r.get('atividade_hc', ''),
        'turno': r.get('turno', ''),
    } for r in rows[:limit]]


def _reincidence_rows_all(df_cur, df_prev) -> list[dict]:
    """Todas as linhas de reincidência/alta frequência (sem paginação)."""
    df_reinc = reincidencia_table_full(df_prev, df_cur)
    rows = _enrich_reinc_rows(df_reinc, df_cur)

    def _is_reinc_sim(row):
        return (row.get('reincidente') or '').lower() == 'sim'

    rows = [r for r in rows if r['ocorrencias'] > 1 or _is_reinc_sim(r)]
    rows.sort(key=lambda x: x['ocorrencias'], reverse=True)
    return [{
        'matricula': r['matricula'],
        'name': r.get('nome') or 'N/A',
        'ocorrencias': r['ocorrencias'],
        'reincidente': r.get('reincidente', ''),
        'percentual': r.get('percentual'),
        'top_cenario': r.get('cenario', ''),
        'tempo_casa': r.get('tempo_casa', '—'),
        'tempo_etapa': r.get('tempo_etapa', '—'),
        'atividade': r.get('atividade_hc', ''),
        'turno': r.get('turno', ''),
    } for r in rows]


def build_reincidence_list_block(df_cur, df_prev, page: int = 1, page_size: int = 50) -> dict:
    all_rows = _reincidence_rows_all(df_cur, df_prev)
    total = len(all_rows)
    start = (page - 1) * page_size
    return {
        'results': all_rows[start:start + page_size],
        'count': total,
        'page': page,
        'page_size': page_size,
    }


def build_reincidence_by_turno_block(df_cur, df_prev, page: int = 1, page_size: int = 50) -> dict:
    df_reinc = reincidencia_table_full(df_prev, df_cur)
    payload = build_reincidence_by_turno(df_reinc, df_cur)

    all_rows = []
    for turno, rows in payload['tabelas'].items():
        for row in rows:
            row = dict(row)
            row['turno'] = turno
            all_rows.append(row)

    total = len(all_rows)
    start = (page - 1) * page_size
    end = start + page_size

    summary = payload['turnos']
    total_reincidentes = sum(t.get('reincidentes', 0) for t in summary)
    total_ocorrencias = sum(t.get('ocorrencias', 0) for t in summary)
    total_agentes = sum(t.get('agentes', 0) for t in summary)
    turno_dom = max(summary, key=lambda t: t.get('reincidentes', 0), default=None)

    bullets = []
    if turno_dom and turno_dom.get('reincidentes'):
        bullets.append(
            f"Turno com mais reincidentes: {turno_dom['turno']} "
            f"({turno_dom['reincidentes']} agentes)."
        )
    if total_agentes:
        bullets.append(
            f"{total_reincidentes} reincidentes em {total_agentes} agentes monitorados "
            f"({_pct(total_reincidentes, total_agentes) or 0}%)."
        )

    return {
        'summary': summary,
        'results': all_rows[start:end],
        'count': total,
        'page': page,
        'page_size': page_size,
        'kpis': {
            'total_reincidentes': total_reincidentes,
            'total_ocorrencias': total_ocorrencias,
            'total_agentes': total_agentes,
            'turno_dominante': turno_dom['turno'] if turno_dom else '—',
            'reinc_pct': _pct(total_reincidentes, total_agentes) or 0,
        },
        'pre_diagnostico': pre_diagnostico_block(
            veredito=f"{total_reincidentes} agentes reincidentes no recorte, em {len(summary)} turnos.",
            bullets=bullets,
            titulo='Leitura — Reincidência',
        ),
    }


def build_ult3m_block(user, query_params, params: dict) -> dict:
    scope_only = dict(params)
    scope_only.pop('start_date', None)
    scope_only.pop('end_date', None)
    df_scope = failures_qs_to_legacy_df(
        apply_failure_filters(Failure.objects.select_related('agent'), scope_only)
    )
    return build_ult3m_payload(df_scope, params) | {
        'pre_diagnostico': pre_diagnostico_block(
            veredito='Radar ULT3M — agentes com maior concentração de falhas nos últimos 3 meses.',
            titulo='Leitura ULT3M',
        ),
    }


def build_matrix_block(df_cur) -> dict:
    empty = {
        'rows': [], 'cols': [], 'matrix': [],
        'cenario_rows': [], 'cenario_cols': [], 'matrix_cenario_uf': [],
        'cenario_doc_rows': [], 'cenario_doc_cols': [], 'matrix_cenario_doc': [],
    }
    if df_cur.empty:
        return empty

    df_cur = df_cur.copy()
    df_cur['tipo_doc'] = df_cur.get('Tipo de documento', pd.Series(dtype=str)).fillna('Desconhecido').replace('', 'Desconhecido')
    df_cur['uf'] = df_cur.get('UF do documento', pd.Series(dtype=str)).fillna('Desconhecido').replace('', 'Desconhecido')
    df_cur['cenario'] = df_cur.get('Cenário Unificado', pd.Series(dtype=str)).fillna('Desconhecido').replace('', 'Desconhecido')

    def _build_matrix(row_col, col_col, top_rows=8, top_cols=12):
        top_rows_list = df_cur[row_col].value_counts().head(top_rows).index.tolist()
        top_cols_list = df_cur[col_col].value_counts().head(top_cols).index.tolist()
        df_f = df_cur[df_cur[row_col].isin(top_rows_list) & df_cur[col_col].isin(top_cols_list)]
        cross = df_f.groupby([row_col, col_col]).size().unstack(fill_value=0)
        matrix = []
        for r in top_rows_list:
            vals = {}
            for c in top_cols_list:
                vals[c] = int(cross.loc[r, c]) if r in cross.index and c in cross.columns else 0
            matrix.append({row_col: r, 'valores': vals})
        return top_rows_list, top_cols_list, matrix

    top_types, top_ufs, heatmap_data = _build_matrix('tipo_doc', 'uf', 8, 12)
    top_cenarios, top_ufs2, cenario_uf = _build_matrix('cenario', 'uf', 8, 12)
    top_cenarios2, top_docs, cenario_doc = _build_matrix('cenario', 'tipo_doc', 8, 8)

    return {
        'rows': top_types,
        'cols': top_ufs,
        'matrix': [{'tipo_doc': r['tipo_doc'], 'valores': r['valores']} for r in heatmap_data],
        'cenario_rows': top_cenarios,
        'cenario_cols': top_ufs2,
        'matrix_cenario_uf': [{'cenario': r['cenario'], 'valores': r['valores']} for r in cenario_uf],
        'cenario_doc_rows': top_cenarios2,
        'cenario_doc_cols': top_docs,
        'matrix_cenario_doc': [{'cenario': r['cenario'], 'valores': r['valores']} for r in cenario_doc],
    }


def build_rank_delta_block(df_cur, df_prev, col: str = 'Cenário Unificado') -> dict:
    return {
        'cenario': build_rank_delta_payload(df_cur, df_prev, 'Cenário Unificado'),
        'novo_cenario': build_rank_delta_payload(df_cur, df_prev, 'Novo cenário'),
        'formatacao_fonte': build_rank_delta_payload(
            df_cur[df_cur['Cenário Unificado'].astype(str).str.contains('format|fonte|desalinh', case=False, na=False)]
            if not df_cur.empty and 'Cenário Unificado' in df_cur.columns else df_cur,
            df_prev,
            'UF do documento' if 'UF do documento' in df_cur.columns else col,
        ),
    }


def build_clientes_workflows_block(df_cur, params: dict) -> dict:
    payload = build_clientes_workflows(df_cur)
    falhas_total = payload.get('total', 0)
    cont_qs = Contestation.objects.all()
    loc = params.get('localidade')
    if loc == '__BLOCKED__':
        cont_qs = cont_qs.none()
    else:
        cont_qs = filter_qs_by_localidade(cont_qs, 'localidade', loc or 'Geral')
    start_date = params.get('start_date')
    end_date = params.get('end_date')
    if start_date:
        cont_qs = cont_qs.filter(data__gte=start_date)
    if end_date:
        cont_qs = cont_qs.filter(data__lte=end_date)

    cont_total = cont_qs.count()
    cont_interna = cont_qs.filter(fonte__iexact='Interna').count()
    cont_externa = cont_qs.filter(fonte__iexact='Externa').count()
    origem_total = falhas_total + cont_total

    payload['origem'] = {
        'auditoria': falhas_total,
        'auditoria_pct': _pct(falhas_total, origem_total) or 0,
        'contestacao': cont_total,
        'contestacao_pct': _pct(cont_total, origem_total) or 0,
        'contestacao_interna': cont_interna,
        'contestacao_externa': cont_externa,
        'total': origem_total,
    }

    top_cli = payload.get('top_clientes') or []
    top_wf = payload.get('top_workflows') or []
    bullets = []
    if top_cli:
        bullets.append(
            f"Cliente com mais falhas: {top_cli[0]['nome']} "
            f"({top_cli[0]['qtd']} — {top_cli[0]['percentual']}% do total)."
        )
    if top_wf:
        bullets.append(
            f"Workflow mais recorrente: {top_wf[0]['nome']} "
            f"({top_wf[0]['qtd']} — {top_wf[0]['percentual']}%)."
        )
    if cont_total:
        bullets.append(
            f"{cont_total} contestações no período ({payload['origem']['contestacao_pct']}% dos apontamentos) — "
            f"{cont_interna} internas, {cont_externa} externas."
        )
    payload['pre_diagnostico'] = pre_diagnostico_block(
        veredito=(
            f"{falhas_total} falhas de auditoria e {cont_total} contestações compõem os apontamentos do período."
        ),
        bullets=bullets,
        titulo='Leitura — Clientes e Workflows',
    )
    return payload


def build_consolidado_tiles_block(df_all, df_cur, df_prev, params: dict, scope: dict) -> dict:
    """Consolidado leve — só tiles (sem por_localidade). Usado pelo agregador /diagnostico/."""
    payload = build_consolidado_payload(df_all, df_cur, df_prev, params)
    payload['scope'] = scope
    return payload


def build_consolidado_block(user, query_params, df_all, df_cur, df_prev, params: dict, scope: dict) -> dict:
    """Consolidado completo com por_localidade — espelha GET /consolidado/ legado."""
    payload = build_consolidado_payload(df_all, df_cur, df_prev, params)
    payload['scope'] = scope
    if scope.get('scope') == 'global':
        por_loc = {}
        qp = query_params.copy()
        for loc in ('Brasília', 'São Carlos', 'Geral'):
            qp_loc = qp.copy()
            qp_loc['localidade'] = loc
            da, dc, dp, p_loc, _ = load_period_frames(user, qp_loc)
            por_loc[loc] = build_consolidado_payload(da, dc, dp, p_loc)['tiles']
        payload['por_localidade'] = por_loc
    return payload


def _parse_pct(value) -> float | None:
    if value is None or value == '':
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace('%', '').replace(',', '.')
    try:
        return float(text)
    except ValueError:
        return None


def _count_agentes_com_falha(df_cur) -> int:
    if df_cur is None or getattr(df_cur, 'empty', True):
        return 0
    col = 'Matrícula Agente' if 'Matrícula Agente' in df_cur.columns else None
    if not col:
        for candidate in ('matricula', 'Matricula Agente', 'matricula_agente'):
            if candidate in df_cur.columns:
                col = candidate
                break
    if not col:
        return 0
    series = df_cur[col].dropna().astype(str).str.strip()
    series = series[series != '']
    return int(series.nunique())


def build_dashboard_summary(user, query_params) -> dict:
    t0 = time.perf_counter()
    df_all, df_cur, df_prev, params, scope = load_period_frames(user, query_params)
    warnings: list[str] = []

    exec_payload = build_executive_payload(df_all, df_cur, df_prev, params)
    kpis = build_kpis_from_executive_payload(exec_payload, params)
    executive = build_executive_from_payload(exec_payload, scope)
    charts_preview = build_charts_preview(df_cur, df_all, params)
    top_reincidentes = build_top_reincidentes(df_cur, df_prev, limit=5)

    alerts: list = []
    try:
        alerts = build_dashboard_alerts(user, query_params)
    except Exception:
        alerts = []
        warnings.append('alerts_unavailable')

    comparativo_preview = None
    if scope.get('scope') == 'global':
        try:
            comparativo_preview = build_comparativo_bsb_sc(user, params)
            comparativo_preview['scope'] = scope
        except Exception:
            comparativo_preview = None
            warnings.append('comparativo_preview_unavailable')

    suporte = kpis.get('suporte_resumo') or {}
    pre_diag = kpis.get('pre_diagnostico') or executive.get('pre_diagnostico')
    leitura = build_dashboard_leitura(
        pre_diagnostico=pre_diag if isinstance(pre_diag, dict) else None,
        top_cenario_nome=kpis.get('top1_cenario_nome'),
        top_cenario_pct=_parse_pct(kpis.get('top1_cenario_pct')),
        reinc_total=kpis.get('reinc_total'),
        variacao_delta=kpis.get('variacao_delta'),
    )
    agentes_com_falha = _count_agentes_com_falha(df_cur)
    kpis['agentes_com_falha'] = agentes_com_falha
    prioridades = build_dashboard_prioridades(
        top_cenario_nome=kpis.get('top1_cenario_nome'),
        top_cenario_qtd=kpis.get('top1_cenario_qtd'),
        top_cenario_pct=_parse_pct(kpis.get('top1_cenario_pct')),
        reinc_total=kpis.get('reinc_total'),
        agentes_com_falha=agentes_com_falha,
        suporte_nc_pct=_parse_pct(suporte.get('nc_oficial_pct')),
        suporte_total=suporte.get('total'),
    )
    causas_top = []
    for nome, qtd in _top_cenarios(df_cur, 3):
        if not nome or nome in ('—', '-', 'N/A'):
            continue
        pct_val = (qtd / len(df_cur) * 100.0) if len(df_cur) else None
        causas_top.append({
            'nome': nome,
            'qtd': qtd,
            'pct': round(pct_val, 1) if pct_val is not None else None,
        })

    bsb_sc_compact = None
    if comparativo_preview and isinstance(comparativo_preview, dict):
        bsb = (comparativo_preview.get('brasilia') or {}).get('falhas') or {}
        sc = (comparativo_preview.get('sao_carlos') or {}).get('falhas') or {}
        bsb_sc_compact = {
            'bsb_total': bsb.get('total') or 0,
            'sc_total': sc.get('total') or 0,
            'sc_sem_dados': bool((comparativo_preview.get('meta') or {}).get('sc_sem_dados')),
        }

    duration_ms = int((time.perf_counter() - t0) * 1000)
    return {
        'kpis': kpis,
        'executive': executive,
        'charts_preview': charts_preview,
        'top_reincidentes': top_reincidentes,
        'alerts': alerts,
        'comparativo_preview': comparativo_preview,
        'leitura': leitura,
        'prioridades': prioridades,
        'causas_top': causas_top,
        'bsb_sc_compact': bsb_sc_compact,
        'agentes_prioritarios': top_reincidentes,
        'meta': _meta_block(params, scope, warnings, duration_ms=duration_ms),
    }


def build_diagnostico_summary(user, query_params) -> dict:
    t0 = time.perf_counter()
    df_all, df_cur, df_prev, params, scope = load_period_frames(user, query_params)
    warnings: list[str] = []

    rank_delta = build_rank_delta_block(df_cur, df_prev)
    matrix = build_matrix_block(df_cur)
    clientes_workflows = build_clientes_workflows_block(df_cur, params)
    consolidado = build_consolidado_tiles_block(df_all, df_cur, df_prev, params, scope)

    try:
        qs = Support.objects.select_related('agent')
        qs_cur = apply_support_filters(qs, params)
        df_sup = supports_qs_to_legacy_df(qs_cur)
        support_bridge_preview = build_support_falhas_bridge(
            df_sup, params, user=user, query_params=query_params,
        )
    except Exception:
        support_bridge_preview = _empty_support_bridge()
        warnings.append('support_bridge_unavailable')

    try:
        train_qs = _training_qs_filtered(params)
        training_bridge_preview = build_training_reincidence_bridge(
            train_qs, params, user=user, query_params=query_params,
        )
    except Exception:
        training_bridge_preview = _empty_training_bridge()
        warnings.append('training_bridge_unavailable')

    duration_ms = int((time.perf_counter() - t0) * 1000)

    impacto_full = list((rank_delta.get('cenario') or {}).get('impacto') or [])
    impacto = impacto_full[:5]
    total_periodo = 0
    tiles = (consolidado or {}).get('tiles') or {}
    for key in ('total_periodo', 'oficial_periodo'):
        block = tiles.get(key) or {}
        if isinstance(block, dict) and block.get('valor') is not None:
            total_periodo = int(block.get('valor') or 0)
            break
    if not total_periodo:
        total_periodo = len(df_cur) if df_cur is not None and not getattr(df_cur, 'empty', True) else 0
    if not total_periodo and impacto_full:
        total_periodo = sum(int(r.get('cur') or 0) for r in impacto_full)

    combinacoes = []
    matrix_cu = matrix.get('matrix_cenario_uf') or []
    cols_cu = matrix.get('cenario_cols') or []
    pairs = []
    for row in matrix_cu:
        cenario = row.get('cenario') or ''
        valores = row.get('valores') or {}
        for col in cols_cu:
            qtd = int(valores.get(col) or 0)
            if qtd > 0:
                pairs.append((cenario, col, qtd))
    pairs.sort(key=lambda x: x[2], reverse=True)
    for idx, (cenario, uf, qtd) in enumerate(pairs[:8]):
        share = (qtd / total_periodo * 100) if total_periodo else 0
        rank = idx + 1
        if rank == 1 and share >= 10:
            level = PRIORITY_ALTA
        elif rank <= 3:
            level = PRIORITY_MEDIA
        else:
            level = PRIORITY_BAIXA
        combinacoes.append(
            priority_item(
                level,
                f'{cenario} × {uf}',
                f'{qtd} falhas ({share:.0f}% das falhas do período)' if total_periodo else f'{qtd} falhas',
                {
                    'type': 'cases',
                    'module': 'diagnostico',
                    'label': 'Ver casos',
                    'filters': {'cenario': cenario, 'uf_documento': uf},
                },
            )
        )

    top = impacto[0] if impacto else None
    top_nome = (top or {}).get('label') or ''
    top_qtd = int((top or {}).get('cur') or 0)
    top_share = (top_qtd / total_periodo * 100.0) if total_periodo and top_qtd else 0.0
    origem = (clientes_workflows or {}).get('origem') or {}
    aud = origem.get('auditoria')
    cont = origem.get('contestacao')
    cta_filters = {'cenario': top_nome} if top_nome else {}
    leitura = {
        'titulo': 'Diagnóstico do período',
        'veredito': (
            f'{top_qtd} falhas — {top_share:.1f}% das falhas do período em um cenário: {top_nome}.'
            if top_nome else
            'Sem concentração clara de cenário no período.'
        ),
        'bullets': [
            f'Cenários ativos: {len(impacto_full)}',
            (
                f'Falhas do período: {total_periodo}'
                f' · Apontamentos de auditoria: {aud if aud is not None else "—"}'
                f' · Contestações recebidas: {cont if cont is not None else "—"}'
                ' (não somam o mesmo total)'
            ),
        ],
        'tendencia': '',
        'cta': {
            'type': 'cases',
            'module': 'diagnostico',
            'label': 'Ver casos do cenário',
            'filters': cta_filters,
        },
        'cta_secondary': {
            'type': 'navigate',
            'module': 'reincidencia',
            'label': 'Ver pessoas relacionadas',
        },
    }

    # Pareto de cenários (top 10 + % acumulado)
    pareto = []
    running = 0.0
    for row in impacto_full[:10]:
        label = (row.get('label') or '').strip()
        qtd = int(row.get('cur') or 0)
        if not label or qtd <= 0:
            continue
        share_pct = (qtd / total_periodo * 100.0) if total_periodo else 0.0
        running += share_pct
        pareto.append({
            'label': label,
            'qtd': qtd,
            'share_pct': round(share_pct, 1),
            'cumul_pct': round(min(running, 100.0), 1),
        })

    # Prioridade #1: combinação se forte; senão cenário top
    prioridade = None
    if combinacoes:
        top_comb = combinacoes[0]
        prioridade = {
            'level': top_comb.get('level') or PRIORITY_MEDIA,
            'title': top_comb.get('title') or '',
            'evidence': top_comb.get('evidence') or '',
            'hint': 'Concentra o esforço aqui — maior fatia do período nesta combinação.',
            'cta': top_comb.get('cta') or {
                'type': 'cases',
                'label': 'Ver casos',
                'filters': {},
            },
            'cta_secondary': {
                'type': 'navigate',
                'module': 'reincidencia',
                'label': 'Ver pessoas',
            },
        }
    elif top_nome:
        level = PRIORITY_ALTA if top_share >= 20 else (PRIORITY_MEDIA if top_share >= 10 else PRIORITY_BAIXA)
        prioridade = {
            'level': level,
            'title': top_nome,
            'evidence': (
                f'{top_qtd} falhas ({top_share:.0f}% das falhas do período)'
                if total_periodo else f'{top_qtd} falhas'
            ),
            'hint': 'Concentra o esforço aqui — cenário com maior volume no período.',
            'cta': {
                'type': 'cases',
                'label': 'Ver casos',
                'filters': {'cenario': top_nome},
            },
            'cta_secondary': {
                'type': 'navigate',
                'module': 'reincidencia',
                'label': 'Ver pessoas',
            },
        }

    clientes_distintos = int((clientes_workflows or {}).get('clientes_distintos') or 0)
    workflows_distintos = int((clientes_workflows or {}).get('workflows_distintos') or 0)
    top_cli = (clientes_workflows or {}).get('top_cliente')
    top_wf = (clientes_workflows or {}).get('top_workflow')

    kpis_diag = {
        'falhas': total_periodo,
        'cenarios_ativos': len(impacto_full),
        'auditoria': aud,
        'contestacoes': cont,
        'clientes': clientes_distintos,
        'workflows': workflows_distintos,
        'top_cliente': top_cli,
        'top_workflow': top_wf,
    }

    return {
        'rank_delta': rank_delta,
        'matrix': matrix,
        'clientes_workflows': clientes_workflows,
        'consolidado': consolidado,
        'support_bridge_preview': support_bridge_preview,
        'training_bridge_preview': training_bridge_preview,
        'leitura': leitura,
        'combinacoes': combinacoes,
        'prioridade': prioridade,
        'pareto': pareto,
        'kpis': kpis_diag,
        'meta': _meta_block(params, scope, warnings, duration_ms=duration_ms),
    }


def build_reincidence_summary(user, query_params) -> dict:
    t0 = time.perf_counter()
    df_all, df_cur, df_prev, params, scope = load_period_frames(user, query_params)
    warnings: list[str] = []

    by_turno = build_reincidence_by_turno_block(df_cur, df_prev)
    all_rows = _reincidence_rows_all(df_cur, df_prev)
    page = 1
    page_size = 50
    reincidence_list = {
        'results': all_rows[(page - 1) * page_size:page * page_size],
        'count': len(all_rows),
        'page': page,
        'page_size': page_size,
    }

    try:
        ult3m = build_ult3m_block(user, query_params, params)
    except Exception:
        ult3m = {'agentes': [], 'kpis': {}}
        warnings.append('ult3m_unavailable')

    charts = build_charts_full(df_cur, df_all, params)

    recorrente_mats = {
        str(a.get('matricula') or '')
        for a in (ult3m.get('agentes') or [])
        if a.get('recorrente')
    }
    ult3m_by_mat = {
        str(a.get('matricula') or ''): a
        for a in (ult3m.get('agentes') or [])
    }
    rows = all_rows
    fila = []
    for r in rows:
        mat = str(r.get('matricula') or '')
        oficial = (r.get('reincidente') or '').lower() == 'sim'
        occ = int(r.get('ocorrencias') or r.get('count') or 0)
        alta_freq = occ > 1 and not oficial
        recorrente_4m = mat in recorrente_mats
        critico = occ >= 2 or recorrente_4m
        level = agent_priority(
            oficial=oficial,
            critico=critico and oficial,
            alta_frequencia=alta_freq,
            recorrente_4m=recorrente_4m,
        )
        if oficial and critico:
            level = 'alta'
        sinais = []
        if oficial:
            sinais.append('oficial')
        if alta_freq:
            sinais.append('alta_freq')
        if recorrente_4m:
            sinais.append('recorrente_4m')
        if critico:
            sinais.append('critico')
        agent_ult = ult3m_by_mat.get(mat)
        falhas_hist = agent_ult.get('falhas') if isinstance(agent_ult, dict) else None
        historico_4m = None
        if isinstance(falhas_hist, dict):
            historico_4m = {
                'ref': falhas_hist.get('ref'),
                'm1': falhas_hist.get('m1'),
                'm2': falhas_hist.get('m2'),
                'm3': falhas_hist.get('m3'),
            }
        fila.append({
            **r,
            'count': occ,
            'oficial': oficial,
            'alta_frequencia': alta_freq,
            'recorrente_4m': recorrente_4m,
            'critico': critico,
            'sinais': sinais,
            'priority': level,
            'historico_4m': historico_4m,
        })
    fila.sort(key=lambda x: {'alta': 0, 'media': 1, 'baixa': 2}.get(x.get('priority'), 9))

    oficiais = sum(1 for f in fila if f.get('oficial'))
    alta = sum(1 for f in fila if f.get('alta_frequencia'))
    recorrentes = int((ult3m.get('kpis') or {}).get('recorrentes_4m') or len(recorrente_mats))
    criticos = sum(1 for f in fila if f.get('critico'))
    leitura_kpis = {
        'oficiais': oficiais,
        'alta_frequencia': alta,
        'recorrentes_4m': recorrentes,
        'criticos': criticos,
    }
    pre = by_turno.get('pre_diagnostico') if isinstance(by_turno, dict) else None
    leitura = {
        'titulo': 'Leitura da reincidência',
        'veredito': (pre or {}).get('veredito') or (
            f'{oficiais} reincidente(s) oficial(is), {alta} em alta frequência e {recorrentes} recorrente(s) em 4 meses.'
        ),
        'bullets': (pre or {}).get('bullets') or [
            f'Oficiais: {oficiais}',
            f'Alta frequência: {alta}',
            f'Recorrentes 4M: {recorrentes}',
            f'Com sinal crítico: {criticos}',
        ],
        'tendencia': (pre or {}).get('tendencia') or '',
        'cta': {'type': 'navigate', 'module': 'treinamentos', 'label': 'Ver capacitação'},
    }

    duration_ms = int((time.perf_counter() - t0) * 1000)
    return {
        'by_turno': by_turno,
        'list': reincidence_list,
        'ult3m': ult3m,
        'charts': charts,
        'leitura': leitura,
        'fila': fila,
        'kpis': leitura_kpis,
        'meta': _meta_block(params, scope, warnings, duration_ms=duration_ms),
    }
