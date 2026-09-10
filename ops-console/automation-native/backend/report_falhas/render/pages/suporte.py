# -*- coding: utf-8 -*-

"""Páginas HTML completas (Etapa 2)."""

from datetime import date, datetime, timedelta
from io import BytesIO
import base64
import html as _html
import json
import re
import unicodedata

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

from report_falhas.io.data_loader import (
    normalize_text,
    safe_str,
    safe_to_datetime,
    norm_matricula,
    norm_protocolo,
)
from report_falhas.assets import resolve_logo_data_uri
from report_falhas.config_report import (
    COL_CLIENTE,
    COL_DATA,
    COL_DATA_ANALISE,
    COL_DATA_AUDITORIA,
    COL_MATRICULA,
    COL_CENARIO as _COL_CENARIO_DEFAULT,
    COL_PROTOCOLO,
    COL_WORKFLOW_BASE,
    SUP_COL_DATA,
    SUP_COL_PROTO,
    SUP_COL_CLIENTE,
    SUP_COL_WORKFLOW,
    SUP_COL_AGENTE,
    SUP_COL_LIDER_SOL,
    SUP_COL_TIPO_SOL,
    SUP_COL_DUVIDA,
    SUP_COL_CONCL,
    SUP_COL_LOCAL_SOL,
    SUP_COL_OBS,
    SUP_COL_OBS_SUP,
    SUP_COL_CONFORM,
    SUP_COL_DIFIC,
    SUP_COL_CRIT,
    SUP_COL_TIPO_DOC,
    SUP_COL_UF_EMIS,
)
from report_falhas.contestacoes import get_contest_state
from report_falhas.html_blocks import format_html as format_html_blocks
from report_falhas.html_blocks import table_row_cols as table_row_cols_blocks
from report_falhas.kpis import is_formatacao_fonte
from report_falhas.matricula_utils import clean_matricula_unified as _clean_matricula_pair
from report_falhas.periods import (
    add_months_first_day,
    april_start,
    filter_by_date_range_on,
    first_day,
    get_comparativo_by_same_period_on,
    month_last_day,
    months_from_to,
    prev_month_first_day,
)
from report_falhas import periods as _periods_mod
from report_falhas.utils_report import (
    MESES_ABREV_BR,
    build_metric_filter_buttons,
    fmt_data_br,
    fmt_delta_html,
    fmt_mes_ano_br,
)

# Mantido para compatibilidade: pode ser atualizado por report_falhas_criticas.read_base
COL_CENARIO = _COL_CENARIO_DEFAULT

from report_falhas.render.pages.common import wrap_simple_page, _top1, _kpi_tile_legacy




def build_suporte_page_html(df_sup: pd.DataFrame, cur_start: date, cur_end: date, scope_name: str = '', nome_map: dict | None = None, df_hc: pd.DataFrame | None = None,) -> str:
    """Gera HTML da aba Suporte (TEAMS).

    Nota: NÃO usa a coluna 'Agente suporte' (coluna H).
    A tabela detalha por 'Agente' (quem abriu/solicitou).
    """

    # Padrão final (Suporte): Matrícula visível + Nome via hover (title)
    from html import escape as _esc

    nome_map = nome_map or {}  # mantido por compatibilidade; Suporte usa HC (nome→matrícula)

    def _hc_col(df: pd.DataFrame, expected: str) -> str | None:
        exp = normalize_text(expected)
        for c in df.columns:
            cc = str(c).replace('\r', '').replace('\n', '').strip()
            if normalize_text(cc) == exp:
                return c
        return None

    # Mapa Nome(normalize_text) -> matrícula (HC), priorizando registro mais recente (data_inicial/id)
    name2mat: dict[str, str] = {}

    try:
        if df_hc is not None and not df_hc.empty:
            col_nome = _hc_col(df_hc, 'nome_agente')
            col_mat = _hc_col(df_hc, 'matricula_agente')
            col_data = _hc_col(df_hc, 'data_inicial')
            col_id = _hc_col(df_hc, 'id')

            if col_nome and col_mat:
                dfx = df_hc.copy()

                if col_data and (not pd.api.types.is_datetime64_any_dtype(dfx[col_data])):
                    dfx[col_data] = safe_to_datetime(dfx[col_data])

                if col_id:
                    dfx['_id_num'] = pd.to_numeric(dfx[col_id], errors='coerce').fillna(-1)
                else:
                    dfx['_id_num'] = -1

                dfx['_k_name'] = dfx[col_nome].apply(lambda x: normalize_text(str(x).replace('\r', ' ').replace('\n', ' ').strip()))
                dfx['_mat'] = dfx[col_mat].apply(lambda x: safe_str(x))
                dfx = dfx[(dfx['_k_name'] != '') & (dfx['_mat'] != '')].copy()

                if not dfx.empty:
                    sort_cols = ['_k_name'] + ([col_data] if col_data else []) + ['_id_num']
                    dfx = dfx.sort_values(sort_cols, ascending=True)
                    last = dfx.groupby('_k_name')['_mat'].last()
                    name2mat = {str(k): str(v) for k, v in last.items()}

    except Exception:
        name2mat = {}

    def _agent_span(nome_raw) -> str:
        # Suporte: coluna Agente traz NOME. Exibir apenas MATRÍCULA e colocar NOME no title.
        nome = safe_str(nome_raw).replace('\r', ' ').replace('\n', ' ').strip()
        key = normalize_text(nome)
        matricula = safe_str(name2mat.get(key, ''))
        disp = _esc(matricula) if matricula else '—'
        if nome:
            return f'<span title="{_esc(nome, quote=True)}" style="cursor:help;">{disp}</span>'
        return disp

    if df_sup is None or df_sup.empty or SUP_COL_DATA not in df_sup.columns:
        return wrap_simple_page('🛟 Suporte (TEAMS)', "<div style='font-size:12px;color:#6c757d;'>Sem dados na aba Suporte.</div>")

    df_sup_scope = df_sup
    periodo_obs_html = ''

    scope_norm = normalize_text(scope_name)
    scope_localidade = None
    if scope_norm:
        if 'brasilia' in scope_norm:
            scope_localidade = 'brasilia'
        elif 'sao carlos' in scope_norm or 'saocarlos' in scope_norm:
            scope_localidade = 'sao carlos'

    if scope_localidade:
        scoped = False
        loc_col = next(
            (
                c for c in df_sup_scope.columns
                if normalize_text(str(c).replace('\r', '').replace('\n', '').strip()) == normalize_text(SUP_COL_LOCAL_SOL)
            ),
            None,
        )
        if loc_col:
            df_tmp = df_sup_scope.copy()
            df_tmp['_loc_sup'] = df_tmp[loc_col].apply(lambda x: normalize_text(safe_str(x)))
            mask_loc = df_tmp['_loc_sup'].str.contains(scope_localidade, na=False)
            if mask_loc.any():
                df_sup_scope = df_tmp[mask_loc].copy()
                scoped = True

        if not scoped and (df_hc is not None) and (not df_hc.empty):
            col_mat_hc = _hc_col(df_hc, 'matricula_agente')
            col_loc_hc = _hc_col(df_hc, 'localidade')

            if col_mat_hc and col_loc_hc:
                mat2loc = {}
                try:
                    dfx_hc = df_hc[[col_mat_hc, col_loc_hc]].copy()
                    dfx_hc['_mat'] = dfx_hc[col_mat_hc].apply(norm_matricula)
                    dfx_hc['_loc'] = dfx_hc[col_loc_hc].apply(lambda x: normalize_text(safe_str(x)))
                    dfx_hc = dfx_hc[(dfx_hc['_mat'] != '') & (dfx_hc['_loc'] != '')]
                    if not dfx_hc.empty:
                        mat2loc = dfx_hc.groupby('_mat')['_loc'].last().to_dict()
                except Exception:
                    mat2loc = {}

                ag_col_local = next((c for c in df_sup_scope.columns if normalize_text(str(c).replace('\r', '').replace('\n', '').strip()) == normalize_text('Agente')), None)
                if mat2loc and ag_col_local:
                    df_sup_scope = df_sup_scope.copy()

                    def _mat_from_agente(x):
                        raw = safe_str(x).replace('\r', ' ').replace('\n', ' ').strip()
                        from_name = name2mat.get(normalize_text(raw), '')
                        if from_name:
                            return norm_matricula(from_name)
                        # Schema TBSO: coluna Agente já traz matrícula
                        cand = norm_matricula(raw)
                        return cand if cand else ''

                    df_sup_scope['_mat_norm'] = df_sup_scope[ag_col_local].apply(_mat_from_agente)
                    df_sup_scope['_loc_hc'] = df_sup_scope['_mat_norm'].map(mat2loc).fillna('')
                    mask_hc = df_sup_scope['_loc_hc'].str.contains(scope_localidade, na=False)
                    if mask_hc.any():
                        df_sup_scope = df_sup_scope[mask_hc].copy()
                        scoped = True

        if scoped:
            periodo_obs_html += f"<div class='badge brand' style='margin-bottom:10px;'>Filtro de BU/local aplicado: {scope_name}</div>"

    df_cur = filter_by_date_range_on(df_sup_scope, cur_start, cur_end, SUP_COL_DATA)
    if df_cur.empty:
        # 1) tenta o mês inteiro de referência
        df_cur = filter_by_date_range_on(df_sup_scope, cur_start, month_last_day(cur_start), SUP_COL_DATA)
        if not df_cur.empty:
            periodo_obs_html = f"<div class='badge brand' style='margin-bottom:10px;'>Exibindo o mês completo de referência: {cur_start.strftime('%m/%Y')}</div>"
        else:
            # 2) fallback para o último mês com dados na aba Suporte
            try:
                dmax = pd.Timestamp(df_sup_scope[SUP_COL_DATA].max())
                if pd.notna(dmax):
                    fb_start = date(int(dmax.year), int(dmax.month), 1)
                    fb_end = month_last_day(fb_start)
                    df_cur = filter_by_date_range_on(df_sup_scope, fb_start, fb_end, SUP_COL_DATA)
                    if not df_cur.empty:
                        periodo_obs_html = f"<div class='badge brand' style='margin-bottom:10px;'>Sem dados no período solicitado; exibindo último mês com dados: {fb_start.strftime('%m/%Y')}</div>"
            except Exception:
                pass
    if df_cur.empty:
        return wrap_simple_page('🛟 Suporte (TEAMS)', "<div style='font-size:12px;color:#6c757d;'>Sem dados de Suporte no período atual.</div>")

    prev_start = prev_month_first_day(cur_start)
    total_atual, total_prev, p_ini, p_fim = get_comparativo_by_same_period_on(df_sup_scope, cur_start, cur_end, prev_start, SUP_COL_DATA)
    comp_txt = f"{p_ini.strftime('%d/%m')} → {p_fim.strftime('%d/%m')}" if (p_ini and p_fim) else 'Sem comparação'
    df_prev_mtd = (
        filter_by_date_range_on(df_sup_scope, p_ini, p_fim, SUP_COL_DATA)
        if (p_ini and p_fim) else pd.DataFrame()
    )

    if 'Conformidade (Regra)' in df_cur.columns:
        conform_sheet = df_cur['Conformidade (Regra)'].apply(normalize_text)
    else:
        conform_sheet = pd.Series([''] * len(df_cur), index=df_cur.index)

    def _compute_conform_rule(row) -> str:
        duv = safe_str(row.get(SUP_COL_DUVIDA, ''))
        conc = safe_str(row.get(SUP_COL_CONCL, ''))
        dif = normalize_text(safe_str(row.get(SUP_COL_DIFIC, '')))
        crit = normalize_text(safe_str(row.get(SUP_COL_CRIT, '')))

        if dif in {'medio', 'dificil', 'complexo'}:
            return 'CONFORME'
        if crit == 'sim' and dif == 'facil':
            return 'NÃO CONFORME'
        if normalize_text(duv) and normalize_text(duv) == normalize_text(conc):
            return 'CONFORME'
        return 'NÃO CONFORME'

    conform_calc = df_cur.apply(_compute_conform_rule, axis=1) if len(df_cur) else pd.Series(dtype='object')
    conform_calc = conform_calc.apply(normalize_text) if len(conform_calc) else conform_calc

    mask_nc = conform_calc == 'nao conforme'
    mask_c = conform_calc == 'conforme'
    conf = int(mask_c.sum())
    nconf = int(mask_nc.sum())
    pct_nconf = (nconf / len(df_cur) * 100.0) if len(df_cur) else 0.0

    has_r3_cols = (SUP_COL_CRIT in df_cur.columns) and (SUP_COL_DIFIC in df_cur.columns)
    if has_r3_cols:
        crit = df_cur[SUP_COL_CRIT].apply(normalize_text)
        dif = df_cur[SUP_COL_DIFIC].apply(normalize_text)
        mask_r3 = (crit == 'sim') & (dif == 'facil')
    else:
        mask_r3 = pd.Series([False] * len(df_cur), index=df_cur.index)
    r3 = int(mask_r3.sum())

    nconf_sheet = int((conform_sheet == 'nao conforme').sum())
    if nconf_sheet != nconf:
        consistency_html = (
            "<div style='background:#EFF6FF;border:1px solid #93C5FD;border-radius:8px;padding:8px 10px;margin:8px 0 10px;font-size:12px;color:#1D4ED8;'>"
            f"NC oficial calculado pelo painel = <b>{nconf}</b> ({pct_nconf:.1f}%) | NC da coluna da planilha = <b>{nconf_sheet}</b>. "
            "A regra oficial usada no painel inclui a Regra 3."
            "</div>"
        )
    else:
        consistency_html = (
            "<div style='background:#ECFDF3;border:1px solid #A7F3D0;border-radius:8px;padding:8px 10px;margin:8px 0 10px;font-size:12px;color:#065F46;'>"
            f"NC oficial calculado pelo painel = <b>{nconf}</b> ({pct_nconf:.1f}%). A Regra 3 já está incluída na conformidade oficial."
            "</div>"
        )

    top_duv = df_cur[SUP_COL_DUVIDA].apply(safe_str) if SUP_COL_DUVIDA in df_cur.columns else pd.Series(dtype='object')
    top_duv = top_duv[top_duv != ''].value_counts().head(5)
    top_conc = df_cur[SUP_COL_CONCL].apply(safe_str) if SUP_COL_CONCL in df_cur.columns else pd.Series(dtype='object')
    top_conc = top_conc[top_conc != ''].value_counts().head(5)

    df_r3 = df_cur[mask_r3].copy() if r3 > 0 else df_cur.iloc[0:0].copy()
    r3_top_duv = (df_r3[SUP_COL_DUVIDA].apply(safe_str) if (not df_r3.empty and SUP_COL_DUVIDA in df_r3.columns) else pd.Series(dtype='object'))
    r3_top_duv = r3_top_duv[r3_top_duv != ''].value_counts().head(5)
    r3_top_conc = (df_r3[SUP_COL_CONCL].apply(safe_str) if (not df_r3.empty and SUP_COL_CONCL in df_r3.columns) else pd.Series(dtype='object'))
    r3_top_conc = r3_top_conc[r3_top_conc != ''].value_counts().head(5)

    def _mini_list(vc: pd.Series) -> str:
        if vc is None or vc.empty:
            return "<div style='font-size:12px;color:#6c757d;'>Sem dados.</div>"
        items = ''.join([f"<li style='margin:4px 0;'>{safe_str(k)} <span style='color:#6c757d'>({int(v)})</span></li>" for k, v in vc.items()])
        return f"<ul style='margin:6px 0 0;padding-left:18px;font-size:12px;'>{items}</ul>"

    tab_html = "<div style='font-size:12px;color:#6c757d;'>Coluna 'Agente' não encontrada para detalhar.</div>"
    top_agentes_volume_html = "<div style='font-size:12px;color:#6c757d;'>Coluna 'Agente' não encontrada para ranking de volume.</div>"
    top_clientes_html = "<div style='font-size:12px;color:#6c757d;'>Sem críticos (Regra 3) para priorização por cliente.</div>"
    ag_crit_qtd = 0
    ag_volume_qtd = 0
    cli_crit_qtd = 0

    if not df_r3.empty and SUP_COL_CLIENTE in df_r3.columns:
        vc_cli = df_r3[SUP_COL_CLIENTE].apply(safe_str)
        vc_cli = vc_cli[vc_cli != ''].value_counts().head(10)
        cli_crit_qtd = int(vc_cli.shape[0])
        if not vc_cli.empty:
            rows_cli = ''.join([
                "<tr>"
                f"<td style='padding:6px 8px;border:1px solid #FEE2E2;'>{_esc(k)}</td>"
                f"<td style='padding:6px 8px;border:1px solid #FEE2E2;text-align:right;font-weight:800;color:#B91C1C;'>{int(v)}</td>"
                "</tr>"
                for k, v in vc_cli.items()
            ])
            top_clientes_html = (
                "<div style='font-weight:800;margin:10px 0 6px;font-size:12px;'>Top Clientes com críticos (Regra 3)</div>"
                "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;font-size:11px;'>"
                "<thead><tr style='background:#FEF2F2;'>"
                "<th style='padding:6px 8px;border-bottom:1px solid #FECACA;text-align:left;'>Cliente</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #FECACA;text-align:right;'>Críticos</th>"
                "</tr></thead>"
                f"<tbody>{rows_cli}</tbody>"
                "</table>"
            )

    ag_col = next((c for c in df_cur.columns if normalize_text(str(c).replace('\r', '').replace('\n', '').strip()) == normalize_text('Agente')), None)
    if ag_col:
        try:
            cur_agent_vc = df_cur.groupby(ag_col, dropna=False).size()
            prev_agent_vc = (
                df_prev_mtd.groupby(ag_col, dropna=False).size()
                if (df_prev_mtd is not None and not df_prev_mtd.empty) else pd.Series(dtype=int)
            )
            top_duv_agent = {}
            if SUP_COL_DUVIDA in df_cur.columns:
                for agent_name, grp in df_cur.groupby(ag_col, dropna=False):
                    dv = grp[SUP_COL_DUVIDA].apply(safe_str)
                    dv = dv[dv != '']
                    top_duv_agent[agent_name] = dv.value_counts().index[0] if not dv.empty else ''

            vol_rows = []
            for agent_name, cur_q in cur_agent_vc.items():
                prev_q = int(prev_agent_vc.get(agent_name, 0)) if not prev_agent_vc.empty else 0
                vol_rows.append({
                    'agent': agent_name,
                    'cur': int(cur_q),
                    'prev': prev_q,
                    'top_duv': safe_str(top_duv_agent.get(agent_name, '')),
                })
            vol_rows.sort(key=lambda r: (r['cur'], r['cur'] - r['prev']), reverse=True)
            vol_rows = vol_rows[:12]
            ag_volume_qtd = len(vol_rows)

            if vol_rows:
                vol_body = ''.join([
                    "<tr>"
                    f"<td style='padding:6px 8px;border:1px solid #e9ecef;white-space:nowrap;'>{_agent_span(r['agent'])}</td>"
                    f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;font-weight:700;'>{r['cur']}</td>"
                    f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;color:#6B7280;'>{r['prev']}</td>"
                    f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:center;'>{fmt_delta_html(r['cur'], r['prev'])}</td>"
                    f"<td style='padding:6px 8px;border:1px solid #e9ecef;font-size:11px;'>{_esc(r['top_duv'][:80])}</td>"
                    "</tr>"
                    for r in vol_rows
                ])
                top_agentes_volume_html = (
                    "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Quem mais aciona suporte (volume)</div>"
                    "<div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>"
                    f"Comparativo MTD: {cur_start.strftime('%d/%m')} → {cur_end.strftime('%d/%m')} vs {comp_txt}. "
                    "Passe o mouse na matrícula para ver o nome."
                    "</div>"
                    "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
                    "style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
                    "<colgroup><col style='width:14%'><col style='width:10%'><col style='width:10%'><col style='width:12%'><col style='width:54%'></colgroup>"
                    "<thead><tr style='background:#EFF6FF;'>"
                    "<th style='padding:6px 8px;border-bottom:1px solid #BFDBFE;text-align:left;'>Matrícula</th>"
                    "<th style='padding:6px 8px;border-bottom:1px solid #BFDBFE;text-align:right;'>Atual</th>"
                    "<th style='padding:6px 8px;border-bottom:1px solid #BFDBFE;text-align:right;'>M-1 eq.</th>"
                    "<th style='padding:6px 8px;border-bottom:1px solid #BFDBFE;text-align:center;'>Δ</th>"
                    "<th style='padding:6px 8px;border-bottom:1px solid #BFDBFE;text-align:left;'>Cenário dominante</th>"
                    "</tr></thead>"
                    f"<tbody>{vol_body}</tbody></table>"
                )
            else:
                top_agentes_volume_html = "<div style='font-size:12px;color:#6c757d;'>Sem solicitações por agente no período.</div>"
        except Exception:
            top_agentes_volume_html = "<div style='font-size:12px;color:#6c757d;'>Não foi possível montar o ranking de volume por agente.</div>"

        df_work = df_cur.copy()
        df_work['_conform_calc'] = conform_calc
        df_work['_r3_flag'] = mask_r3.astype(int)
        grp = df_work.groupby(ag_col, dropna=False)
        tab = grp.agg(
            Solicitacoes=(SUP_COL_PROTO, 'count') if SUP_COL_PROTO in df_cur.columns else (SUP_COL_DATA, 'count'),
            NaoConforme=('_conform_calc', lambda s: int((s.apply(normalize_text) == 'nao conforme').sum())),
            CriticosR3=('_r3_flag', 'sum'),
        ).reset_index()
        tab['% Não Conforme'] = tab.apply(lambda r: (r['NaoConforme'] / r['Solicitacoes'] * 100.0) if r['Solicitacoes'] else 0.0, axis=1)
        tab['% Críticos R3'] = tab.apply(lambda r: (r['CriticosR3'] / r['Solicitacoes'] * 100.0) if r['Solicitacoes'] else 0.0, axis=1)
        tab['Top Dúvida'] = grp[SUP_COL_DUVIDA].apply(_top1).reset_index(drop=True)
        tab['Top Conclusão'] = grp[SUP_COL_CONCL].apply(_top1).reset_index(drop=True)

        if not df_r3.empty:
            grp_r3 = df_work[df_work['_r3_flag'] == 1].groupby(ag_col, dropna=False)
            map_duv_r3 = grp_r3[SUP_COL_DUVIDA].apply(_top1).to_dict() if SUP_COL_DUVIDA in df_work.columns else {}
            map_conc_r3 = grp_r3[SUP_COL_CONCL].apply(_top1).to_dict() if SUP_COL_CONCL in df_work.columns else {}
        else:
            map_duv_r3 = {}
            map_conc_r3 = {}

        tab['Top Dúvida (R3)'] = tab[ag_col].map(lambda x: safe_str(map_duv_r3.get(x, '')))
        tab['Top Conclusão (R3)'] = tab[ag_col].map(lambda x: safe_str(map_conc_r3.get(x, '')))

        tab = tab[tab['CriticosR3'] > 0].copy()
        tab = tab.sort_values(['CriticosR3', '% Críticos R3', 'NaoConforme', 'Solicitacoes', SUP_COL_AGENTE], ascending=[False, False, False, False, True]).head(20)
        ag_crit_qtd = int(tab.shape[0])

        rows = []
        for _, r in tab.iterrows():
            rows.append(
                "<tr>"
                f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{_agent_span(r.get(ag_col,''))}</td>"
                f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;white-space:nowrap;'>{int(r.get('Solicitacoes',0))}</td>"
                f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;white-space:nowrap;color:#DC3545;font-weight:700;'>{int(r.get('NaoConforme',0))}</td>"
                f"<td style='padding:6px 8px;border:1px solid #e9ecef;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{float(r.get('% Não Conforme',0.0)):.1f}%</td>"
                f"<td style='padding:6px 8px;border:1px solid #FEE2E2;text-align:right;white-space:nowrap;color:#B91C1C;font-weight:800;'>{int(r.get('CriticosR3',0))}</td>"
                f"<td style='padding:6px 8px;border:1px solid #FEE2E2;text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;color:#B91C1C;font-weight:700;'>{float(r.get('% Críticos R3',0.0)):.1f}%</td>"
                f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{safe_str(r.get('Top Dúvida (R3)',''))}</td>"
                f"<td style='padding:6px 8px;border:1px solid #e9ecef;'>{safe_str(r.get('Top Conclusão (R3)',''))}</td>"
                "</tr>"
            )

        if tab.empty:
            tab_html = "<div style='font-size:12px;color:#6c757d;'>Sem agentes com críticos (Regra 3) no período.</div>"
        else:
            tab_html = (
                "<div style='font-weight:800;margin:10px 0 6px;font-size:12px;'>Top Agentes para ação (apenas críticos R3)</div>"
                "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
                "<colgroup><col style='width:19%'><col style='width:8%'><col style='width:9%'><col style='width:9%'><col style='width:9%'><col style='width:9%'><col style='width:18%'><col style='width:19%'></colgroup>"
                "<thead><tr style='background:#F3F4F6;'>"
                "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:left;'>Matrícula</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:right;'>Qtd</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:right;'>NC</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:right;'>%NC</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #FECACA;text-align:right;color:#B91C1C;'>R3</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #FECACA;text-align:right;color:#B91C1C;'>%R3</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:left;'>Top Dúvida (R3)</th>"
                "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:left;'>Top Conclusão (R3)</th>"
                "</tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table>"
            )

    cli_crit_qtd = int(df_r3[SUP_COL_CLIENTE].apply(safe_str).replace('', np.nan).dropna().nunique()) if (not df_r3.empty and SUP_COL_CLIENTE in df_r3.columns) else 0

    tiles = table_row_cols_blocks([
        _kpi_tile_legacy('Solicitações Suporte (MTD)', str(int(total_atual)), f"Período: {cur_start.strftime('%d/%m/%Y')} → {cur_end.strftime('%d/%m/%Y')}"),
        _kpi_tile_legacy('Suporte mês anterior (equivalente)', str(int(total_prev)), f"Período: {comp_txt}"),
        _kpi_tile_legacy('NC oficial (Regra)', f"{pct_nconf:.1f}%", f"{nconf} NC | {conf} C | Regra 3 incluída"),
    ], gray_container=True)

    # Tabela detalhada de críticos (Regra 3) para ação operacional
    r3_table_html = "<div style='font-size:12px;color:#6c757d;'>Sem registros críticos (Regra 3) no período.</div>"
    if not df_r3.empty:
        r3_rows = []
        for _, row in df_r3.head(80).iterrows():
            r3_rows.append({
                'Cliente': safe_str(row.get(SUP_COL_CLIENTE, '')),
                'Protocolo': safe_str(row.get(SUP_COL_PROTO, '')),
                'Agente': safe_str(row.get(SUP_COL_AGENTE, '')),
                'Dúvida': safe_str(row.get(SUP_COL_DUVIDA, '')),
                'Conclusão': safe_str(row.get(SUP_COL_CONCL, '')),
            })

        r3_body = ''
        for i, r in enumerate(r3_rows):
            zebra = 'background:#FAFAFA;' if (i % 2 == 1) else 'background:#FFFFFF;'
            proto_fmt = norm_protocolo(r['Protocolo'])
            agente_html = _agent_span(r['Agente'])
            r3_body += (
                f"<tr style='{zebra}'>"
                f"<td style='padding:6px 8px;border:1px solid #FFF5F5;font-size:11px;white-space:nowrap;color:#000;'>{proto_fmt}</td>"
                f"<td style='padding:6px 8px;border:1px solid #FFF5F5;font-size:11px;color:#000;'>{_esc(r['Cliente'])}</td>"
                f"<td style='padding:6px 8px;border:1px solid #FFF5F5;font-size:11px;white-space:nowrap;color:#000;'>{agente_html}</td>"
                f"<td style='padding:6px 8px;border:1px solid #FFF5F5;font-size:11px;color:#000;'>{_esc(r['Dúvida'])}</td>"
                f"<td style='padding:6px 8px;border:1px solid #FFF5F5;font-size:11px;color:#000;'>{_esc(r['Conclusão'])}</td>"
                "</tr>"
            )

        r3_table_html = (
            "<div style='background:#FFF5F5;border:2px solid #FFB3B3;border-radius:10px;padding:12px;margin:10px 0;'>"
            "<div style='font-weight:900;margin:0 0 8px;color:#DC3545;font-size:13px;'>⚠️ Casos críticos para ação (Regra 3: Crítico=Sim + Fácil)</div>"
            "<div style='font-size:12px;color:#666;margin:0 0 10px;'>Visão operacional: prioriza casos críticos para atuação imediata, sem redefinir a métrica oficial de NC.</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;font-size:11px;'>"
            "<colgroup><col style='width:12%'><col style='width:18%'><col style='width:12%'><col style='width:28%'><col style='width:30%'></colgroup>"
            "<thead><tr style='background:#C90000;color:#fff;'>"
            "<th style='padding:8px 8px;text-align:left;font-weight:900;font-size:12px;color:#111827;'>Protocolo</th>"
            "<th style='padding:8px 8px;text-align:left;font-weight:900;font-size:12px;color:#111827;'>Cliente</th>"
            "<th style='padding:8px 8px;text-align:left;font-weight:900;font-size:12px;color:#111827;'>Agente</th>"
            "<th style='padding:8px 8px;text-align:left;font-weight:900;font-size:12px;color:#111827;'>Dúvida</th>"
            "<th style='padding:8px 8px;text-align:left;font-weight:900;font-size:12px;color:#111827;'>Conclusão</th>"
            "</tr></thead>"
            f"<tbody>{r3_body}</tbody>"
            "</table>"
            f"<div style='font-size:11px;color:#333;margin-top:8px;'>Mostrando até 80 registros de um total de {r3}.</div>"
            "</div>"
        )

    total_reg = int(len(df_cur))
    top_duvida = _top1(df_cur[SUP_COL_DUVIDA]) if SUP_COL_DUVIDA in df_cur.columns else ''
    top_concl = _top1(df_cur[SUP_COL_CONCL]) if SUP_COL_CONCL in df_cur.columns else ''

    # --- Novas visões solicitadas: Volume, Dúvidas Conformes e Dúvidas em Piora ---
    # Volume de dúvidas acionadas (não-vazias)
    if SUP_COL_DUVIDA in df_cur.columns:
        dv_series = df_cur[SUP_COL_DUVIDA].apply(safe_str)
        dv_nonempty = dv_series[dv_series != '']
        total_duvidas_acionadas = int(dv_nonempty.shape[0])
        top_duvidas_conformes = None
        try:
            # Conformes calculados pela regra já em conform_calc
            mask_conformes = (conform_calc == 'conforme')
            df_conformes = df_cur[mask_conformes]
            if not df_conformes.empty and SUP_COL_DUVIDA in df_conformes.columns:
                top_duvidas_conformes = df_conformes[SUP_COL_DUVIDA].apply(safe_str).value_counts().head(5)
        except Exception:
            top_duvidas_conformes = None
    else:
        total_duvidas_acionadas = 0
        top_duvidas_conformes = None

    # Proporção de conformes (pelo cálculo do painel)
    pct_conforme = (conf / total_reg * 100.0) if total_reg else 0.0

    # Cenários (dúvidas) em piora: MTD equivalente + mês cheio
    diffs_mtd = []
    diffs_mes = []
    try:
        if SUP_COL_DUVIDA in df_cur.columns:
            vc_ref = df_cur[SUP_COL_DUVIDA].apply(safe_str).value_counts()
            vc_prev_mtd = (
                df_prev_mtd[SUP_COL_DUVIDA].apply(safe_str).value_counts()
                if (df_prev_mtd is not None and not df_prev_mtd.empty) else pd.Series(dtype='int')
            )
            for k, v in vc_ref.items():
                prev_v = int(vc_prev_mtd.get(k, 0)) if not vc_prev_mtd.empty else 0
                delta = int(v) - int(prev_v)
                if delta > 0:
                    pct_d = (delta / (prev_v or 1) * 100.0) if prev_v else 100.0
                    diffs_mtd.append((k, int(v), int(prev_v), delta, pct_d))
            diffs_mtd = sorted(diffs_mtd, key=lambda x: x[3], reverse=True)[:8]

            df_prev_mes = filter_by_date_range_on(df_sup_scope, prev_start, month_last_day(prev_start), SUP_COL_DATA)
            vc_prev_mes = (
                df_prev_mes[SUP_COL_DUVIDA].apply(safe_str).value_counts()
                if (df_prev_mes is not None and not df_prev_mes.empty) else pd.Series(dtype='int')
            )
            for k, v in vc_ref.items():
                prev_v = int(vc_prev_mes.get(k, 0)) if not vc_prev_mes.empty else 0
                delta = int(v) - int(prev_v)
                if delta > 0:
                    pct_d = (delta / (prev_v or 1) * 100.0) if prev_v else 100.0
                    diffs_mes.append((k, int(v), int(prev_v), delta, pct_d))
            diffs_mes = sorted(diffs_mes, key=lambda x: x[3], reverse=True)[:8]
    except Exception:
        diffs_mtd = []
        diffs_mes = []
    diffs = diffs_mtd

    # HTML helpers para as novas seções
    def _duvidas_conformes_html(vc: pd.Series) -> str:
        if vc is None or getattr(vc, 'empty', True):
            return "<div style='font-size:12px;color:#6c757d;'>Sem dúvidas conformes no período.</div>"
        items = ''.join([f"<li style='margin:4px 0;'>{safe_str(k)} <span style='color:#6c757d'>({int(v)})</span></li>" for k, v in vc.items()])
        return f"<ul style='margin:6px 0 0;padding-left:18px;font-size:12px;'>{items}</ul>"

    def _cenarios_piora_table(rows: list, comp_label: str) -> str:
        if not rows:
            return f"<div style='font-size:12px;color:#6c757d;'>Nenhum cenário em piora ({comp_label}).</div>"
        body = ''.join([
            "<tr>"
            f"<td style='padding:6px 8px;border:1px solid #FED7AA;font-size:11px;'>{_esc(safe_str(r[0])[:90])}</td>"
            f"<td style='padding:6px 8px;border:1px solid #FED7AA;text-align:right;font-weight:700;'>{r[1]}</td>"
            f"<td style='padding:6px 8px;border:1px solid #FED7AA;text-align:right;color:#6B7280;'>{r[2]}</td>"
            f"<td style='padding:6px 8px;border:1px solid #FED7AA;text-align:center;'>{fmt_delta_html(r[1], r[2])}</td>"
            f"<td style='padding:6px 8px;border:1px solid #FED7AA;text-align:right;color:#B45309;font-weight:700;'>{r[4]:.0f}%</td>"
            "</tr>"
            for r in rows[:8]
        ])
        return (
            f"<div style='font-size:11px;color:#6B7280;margin:0 0 6px;'>{comp_label}</div>"
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
            "style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;'>"
            "<colgroup><col style='width:46%'><col style='width:12%'><col style='width:12%'><col style='width:14%'><col style='width:16%'></colgroup>"
            "<thead><tr style='background:#FFF7ED;'>"
            "<th style='padding:6px 8px;border-bottom:1px solid #FED7AA;text-align:left;'>Cenário (dúvida)</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #FED7AA;text-align:right;'>Atual</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #FED7AA;text-align:right;'>Anterior</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #FED7AA;text-align:center;'>Δ</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #FED7AA;text-align:right;'>Δ%</th>"
            "</tr></thead>"
            f"<tbody>{body}</tbody></table>"
        )

    def _duvidas_piorando_html(rows: list) -> str:
        return _cenarios_piora_table(
            rows,
            f"MTD equivalente: {cur_start.strftime('%d/%m')} → {cur_end.strftime('%d/%m')} vs {comp_txt}",
        )

    # Tiles de insights de dúvidas (sem repetir KPI já existente de volume total)
    duvidas_piora_qtd = int(len(diffs))
    insights_tiles = table_row_cols_blocks([
        _kpi_tile_legacy('Dúvidas acionadas (MTD)', str(int(total_duvidas_acionadas)), 'Registros com texto preenchido em Dúvida'),
        _kpi_tile_legacy('Dúvidas conformes (MTD)', str(int(conf)), f"{pct_conforme:.1f}% do total"),
        _kpi_tile_legacy('Dúvidas em piora', str(duvidas_piora_qtd), 'Itens com REF > M-1'),
    ], gray_container=True)

    quick_sup_html = (
        "<div class='quick'>"
        "<h3>⚡ Resumo Executivo</h3>"
        f"<ul><li>Total no período: <b>{total_reg}</b> • NC oficial: <b>{pct_nconf:.1f}%</b> ({nconf} NC)</li>"
        f"<li>Casos críticos para ação (Regra 3): <b>{r3}</b></li>"
        f"<li>Top Dúvida (geral): <b>{safe_str(top_duvida)}</b> • Top Conclusão (geral): <b>{safe_str(top_concl)}</b></li></ul>"
        f"<div class='insight'>Consistência NC x Regra 3 monitorada no painel de Qualidade/Conformidade.</div>"
        "</div>"
    )

    duvidas_insights_html = (
        "<div class='card' style='background:#fff;'>"
        "<div style='font-weight:900;margin:0 0 6px;'>💬 Insights de Dúvidas (acionadas)</div>"
        "<div style='font-size:12px;color:#4B5563;margin:0 0 10px;'>"
        "Leitura rápida: o bloco mostra volume acionado, dúvidas conformes e quais temas aumentaram no mês de referência (REF) frente ao mês anterior (M-1)."
        "</div>"
        f"{insights_tiles}"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;margin-top:8px;'>"
        "<tr>"
        "<td width='50%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Top Dúvidas (geral)</div>"
        f"{_mini_list(top_duv)}"
        "</td>"
        "<td width='50%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Top Conclusões (geral)</div>"
        f"{_mini_list(top_conc)}"
        "</td>"
        "</tr>"
        "</table>"
        "<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px;'>"
        "<div style='padding:8px;background:#F8FAFC;border-radius:8px;border:1px solid #E5E7EB;'>"
        "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Top Dúvidas Conformes</div>"
        "<div style='font-size:11px;color:#6B7280;margin-bottom:6px;'>Casos classificados como CONFORME pela regra oficial.</div>"
        f"{_duvidas_conformes_html(top_duvidas_conformes)}"
        "</div>"
        "<div style='padding:8px;background:#FFF7ED;border-radius:8px;border:1px solid #FED7AA;'>"
        "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Cenários em piora (dúvidas)</div>"
        "<div style='font-size:11px;color:#6B7280;margin-bottom:6px;'>Temas que aumentaram no período; priorizar investigação operacional.</div>"
        f"{_duvidas_piorando_html(diffs_mtd)}"
        f"<div style='margin-top:10px;'>{_cenarios_piora_table(diffs_mes, 'Mês cheio: atual vs ' + prev_start.strftime('%m/%Y') + ' inteiro')}</div>"
        "</div>"
        "</div>"
        "</div>"
    )

    volume_ranking_html = (
        "<div class='card' style='background:#fff;'>"
        "<div style='font-weight:900;margin:0 0 6px;'>👥 Quem mais aciona o suporte</div>"
        "<div style='font-size:12px;color:#4B5563;margin:0 0 10px;'>"
        "Ranking por volume de solicitações no período, com comparativo MTD e cenário (dúvida) mais frequente de cada pessoa."
        "</div>"
        f"{top_agentes_volume_html}"
        "</div>"
    )

    guide_html = (
        "<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:10px 12px;margin:10px 0;'>"
        "<div style='font-weight:900;margin:0 0 6px;font-size:12px;'>📘 Como interpretar este painel</div>"
        "<ul style='margin:6px 0 6px;padding-left:18px;font-size:12px;color:#334155;'>"
        "<li>NC oficial: representa todos os casos classificados como NÃO CONFORME pela regra oficial do suporte.</li>"
        "<li>Regra 3: representa apenas os casos com Crítico = Sim e Dificuldade = Fácil.</li>"
        "<li>Regra 3 compõe o NC oficial, mas não representa todo o NC.</li>"
        "<li>Os casos de Regra 3 aparecem em destaque porque são prioridade operacional imediata.</li>"
        "</ul>"
        "<div style='font-size:12px;color:#0F172A;font-weight:800;'>NC oficial ≠ Regra 3</div>"
        "<div style='font-size:12px;color:#0F172A;font-weight:800;'>Regra 3 é um subconjunto do NC oficial.</div>"
        "</div>"
    )

    conformity_rules_html = (
        "<details class='collapsible'><summary>📋 Como a Conformidade é calculada?<span class='hint'>(clique para abrir)</span></summary>"
        "<div class='inner'>"
        "<div style='background:#F0F7FF;border:1px solid #B3D9FF;border-radius:10px;padding:12px;margin-top:8px;'>"
        "<div style='font-size:12px;color:#333;line-height:1.5;'>"
        "<p style='margin:0 0 8px;'><b>🟢 Regra 1: Dúvida = Resultado Correto</b><br/>Se a dúvida levantada coincide com o resultado/conclusão obtido → <b>CONFORME</b> (esperado).</p>"
        "<p style='margin:0 0 8px;'><b>🟢 Regra 2: Dificuldade Média/Difícil/Complexo</b><br/>Se a dificuldade foi MÉDIO, DIFÍCIL ou COMPLEXO → <b>CONFORME</b> (justificado, pois é complexo resolver).</p>"
        "<p style='margin:0 0 0;'><b style='color:#DC3545;'>🔴 Regra 3: Crítico = Sim + Dificuldade Fácil</b><br/>Quando ocorre, é tratado como <b>NÃO CONFORME</b> pela regra oficial do suporte e aparece também como oportunidade operacional imediata.</p>"
        "</div>"
        "</div>"
        "</div></details>"
    )

    quality_html = (
        "<div class='card' style='background:#fff;'>"
        "<div style='font-weight:900;margin:0 0 6px;'>✅ Qualidade / Conformidade (visão oficial)</div>"
        "<div style='font-size:12px;color:#4B5563;margin:0 0 8px;'>NC oficial é calculado pela regra do suporte e inclui a Regra 3 (Crítico=Sim + Dificuldade Fácil).</div>"
        "<div style='font-size:12px;color:#4B5563;margin:0 0 8px;'>MTD (Month-to-Date) representa o acumulado do mês corrente até a data de referência do relatório, comparado a um período equivalente do mês anterior.</div>"
        f"{consistency_html}"
        f"{tiles}"
        "</div>"
    )

    action_tiles_html = table_row_cols_blocks([
        _kpi_tile_legacy('Casos críticos (Regra 3)', str(int(r3)), 'Crítico=Sim + Dificuldade Fácil'),
        _kpi_tile_legacy('Agentes com críticos', str(int(ag_crit_qtd)), 'Top 20 priorizados por risco'),
        _kpi_tile_legacy('Clientes com críticos', str(int(cli_crit_qtd)), 'Base para priorização comercial/operacional'),
    ], gray_container=True)

    action_html = (
        "<div class='card' style='background:#fff;'>"
        "<div style='font-weight:900;margin:0 0 6px;'>🚨 Ação operacional (foco em críticos)</div>"
        "<div style='font-size:12px;color:#4B5563;margin:0 0 10px;'>Casos críticos (Regra 3) compõem a conformidade oficial e também são priorizados operacionalmente.</div>"
        f"{action_tiles_html}"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;'>"
        "<tr>"
        "<td width='50%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Top Dúvidas críticas (Regra 3)</div>"
        f"{_mini_list(r3_top_duv)}"
        "</td>"
        "<td width='50%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Top Conclusões críticas (Regra 3)</div>"
        f"{_mini_list(r3_top_conc)}"
        "</td>"
        "</tr>"
        "</table>"
        f"{top_clientes_html}"
        f"{r3_table_html}"
        "</div>"
    )

    # Reorganiza a ordem: resumo rápido + tiles → qualidade/ação → detalhes colapsáveis
    blocos = (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        "<h3 style='margin:0 0 10px;'>📌 Consolidado - Suporte (TEAMS)</h3>"
        f"{periodo_obs_html}"
        # Resumo executivo no topo para leitura rápida
        f"{quick_sup_html}"
        # Qualidade e ação a seguir
        f"{conformity_rules_html}"
        f"{quality_html}"
        f"{volume_ranking_html}"
        f"{duvidas_insights_html}"
        f"{action_html}"
        # Mantém Top Agentes colapsável ao final
        f"<details class='collapsible'><summary>👥 Top Agentes (Suporte) · foco em risco<span class='hint'>(clique para abrir)</span></summary><div class='inner'>" + tab_html + "</div></details>"
        "</div>"
    )

    title = '🛟 Suporte (TEAMS)'
    if safe_str(scope_name):
        title += f" - {safe_str(scope_name)}"
    return wrap_simple_page(title, blocos)

