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
COL_TIPO_DOCUMENTO = "Tipo de documento"
COL_UF_DOCUMENTO = "UF do documento"

from report_falhas.render.pages.common import _kpi_tile_legacy


def _clean_matricula_unified(raw: str) -> str:
    mat, _ = _clean_matricula_pair(raw)
    if mat:
        return mat
    s = safe_str(raw).strip()
    if not s:
        return ''
    mm = re.search(r'([A-Za-z]?\d{3,}[A-Za-z]?)', s)
    return mm.group(1) if mm else s


_clean_matricula_ult3m = _clean_matricula_unified


def calculate_prioridade_capacitacao(total_4m: int, max_ff_scenario_4m: int, recorrente_4m: bool) -> str:
    total_4m = int(total_4m or 0)
    max_ff_scenario_4m = int(max_ff_scenario_4m or 0)
    recorrente_4m = bool(recorrente_4m)
    if total_4m >= 8 or max_ff_scenario_4m >= 3:
        return 'Alta'
    if 4 <= total_4m <= 7 and recorrente_4m:
        return 'Média'
    return 'Baixa'


def _format_prioridade_capacitacao_regra() -> str:
    return (
        "<details class='collapsible'><summary>🧭 Regra de Prioridade de Capacitação<span class='hint'>(clique para ver)</span></summary>"
        "<div class='inner'>"
        "<div style='background:#FFFBEB;border:1px solid #FDE68A;border-radius:10px;padding:12px;margin-top:8px;font-size:12px;color:#374151;line-height:1.45;'>"
        "A prioridade combina <b>recorrência focada</b> e <b>volume acumulado</b>. "
        "<b>Alta</b> ocorre quando há <b>3+ falhas no mesmo cenário crítico de Formatação/Fonte</b> ou <b>8+ falhas no período</b>. "
        "<b>Média</b> cobre volumes entre <b>4 e 7 falhas</b> com recorrência, sem atingir Alta. "
        "<b>Baixa</b> fica para casos pontuais, com baixo volume e baixa recorrência."
        "</div>"
        "</div></details>"
    )


def build_ult3m_training_focus_html(df_tab: pd.DataFrame, titulo: str, df_scope: pd.DataFrame | None = None, cur_start: date | None = None, cur_end: date | None = None) -> str:
    '''Bloco "Radar de capacitação" para a visão de Falhas últimos 3 meses.

    Regras (NÃO altera a janela/recorrência já calculadas na tabela principal):
    - Prioridade Alta: 8+ falhas no período OU 3+ falhas no mesmo cenário crítico de Formatação/Fonte
    - Prioridade Média: 4 a 7 falhas com recorrência, sem atingir Alta
    - Prioridade Baixa: demais
    - Tendência: compara REF vs mês anterior (primeira coluna histórica após REF)
    - Ação sugerida conforme matriz definida.

    Observação: identifica dinamicamente as colunas esperadas na tabela já gerada.
    '''
    from html import escape as _esc
    if df_tab is None or df_tab.empty:
        return "<div style='font-size:12px;color:#6c757d;'>Sem dados para radar de capacitação.</div>"

    # --- Detecção dinâmica de colunas ---
    def _find_ref_col() -> str:
        cols = [c for c in df_tab.columns if str(c).startswith('Falhas REF')]
        return cols[0] if cols else ''

    def _find_hist_cols() -> list:
        # Mantém a ordem natural do df (normalmente: M-1, M-2, M-3)
        cols = [c for c in df_tab.columns if str(c).startswith('Falhas ') and not str(c).startswith('Falhas REF')]
        return cols[:3]

    def _pick_col(prefix: str) -> str:
        for c in df_tab.columns:
            if str(c).startswith(prefix):
                return c
        return ''

    def _num(v) -> int:
        try:
            n = pd.to_numeric(v, errors='coerce')
            return int(n) if pd.notna(n) else 0
        except Exception:
            return 0

    ref_col = _find_ref_col()
    hist_cols = _find_hist_cols()
    prev_col = hist_cols[0] if hist_cols else ''  # mês anterior ao REF
    meses_col = _pick_col('Meses com falha')
    recorr_col = _pick_col('Recorrente')

    # Identificadores (quando existirem)
    nome_col = 'Nome' if 'Nome' in df_tab.columns else ('Nome Agente' if 'Nome Agente' in df_tab.columns else '')
    mat_col = 'Matrícula' if 'Matrícula' in df_tab.columns else ('Matrícula Agente' if 'Matrícula Agente' in df_tab.columns else '')
    turno_col = 'Turno' if 'Turno' in df_tab.columns else ''

    tmp = df_tab.copy()

    # --- Contexto detalhado (para regra de Prioridade por cenario/doc/UF) ---
    df_4m_scope = None
    if df_scope is not None and cur_start is not None and cur_end is not None:
        try:
            _df_ref_sc, df_4m_scope, *_rest, _meta_sc = ult4m_frames(df_scope, cur_start, cur_end)
        except Exception:
            df_4m_scope = None

    # --- Classificações ---
    def _is_sim(v) -> bool:
        return normalize_text(safe_str(v)) == 'sim'

    def _priority_signal_for_row(row) -> tuple[int, int, bool, int]:
        total_4m = _num(row.get('Total 4M (REF+3)', 0))
        if not total_4m:
            total_4m = _num(row.get(meses_col, 0)) + _num(row.get(ref_col, 0))
            if not total_4m and ref_col:
                total_4m = _num(row.get(ref_col, 0)) + sum(_num(row.get(c, 0)) for c in hist_cols)
        recorrente_4m = _is_sim(row.get(recorr_col, '')) if recorr_col else False
        max_ff_scenario_4m = 0

        if df_4m_scope is not None and mat_col:
            try:
                mat_n = norm_matricula(row.get(mat_col, ''))
            except Exception:
                mat_n = ''

            if mat_n:
                try:
                    sub = df_4m_scope[df_4m_scope[COL_MATRICULA].apply(norm_matricula) == mat_n].copy()
                except Exception:
                    sub = None

                if sub is not None and (not getattr(sub, 'empty', True)) and (COL_CENARIO in sub.columns):
                    sub_ff = sub[sub[COL_CENARIO].apply(lambda x: is_formatacao_fonte(safe_str(x)))].copy()
                    if not sub_ff.empty:
                        vc = sub_ff[COL_CENARIO].apply(safe_str).value_counts()
                        if not vc.empty:
                            max_ff_scenario_4m = int(vc.max())

        return total_4m, max_ff_scenario_4m, recorrente_4m, _num(row.get(ref_col, 0)) if ref_col else 0

    def _classify_priority(row) -> str:
        total_4m, max_ff_scenario_4m, recorrente_4m, _ref = _priority_signal_for_row(row)
        return calculate_prioridade_capacitacao(total_4m, max_ff_scenario_4m, recorrente_4m)

    def _classify_trend(row) -> str:
        ref = _num(row.get(ref_col, 0)) if ref_col else 0
        prev = _num(row.get(prev_col, 0)) if prev_col else 0
        if ref > prev:
            return 'Piorando'
        if ref < prev:
            return 'Melhorando'
        return 'Estável'

    def _next_action(prio: str, trend: str) -> str:
        # Ação sugerida definida EXCLUSIVAMENTE pela prioridade (volume + recorrência).
        # A tendência NÃO define prioridade nem altera a ação; é apenas contexto.
        if prio == 'Alta':
            return 'Reciclagem individual imediata'
        if prio == 'Média':
            return 'Refresco temático'
        return 'Monitorar / reforço leve'

    tmp['Prioridade Capacitação'] = tmp.apply(_classify_priority, axis=1)
    tmp['Tendência'] = tmp.apply(_classify_trend, axis=1)
    tmp['Ação sugerida'] = tmp.apply(lambda r: _next_action(safe_str(r['Prioridade Capacitação']), safe_str(r['Tendência'])), axis=1)

    # --- KPIs do radar ---
    alta = int((tmp['Prioridade Capacitação'] == 'Alta').sum())
    media = int((tmp['Prioridade Capacitação'] == 'Média').sum())
    piorando = int((tmp['Tendência'] == 'Piorando').sum())

    novos_ref = 0
    if ref_col:
        if hist_cols:
            hist_sum = pd.Series([0] * len(tmp), index=tmp.index)
            for hc in hist_cols:
                hist_sum = hist_sum + tmp[hc].apply(_num)
            novos_ref = int(((tmp[ref_col].apply(_num) > 0) & (hist_sum == 0)).sum())
        else:
            novos_ref = int((tmp[ref_col].apply(_num) > 0).sum())

    # --- Top 8 para ação (ordenado) ---
    # '_ord_prio' controla a ordem principal: quantidade/recorrência (Alta > Média > Baixa).
    # '_ord_trend' existe apenas como critério secundário de desempate/visual;
    # NÃO deve ser usado para promover/rebaixar prioridades ou alterar ações.
    tmp['_ord_prio'] = tmp['Prioridade Capacitação'].map({'Alta': 2, 'Média': 1, 'Baixa': 0}).fillna(0)
    tmp['_ord_trend'] = tmp['Tendência'].map({'Piorando': 2, 'Estável': 1, 'Melhorando': 0}).fillna(0)

    sort_cols = ['_ord_prio', '_ord_trend'] + ([ref_col] if ref_col else [])
    sort_asc = [False, False] + ([False] if ref_col else [])

    top = tmp.sort_values(sort_cols, ascending=sort_asc).head(8).copy()

    # Monta tabela com cabeçalhos padronizados (mais curtos)
    def _get(row, col):
        return safe_str(row.get(col, ''))

    # Cores (executivo/discreto)
    prio_color_map = {'Alta': '#DC3545', 'Média': '#FD7E14', 'Baixa': '#198754'}
    trend_color_map = {'Piorando': '#DC3545', 'Melhorando': '#198754', 'Estável': '#6B7280'}

    headers = ['Matrícula', 'Turno', 'Falhas REF', 'Meses com falha', 'Recorrente', 'Prioridade Capacitação', 'Tendência', 'Ação sugerida']

    rows_html = []
    for i, (_, r) in enumerate(top.iterrows()):
        zebra = 'background:#F8FAFC;' if (i % 2 == 1) else 'background:#FFFFFF;'
        prio = safe_str(r.get('Prioridade Capacitação', ''))
        trend = safe_str(r.get('Tendência', ''))
        prio_color = prio_color_map.get(prio, '#111827')
        trend_color = trend_color_map.get(trend, '#111827')

        # Valores

        v_mat_raw = _get(r, mat_col) if mat_col else '—'

        v_nome = _get(r, nome_col) if nome_col else ''

        # Matrícula visível + Nome apenas via hover (title)

        if safe_str(v_nome):

            disp_mat = _esc(v_mat_raw) if safe_str(v_mat_raw) else '—'

            v_mat = f'<span title="{_esc(v_nome, quote=True)}" style="cursor:help;">{disp_mat}</span>'

        else:

            v_mat = _esc(v_mat_raw)
        v_turno = _get(r, turno_col) if turno_col else '—'
        v_ref = str(_num(r.get(ref_col, 0))) if ref_col else '0'
        v_meses = str(_num(r.get(meses_col, 0))) if meses_col else '0'
        v_rec = _get(r, recorr_col) if recorr_col else '—'
        v_prio = f"<span style='color:{prio_color};font-weight:800;'>{prio}</span>"
        v_trend = f"<span style='color:{trend_color};font-weight:800;'>{trend}</span>"
        v_acao = _get(r, 'Ação sugerida')

        values = [v_mat, v_turno, v_ref, v_meses, v_rec, v_prio, v_trend, v_acao]
        tds = ''.join([f"<td style='padding:7px 8px;border:1px solid #E5E7EB;vertical-align:top;{zebra}'>{val}</td>" for val in values])
        rows_html.append(f"<tr>{tds}</tr>")

    ths = ''.join([f"<th style='padding:7px 8px;border:1px solid #E5E7EB;background:#F3F4F6;text-align:left;font-weight:800;font-size:12px;'>{safe_str(h)}</th>" for h in headers])

    # KPIs em 2 linhas (evita corte horizontal)
    cards_row1 = table_row_cols_blocks([
        _kpi_tile_legacy('Prioridade Alta', str(alta), 'Reciclagem individual imediata'),
        _kpi_tile_legacy('Prioridade Média', str(media), 'Feedback / refresco'),
    ], gray_container=True)
    cards_row2 = table_row_cols_blocks([
        _kpi_tile_legacy('Piorando no REF', str(piorando), 'REF maior que o mês anterior'),
        _kpi_tile_legacy('Novos no REF', str(novos_ref), 'Sem falha nos 3 meses anteriores'),
    ], gray_container=True)

    # Container do radar (destaque discreto)
    return (
        "<div style='background:#FFFBEB;border:1px solid #FDE68A;border-radius:12px;padding:14px;margin:24px 0 18px;'>"
        + f"<div style='font-weight:900;margin:0 0 6px;'>🎯 Radar de capacitação · {safe_str(titulo)}</div>"
        + "<div style='font-size:12px;color:#6B7280;margin:0 0 10px;line-height:1.45;'>"
        + "A prioridade combina recorrência focada e volume acumulado. <b>Alta</b> exige 3+ falhas no mesmo cenário crítico de Formatação/Fonte ou 8+ falhas no período; <b>Média</b> cobre 4 a 7 falhas com recorrência; <b>Baixa</b> fica para casos pontuais."
        + "<div style='margin-top:6px;font-weight:700;color:#374151;'>Importante: a coluna <i>Tendência</i> indica variação recente apenas, a prioridade e a ação sugerida são definidas exclusivamente por <b>quantidade</b> e <b>recorrência</b>; a tendência não altera a prioridade nem promove/rebaixa ações.</div>"
        + "</div>"
        + _format_prioridade_capacitacao_regra()
        + cards_row1
        + cards_row2
        + "<div style='font-weight:800;margin:24px 0 8px;'>Top 8 para ação</div>"
        + "<div style='max-width:100%;overflow-x:auto;border-radius:10px;margin-top:12px;'><table role='presentation' cellpadding='0' cellspacing='0' style='border-collapse:collapse;width:100%;min-width:980px;font-size:12px;'>"
        + f"<thead><tr>{ths}</tr></thead><tbody>{''.join(rows_html)}</tbody></table>" + '</div>'
        + "</div>"
    )


def format_html_ultimos_3_meses(
    scope_name: str,
    cur_start: date,
    cur_end: date,
    df_total: pd.DataFrame,
    df_oficial: pd.DataFrame,
    nome_map_total: dict | None = None,
    turno_map_total: dict | None = None,
    nome_map_oficial: dict | None = None,
    turno_map_oficial: dict | None = None,
    *,
    dual_metric_mode: bool = True,
) -> str:
    """Gera página HTML dedicada à análise de recorrência para fechamento do mês.

    Janela considerada: mês de referência (REF) + 3 meses anteriores.
    """
    nome_map_total = nome_map_total or {}
    turno_map_total = turno_map_total or {}
    nome_map_oficial = nome_map_oficial or {}
    turno_map_oficial = turno_map_oficial or {}
    tab_ult3m_top_n_agentes = 15
    from html import escape as _esc
    from report_falhas.html_pages import (
        build_cenario_mes_table_html,
        build_cenario_quarter_fy_table_html,
    )

    from report_falhas.matricula_utils import resolve_agent_name

    def _agent_hover(mat: str, nome_map_ctx: dict) -> str:
        m = _clean_matricula_ult3m(mat) or safe_str(mat)
        nome = resolve_agent_name(m, mat, nome_map_ctx)
        disp = _esc(m) if m else '—'
        if nome:
            return (
                f"<span class='hover-detail' title=\"{_esc(nome, quote=True)}\" style='cursor:help;'>"
                f"<span class='hover-trigger'>{disp}</span>"
                f"<span class='hover-panel'><span class='ttl'>Nome do agente</span><div class='sub'>{_esc(nome)}</div></span>"
                f"</span>"
            )
        return disp

    # Tabelas
    df_of_tab, meta_of = build_ult3m_agent_table(df_oficial, cur_start, cur_end, nome_map_oficial, turno_map_oficial)
    df_tot_tab, meta_tot = build_ult3m_agent_table(df_total, cur_start, cur_end, nome_map_total, turno_map_total)

    # Recortes (para diagnóstico)
    df_ref_of, df_4m_of, *_rest_of, meta_of2 = ult4m_frames(df_oficial, cur_start, cur_end)
    df_ref_tot, df_4m_tot, *_rest_tot, meta_tot2 = ult4m_frames(df_total, cur_start, cur_end)

    # KPIs principais (top 1 agente, quantidade de falhas, recorrência) para cada métrica (Oficial vs Total)
    def _kpi_resumo(df_tab: pd.DataFrame, meta: dict) -> dict:
        if df_tab is None or df_tab.empty:
            return {'top': '—', 'qtd': 0, 'rec': 0, 'n': 0, 'ref': meta.get('ref', ''), 'janela_txt': meta.get('janela_txt', '')}
        ref_col = f"Falhas REF ({meta.get('ref','')})"
        top_row = df_tab.iloc[0]
        top_agent = _clean_matricula_ult3m(top_row.get('Matrícula') or '—') or safe_str(top_row.get('Matrícula') or '—')
        top_qtd = int(top_row.get(ref_col, 0)) if ref_col in df_tab.columns else int(top_row.get('Total 4M (REF+3)', 0))
        rec = int((df_tab['Recorrente (4M)'].apply(safe_str).str.lower() == 'sim').sum()) if 'Recorrente (4M)' in df_tab.columns else 0
        return {'top': top_agent, 'qtd': top_qtd, 'rec': rec, 'n': int(len(df_tab)), 'ref': meta.get('ref', ''), 'janela_txt': meta.get('janela_txt', '')}
    
    kpi_of = _kpi_resumo(df_of_tab, meta_of)
    kpi_tot = _kpi_resumo(df_tot_tab, meta_tot)

    # Radar de capacitação (camada visual/analítica em cima da tabela já pronta)
    radar_of = build_ult3m_training_focus_html(df_of_tab, 'Métrica Oficial', df_oficial, cur_start, cur_end)
    radar_tot = build_ult3m_training_focus_html(df_tot_tab, 'Total (Geral)', df_total, cur_start, cur_end)

    # NOVO: Cenários por mês (últimos 3 meses) com variação vs mês anterior (TOP 10)
    scen_mes_of = build_cenario_mes_table_html(df_oficial, cur_start, cur_end, top_n=10, title='🚨 Cenários por mês — Últimos 3 meses (TOP 10)')
    scen_mes_tot = build_cenario_mes_table_html(df_total, cur_start, cur_end, top_n=10, title='🚨 Cenários por mês — Últimos 3 meses (TOP 10)')

    # NOVO: Cenários por Quarter FY (FYTD) · Top 10 FYTD quebrado por quarter (com variação)
    scen_qtr_of = build_cenario_quarter_fy_table_html(df_oficial, cur_end, top_n=10, title='📊 Cenários · Quarter FY (TOP 10 • FYTD)')
    scen_qtr_tot = build_cenario_quarter_fy_table_html(df_total, cur_end, top_n=10, title='📊 Cenários · Quarter FY (TOP 10 • FYTD)')

    # Renderização final da tabela principal (Oficial) com radar de capacitação e cenários
    def _render_table(df_tab: pd.DataFrame, df_scope: pd.DataFrame, nome_map_ctx: dict | None = None) -> str:
        """Tabela (HTML) — padrão final:
        - Coluna visível: Matrícula
        - Nome apenas via hover (title) na Matrícula
        - Nunca exibir coluna visível chamada 'Nome'
        - Tooltips de meses/deltas continuam funcionando (browser)
        """
        nome_map_ctx = nome_map_ctx or {}

        if df_tab is None or df_tab.empty:
            return "<div style='font-size:12px;color:#6c757d;'>Sem dados para este recorte.</div>"

        if 'Nome' in df_tab.columns:
            df_tab = df_tab.drop(columns=['Nome'])

        ref_cols = [c for c in df_tab.columns if str(c).startswith('Falhas REF')]
        ref_col = ref_cols[0] if ref_cols else ''
        hist_cols = [c for c in df_tab.columns if str(c).startswith('Falhas ') and not str(c).startswith('Falhas REF')]

        try:
            inv_mes = {v: k for k, v in MESES_ABREV_BR.items()}

            def _parse_mes_col(col: str):
                s = safe_str(col).lower()
                m = re.search(r'falhas\s+([a-z]{3})/(\d{2})', s)
                if not m:
                    return (9999, 99)
                mm = int(inv_mes.get(m.group(1), 99))
                yy = 2000 + int(m.group(2))
                return (yy, mm)

            hist_cols = sorted(hist_cols, key=_parse_mes_col)
        except Exception:
            pass

        tooltip_cols = set([c for c in ([ref_col] + hist_cols) if c])

        try:
            df_ref_s, _df_4m_s, df_m1_s, df_m2_s, df_m3_s, _meta_s = ult4m_frames(df_scope, cur_start, cur_end)
        except Exception:
            df_ref_s, df_m1_s, df_m2_s, df_m3_s = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

        month_df_by_col = {}
        if ref_col:
            month_df_by_col[ref_col] = df_ref_s
        if len(hist_cols) >= 1:
            month_df_by_col[hist_cols[0]] = df_m1_s
        if len(hist_cols) >= 2:
            month_df_by_col[hist_cols[1]] = df_m2_s
        if len(hist_cols) >= 3:
            month_df_by_col[hist_cols[2]] = df_m3_s

        from html import escape as _esc

        def _cap_month(label: str) -> str:
            s = safe_str(label)
            return (s[:1].upper() + s[1:]) if s else s

        def _display_header(c: str) -> str:
            if ref_col and c == ref_col:
                mm = re.search(r"\(([^)]+)\)", str(c))
                lab = _cap_month(mm.group(1)) if mm else ''
                return (f"REF {lab}".strip() if lab else 'REF')
            m = re.search(r"Falhas\s+([A-Za-z]{3}/\d{2})", str(c))
            return _cap_month(m.group(1)) if m else safe_str(c)

        def _tooltip_top(df_mes: pd.DataFrame, mat_norm: str, top_n: int = 8) -> str:
            if df_mes is None or df_mes.empty or COL_MATRICULA not in df_mes.columns or COL_CENARIO not in df_mes.columns:
                return ''
            try:
                mask = df_mes[COL_MATRICULA].apply(norm_matricula) == mat_norm
                s = df_mes.loc[mask, COL_CENARIO].apply(safe_str)
                s = s[s != '']
                if s.empty:
                    return ''
                vc = s.value_counts().head(int(top_n))
                return ' • ' + ' | '.join([f"{k} ({int(v)})" for k, v in vc.items()])
            except Exception:
                return ''

        def _format_cell_value(val) -> str:
            """Formata valores de célula, aplicando fmt_data_br para datas."""
            if val is None or (hasattr(pd, 'isna') and pd.isna(val)):
                return ''
            try:
                if isinstance(val, (pd.Timestamp, datetime)):
                    return fmt_data_br(val)
            except Exception:
                pass
            return safe_str(val)

        def _short_scen(txt: str) -> str:
            s = safe_str(txt)
            if not s:
                return ''
            parts = re.split(r"\s*[;|•]\s*|\s*,\s*", s)
            return safe_str(parts[0]) if parts else s

        def _render_table_with_cols(
            df_view: pd.DataFrame,
            cols_view: list[str],
            title: str | None = None,
            subtitle: str | None = None,
            short_scen: bool = False,
        ) -> str:
            ths = []
            for c in cols_view:
                ths.append(
                    "<th style='padding:7px 8px;border:1px solid #e5e7eb;background:#f8fafc;font-weight:900;font-size:12px;text-align:left;white-space:normal;overflow-wrap:anywhere;word-break:break-word;line-height:1.15;'>"
                    + _esc(_display_header(c)) + "</th>"
                )
            thead = '<tr>' + ''.join(ths) + '</tr>'

            base_style = 'padding:7px 8px;border:1px solid #e5e7eb;font-size:12px;white-space:normal;overflow-wrap:anywhere;word-break:break-word;vertical-align:top;line-height:1.25;'
            body_rows = []
            for _, row in df_view.iterrows():
                mat_raw = safe_str(row.get('Matrícula', ''))
                mat_raw = _clean_matricula_ult3m(mat_raw) or mat_raw
                mat_n = norm_matricula(mat_raw)
                nome = resolve_agent_name(mat_n, mat_raw, nome_map_ctx, row)
                mat_disp = _esc(mat_raw)
                
                # Renderiza matrícula com hover ou title attribute como fallback
                if nome:
                    # Hover CSS (para navegadores e Outlook NEW)
                    mat_disp = (
                        f"<span class='hover-detail' title='{_esc(nome, quote=True)}'>"
                        f"<span class='hover-trigger'>{mat_disp}</span>"
                        f"<span class='hover-panel'><span class='ttl'>Nome do agente</span><div class='sub'>{_esc(nome)}</div></span>"
                        f"</span>"
                    )
                else:
                    # Se nome não estiver disponível, apenas mostra matrícula
                    mat_disp = f"<span>{mat_disp}</span>"

                tds = []
                for c in cols_view:
                    val = row.get(c, '')
                    if c == 'Matrícula':
                        # Garante que matrícula sempre fica limpa na célula
                        tds.append(f"<td style='{base_style}white-space:nowrap;font-weight:600;'>{mat_disp}</td>")
                        continue
                    if short_scen and safe_str(c).startswith('Cenário principal'):
                        tds.append(f"<td style='{base_style}'>{_esc(_short_scen(val))}</td>")
                        continue
                    if c in tooltip_cols:
                        df_mes = month_df_by_col.get(c)
                        tip = _tooltip_top(df_mes, mat_n, top_n=8) if df_mes is not None else ''
                        if tip:
                            tds.append(f"<td style='{base_style}'><span title=\"{_esc(tip, quote=True)}\" style='cursor:help;'>{_esc(_format_cell_value(val))}</span></td>")
                        else:
                            tds.append(f"<td style='{base_style}'>{_esc(_format_cell_value(val))}</td>")
                        continue
                    tds.append(f"<td style='{base_style}'>{_esc(_format_cell_value(val))}</td>")
                body_rows.append('<tr>' + ''.join(tds) + '</tr>')

            header_html = ""
            if title:
                header_html += f"<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>{_esc(title)}</div>"
            if subtitle:
                header_html += f"<div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>{_esc(subtitle)}</div>"

            return (
                header_html
                + "<div style='overflow-x:auto;border-radius:10px;'>"
                + "<table style='border-collapse:collapse;width:100%;font-size:11px;'>"
                + f"<thead>{thead}</thead><tbody>{''.join(body_rows)}</tbody></table></div>"
            )

        def _find_col_prefix(prefix: str) -> str:
            for c in cols:
                if safe_str(c).startswith(prefix):
                    return c
            return ''

        def _detail_blocks(df_view: pd.DataFrame) -> str:
            if df_view is None or df_view.empty:
                return ""

            scen_ref_c = _find_col_prefix('Top cenários REF')
            scen_4m_c = _find_col_prefix('Top cenários (4M)')
            doc_ref_c = _find_col_prefix('Resumo Doc/UF REF')
            doc_4m_c = _find_col_prefix('Resumo Doc/UF (4M)')

            blocks = []
            for _, row in df_view.iterrows():
                mat_raw = _clean_matricula_ult3m(row.get('Matrícula', '')) or safe_str(row.get('Matrícula', ''))
                if not mat_raw:
                    continue
                mat_n = norm_matricula(mat_raw)
                nome = resolve_agent_name(mat_n, mat_raw, nome_map_ctx, row)
                turno = safe_str(row.get('Turno', '—'))
                recorr = safe_str(row.get('Recorrente (4M)', '—'))
                total4 = _format_cell_value(row.get('Total 4M (REF+3)', ''))
                last_occ = _format_cell_value(row.get('Última ocorrência', '—'))

                mat_disp = _esc(mat_raw)
                if nome:
                    mat_disp = (
                        f"<span class='hover-detail' title=\"{_esc(nome, quote=True)}\" style='cursor:help;'>"
                        f"<span class='hover-trigger'>{mat_disp}</span>"
                        f"<span class='hover-panel'><span class='ttl'>Nome do agente</span><div class='sub'>{_esc(nome)}</div></span>"
                        f"</span>"
                    )

                month_cells = []
                month_cols = [c for c in ([ref_col] + hist_cols) if c]
                for c in month_cols:
                    month_cells.append(
                        "<tr>"
                        f"<td style='padding:6px 8px;border:1px solid #E5E7EB;background:#F8FAFC;font-size:11px;font-weight:700;'>"
                        f"{_esc(_display_header(c))}</td>"
                        f"<td style='padding:6px 8px;border:1px solid #E5E7EB;font-size:11px;'>{_esc(_format_cell_value(row.get(c, '')))}</td>"
                        "</tr>"
                    )

                scen_ref_val = _format_cell_value(row.get(scen_ref_c, '')) if scen_ref_c else ''
                scen_4m_val = _format_cell_value(row.get(scen_4m_c, '')) if scen_4m_c else ''
                doc_ref_val = _format_cell_value(row.get(doc_ref_c, '')) if doc_ref_c else ''
                doc_4m_val = _format_cell_value(row.get(doc_4m_c, '')) if doc_4m_c else ''

                blocks.append(
                    "<details style='border:1px solid #E5E7EB;border-radius:10px;padding:8px 10px;margin:8px 0;background:#fff;'>"
                    "<summary style='cursor:pointer;font-size:12px;font-weight:800;color:#111827;'>"
                    f"{mat_disp} • Turno: { _esc(turno) } • Recorrente: { _esc(recorr) } • Total 4M: { _esc(safe_str(total4)) }"
                    f" • Última ocorrência: { _esc(safe_str(last_occ)) }"
                    "</summary>"
                    "<div style='margin-top:8px;'>"
                    "<div style='font-weight:800;font-size:11px;margin:6px 0 4px;'>Meses anteriores</div>"
                    "<table role='presentation' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;font-size:11px;'>"
                    f"{''.join(month_cells)}"
                    "</table>"
                    "<div style='font-weight:800;font-size:11px;margin:10px 0 4px;'>Cenários (REF e 4M)</div>"
                    "<div style='font-size:11px;color:#374151;'>"
                    f"<div><b>REF</b>: { _esc(safe_str(scen_ref_val)) or '—' }</div>"
                    f"<div><b>4M</b>: { _esc(safe_str(scen_4m_val)) or '—' }</div>"
                    "</div>"
                    "<div style='font-weight:800;font-size:11px;margin:10px 0 4px;'>Resumo Doc/UF (REF e 4M)</div>"
                    "<div style='font-size:11px;color:#374151;'>"
                    f"<div><b>REF</b>: { _esc(safe_str(doc_ref_val)) or '—' }</div>"
                    f"<div><b>4M</b>: { _esc(safe_str(doc_4m_val)) or '—' }</div>"
                    "</div>"
                    "</div>"
                    "</details>"
                )

            if not blocks:
                return ""

            return (
                "<div style='margin-top:10px;'>"
                "<div style='font-weight:800;margin:0 0 6px;font-size:12px;'>Detalhamento por agente (expandir)</div>"
                "<div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>Clique para ver meses anteriores, cenários e Doc/UF.</div>"
                + "".join(blocks)
                + "</div>"
            )

        cols = list(df_tab.columns)
        scen_ref_col = ''
        for c in cols:
            if safe_str(c).startswith('Top cenários REF'):
                scen_ref_col = c
                break

        summary_cols = [
            'Matrícula',
            'Turno',
            ref_col,
            'Total 4M (REF+3)',
            'Meses com falha (4M)',
            'Recorrente (4M)',
            'Última ocorrência',
        ]
        summary_cols = [c for c in summary_cols if c and c in cols]

        df_summary = df_tab.copy()
        if scen_ref_col:
            df_summary['Cenário principal REF'] = df_summary[scen_ref_col]
            summary_cols.append('Cenário principal REF')

        detail_cols = []
        for c in cols:
            if c not in summary_cols:
                detail_cols.append(c)

        detail_cols = [c for c in detail_cols if c in cols]
        for key_col in ['Matrícula', 'Turno']:
            if key_col in cols and key_col not in detail_cols:
                detail_cols.insert(0, key_col)

        summary_html = _render_table_with_cols(
            df_summary,
            summary_cols,
            title='Resumo do Top (legibilidade rápida)',
            subtitle='Campos essenciais para comparar agentes no REF e na janela 4M.',
            short_scen=True,
        )

        return summary_html

    def _janela_txt(kpi):
        j = safe_str(kpi.get('janela_txt', ''))
        return f"Janela: {j} (inclui REF)" if j else '—'

    def _exec_summary(df_tab: pd.DataFrame, df_ref: pd.DataFrame, df_4m: pd.DataFrame, meta: dict, top_n_label: str) -> str:
        if df_tab is None or df_tab.empty:
            return "<div style='font-size:12px;color:#6c757d;'>Sem dados para leitura executiva.</div>"

        ref = safe_str(meta.get('ref', ''))
        ref_col = [c for c in df_tab.columns if str(c).startswith('Falhas REF')]
        ref_col = ref_col[0] if ref_col else ''
        hist_cols = [c for c in df_tab.columns if str(c).startswith('Falhas ') and not str(c).startswith('Falhas REF')]
        prev_col = hist_cols[0] if hist_cols else ''

        def _num(v) -> int:
            try:
                n = pd.to_numeric(v, errors='coerce')
                return int(n) if pd.notna(n) else 0
            except Exception:
                return 0

        rec_count = int((df_tab['Recorrente (4M)'].apply(safe_str).str.lower() == 'sim').sum()) if 'Recorrente (4M)' in df_tab.columns else 0

        piora = 0
        novos = 0
        if ref_col:
            ref_vals = df_tab[ref_col].apply(_num)
            if prev_col and prev_col in df_tab.columns:
                prev_vals = df_tab[prev_col].apply(_num)
                piora = int((ref_vals > prev_vals).sum())
            if hist_cols:
                hist_sum = pd.Series([0] * len(df_tab), index=df_tab.index)
                for hc in hist_cols:
                    hist_sum = hist_sum + df_tab[hc].apply(_num)
                novos = int(((ref_vals > 0) & (hist_sum == 0)).sum())

        def _top_scen(df: pd.DataFrame) -> str:
            if df is None or df.empty or COL_CENARIO not in df.columns:
                return ''
            s = df[COL_CENARIO].apply(safe_str)
            s = s[s != '']
            if s.empty:
                return ''
            return str(s.value_counts().index[0])

        scen_ref = _top_scen(df_ref)
        scen_4m = _top_scen(df_4m)

        return (
            "<div style='background:#F8FAFC;border:1px solid #E2E8F0;border-radius:10px;padding:10px 12px;margin:10px 0;'>"
            "<div style='font-weight:900;margin:0 0 6px;font-size:12px;'>⚡ Leitura Executiva</div>"
            "<ul style='margin:6px 0 0;padding-left:18px;font-size:12px;color:#334155;'>"
            f"<li>Cenário principal: REF ({_esc(ref)}) <b>{_esc(safe_str(scen_ref))}</b> | Janela 4M <b>{_esc(safe_str(scen_4m))}</b></li>"
            f"<li>Recorrentes (4M) {top_n_label}: <b>{rec_count}</b></li>"
            f"<li>Sinal de piora no REF (vs M-1): <b>{piora}</b> agentes</li>"
            f"<li>Novos no REF (sem falha nos 3 anteriores): <b>{novos}</b> agentes</li>"
            "</ul></div>"
        )

    def _capacitacao_focus_html(
        df_ref: pd.DataFrame,
        df_4m: pd.DataFrame,
        nome_map_ctx: dict,
        turno_map_ctx: dict,
        title: str,
        meta: dict,
    ) -> str:
        nome_map_ctx = nome_map_ctx or {}
        turno_map_ctx = turno_map_ctx or {}
        focus_raw = "NAO SINALIZADO - FORMATACAO/FONTE E DESALINHAMENTO ADULTERADA"
        focus_norm = normalize_text(focus_raw)

        if df_ref is None or df_ref.empty or COL_CENARIO not in df_ref.columns:
            return "<div style='font-size:12px;color:#6c757d;'>Sem dados para a visao de capacitacao.</div>"

        dref = df_ref.copy()
        dref['_sc'] = dref[COL_CENARIO].apply(normalize_text)
        dref = dref[dref['_sc'] == focus_norm]
        if dref.empty:
            return "<div style='font-size:12px;color:#6c757d;'>Sem ocorrencias do cenario alvo no REF.</div>"

        d4m = df_4m.copy() if df_4m is not None else pd.DataFrame()
        if not d4m.empty and COL_CENARIO in d4m.columns:
            d4m = d4m.copy()
            d4m['_sc'] = d4m[COL_CENARIO].apply(normalize_text)

        def _top_str(series: pd.Series, n: int = 3) -> str:
            s = series.apply(safe_str)
            s = s[s != '']
            if s.empty:
                return ''
            vc = s.value_counts().head(int(n))
            return ' | '.join([f"{k} ({int(v)})" for k, v in vc.items()])

        def _combo_counts(df_rows: pd.DataFrame) -> tuple[list[tuple[str, int]], int]:
            if df_rows is None or df_rows.empty:
                return [], 0
            if COL_TIPO_DOCUMENTO not in df_rows.columns or COL_UF_DOCUMENTO not in df_rows.columns:
                return [], int(len(df_rows))
            tmp = df_rows[[COL_TIPO_DOCUMENTO, COL_UF_DOCUMENTO]].copy()
            tmp['_doc'] = tmp[COL_TIPO_DOCUMENTO].apply(safe_str)
            tmp['_uf'] = tmp[COL_UF_DOCUMENTO].apply(safe_str)
            tmp['_doc'] = tmp['_doc'].replace('', '—')
            tmp['_uf'] = tmp['_uf'].replace('', '—')
            tmp['_combo'] = tmp['_doc'] + ' / ' + tmp['_uf']
            vc = tmp['_combo'].value_counts()
            pairs = [(k, int(v)) for k, v in vc.items()]
            return pairs, int(vc.sum())

        def _tip_wrap(label: str, lines: list[str]) -> str:
            if not lines:
                return label
            inner = ''.join([f"<div>{_esc(l)}</div>" for l in lines])
            return (
                "<span class='tip'>"
                + label
                + "<span class='tipbox'>"
                + inner
                + "</span></span>"
            )

        month_labels = []
        ref_label = ''
        if meta:
            prev_labels = meta.get('prev') or []
            ref_label = meta.get('ref') or ''
            month_labels = prev_labels + ([ref_label] if ref_label else [])

        def _build_tip_lines(mat_n: str, month_counts: dict[str, int]) -> list[str]:
            if d4m.empty:
                return []

            lines = []

            # 1) Outros cenários além do foco (resumo curto)
            other_scen = d4m[(d4m[COL_MATRICULA].apply(norm_matricula) == mat_n) & (d4m['_sc'] != focus_norm)]
            if (not other_scen.empty) and (COL_CENARIO in other_scen.columns):
                vc = other_scen[COL_CENARIO].apply(safe_str)
                vc = vc[vc != '']
                if not vc.empty:
                    tops = vc.value_counts().head(2)
                    txt = ', '.join([f"{k} ({int(v)})" for k, v in tops.items()])
                    lines.append(f"Outros cenários: {txt}")

            # 2) Variação de Doc/UF no histórico do cenário foco
            focus_docs = d4m[(d4m[COL_MATRICULA].apply(norm_matricula) == mat_n) & (d4m['_sc'] == focus_norm)]
            if (not focus_docs.empty) and (COL_TIPO_DOCUMENTO in focus_docs.columns) and (COL_UF_DOCUMENTO in focus_docs.columns):
                tmp = focus_docs[[COL_TIPO_DOCUMENTO, COL_UF_DOCUMENTO]].copy()
                tmp['_doc'] = tmp[COL_TIPO_DOCUMENTO].apply(safe_str).replace('', '—')
                tmp['_uf'] = tmp[COL_UF_DOCUMENTO].apply(safe_str).replace('', '—')
                grp_doc = tmp.groupby('_doc')['_uf'].nunique().sort_values(ascending=False)
                docs_var = grp_doc[grp_doc > 1]
                if not docs_var.empty:
                    d = docs_var.index[0]
                    n = int(docs_var.iloc[0])
                    lines.append(f"Variação Doc/UF: {d} em {n} UFs")
                else:
                    n_combo = int((tmp['_doc'] + ' / ' + tmp['_uf']).nunique())
                    if n_combo > 1:
                        lines.append(f"Variação Doc/UF: {n_combo} combinações no período")

            # 3) Padrão relevante de recorrência no período
            vals = [int(v) for v in (month_counts or {}).values()]
            meses_ativos = sum(1 for v in vals if v > 0)
            total_4m = sum(vals)
            if meses_ativos >= 2:
                lines.append(f"Padrão: recorrente em {meses_ativos}/{max(1, len(vals))} meses")
            elif total_4m >= 2:
                lines.append("Padrão: concentrado em um único mês")

            return lines[:3]

        rows = []
        for mat, sub in dref.groupby(COL_MATRICULA):
            mat_s = safe_str(mat)
            if not mat_s:
                continue
            mat_n = norm_matricula(mat_s)
            turno = safe_str(turno_map_ctx.get(mat_n, '—'))
            _nome = safe_str(nome_map_ctx.get(mat_n, ''))

            combos, combo_total = _combo_counts(sub)
            combo_parts = [f"{c} ({n})" for c, n in combos]
            combo_cell = ''
            if combo_parts:
                show_parts = combo_parts[:3]
                if len(combo_parts) > 3:
                    show_parts.append(f"+{len(combo_parts) - 3} combinacoes")
                combo_cell = ', '.join(show_parts)
            else:
                combo_cell = '—'

            month_counts = {lab: 0 for lab in month_labels}
            last_txt = '—'
            if not d4m.empty and COL_DATA in d4m.columns:
                try:
                    sub_last = d4m[(d4m[COL_MATRICULA].apply(norm_matricula) == mat_n) & (d4m['_sc'] == focus_norm)]
                    if not sub_last.empty:
                        last_dt = sub_last[COL_DATA].max()
                        last_txt = pd.Timestamp(last_dt).strftime('%d/%m/%Y') if not pd.isna(last_dt) else '—'
                        if month_labels:
                            sub_last = sub_last.dropna(subset=[COL_DATA]).copy()
                            sub_last['_mes'] = sub_last[COL_DATA].apply(lambda x: fmt_mes_ano_br(first_day(pd.Timestamp(x).date())))
                            vc_mes = sub_last['_mes'].value_counts()
                            for lab in month_labels:
                                if lab in vc_mes:
                                    month_counts[lab] = int(vc_mes[lab])
                except Exception:
                    last_txt = '—'

            tip_parts = _build_tip_lines(mat_n, month_counts)

            mat_disp = _tip_wrap(_esc(mat_s), tip_parts)

            rows.append({
                'Matrícula': mat_disp,
                'Turno': turno,
                'Combos': combo_cell,
                'Última ocorrência': last_txt,
                'Meses': month_counts,
                'Combo total': combo_total,
                'Tip lines': tip_parts,
            })

        if not rows:
            return "<div style='font-size:12px;color:#6c757d;'>Sem agentes com ocorrencias no REF.</div>"

        if ref_label:
            rows = sorted(rows, key=lambda x: int((x.get('Meses') or {}).get(ref_label, 0)), reverse=True)
        else:
            rows = sorted(rows, key=lambda x: int(x.get('Combo total', '0')), reverse=True)
        month_ths = ''.join([
            f"<th style='padding:7px 6px;border:1px solid #E5E7EB;background:#F8FAFC;text-align:center;font-weight:800;font-size:11px;'>{_esc(lab)}</th>"
            for lab in month_labels
        ])

        ths = "".join([
            "<th style='padding:7px 8px;border:1px solid #E5E7EB;background:#F8FAFC;text-align:left;font-weight:800;font-size:12px;'>Matrícula</th>",
            "<th style='padding:7px 8px;border:1px solid #E5E7EB;background:#F8FAFC;text-align:left;font-weight:800;font-size:12px;'>Turno</th>",
            month_ths,
            "<th style='padding:7px 8px;border:1px solid #E5E7EB;background:#F8FAFC;text-align:left;font-weight:800;font-size:12px;white-space:nowrap;'>Combinações Doc/UF do cenário</th>",
            "<th style='padding:7px 8px;border:1px solid #E5E7EB;background:#F8FAFC;text-align:center;font-weight:800;font-size:12px;'>Última ocorrência</th>",
        ])

        body_rows = []
        for i, r in enumerate(rows):
            bg = '#FFFFFF' if i % 2 == 0 else '#FAFAFA'
            month_cells = []
            month_vals = r.get('Meses') or {}
            for lab in month_labels:
                val = int(month_vals.get(lab, 0))
                month_cells.append(
                    f"<td style='padding:7px 6px;border:1px solid #E5E7EB;background:{bg};font-size:11px;text-align:center;font-weight:800;'>{val}</td>"
                )

            combo_text = safe_str(r.get('Combos', '—'))
            combo_tip = r.get('Tip lines') or []
            combo_html = _tip_wrap(_esc(combo_text), combo_tip) if combo_text else '—'

            body_rows.append(
                "<tr>"
                f"<td style='padding:7px 8px;border:1px solid #E5E7EB;background:{bg};font-size:12px;'>{r['Matrícula']}</td>"
                f"<td style='padding:7px 8px;border:1px solid #E5E7EB;background:{bg};font-size:12px;'>{_esc(r['Turno'])}</td>"
                + "".join(month_cells) +
                f"<td style='padding:7px 8px;border:1px solid #E5E7EB;background:{bg};font-size:12px;white-space:nowrap;'>{combo_html}</td>"
                f"<td style='padding:7px 8px;border:1px solid #E5E7EB;background:{bg};font-size:12px;text-align:center;'>{_esc(r['Última ocorrência'])}</td>"
                "</tr>"
            )

        return (
            "<div style='border:1px solid #E5E7EB;border-radius:12px;padding:12px;background:#fff;'>"
            f"<div style='font-weight:900;margin:0 0 6px;font-size:12px;'>🧑‍🏫 {safe_str(title)}</div>"
            "<div style='font-size:11px;color:#6B7280;margin:0 0 10px;'>Foco no cenario de formatacao/fonte com historico mensal e combinacoes Doc/UF. Tooltip traz contexto.</div>"
            "<table role='presentation' cellspacing='0' cellpadding='0' style='width:100%;border-collapse:collapse;'>"
            f"<thead><tr>{ths}</tr></thead><tbody>{''.join(body_rows)}</tbody>"
            "</table></div>"
        )

    diag_of = _diagnostico_bloco_html(df_ref_of, df_4m_of, meta_of2)
    diag_tot = _diagnostico_bloco_html(df_ref_tot, df_4m_tot, meta_tot2)

    section_oficial_heading = (
        "<h3 style='margin:8px 0 6px;font-size:13px;'>📌 Métrica Oficial</h3>"
        if dual_metric_mode else ""
    )
    section_oficial = f"""
                <section data-metric='oficial' data-ult3m-panel style='display:block;max-width:100%;overflow:visible;'>
          {section_oficial_heading}
                    <div style='max-width:100%;overflow:visible;'>
                    {table_row_cols_blocks([
              _kpi_tile_legacy('Top agente (REF)', _agent_hover(safe_str(kpi_of.get('top','—')), nome_map_oficial), f"Qtd no mês REF: {int(kpi_of.get('qtd',0))}"),
              _kpi_tile_legacy('Agentes no Top', f"{int(kpi_of.get('n',0))}/{int(tab_ult3m_top_n_agentes)}", 'Exibidos / Configurado'),
              _kpi_tile_legacy('Recorrentes (4M) no Top', str(int(kpi_of.get('rec',0))), _janela_txt(kpi_of)),
          ], gray_container=True)}
                    </div>
          {_exec_summary(df_of_tab, df_ref_of, df_4m_of, meta_of2, f"no Top {tab_ult3m_top_n_agentes}")}
          {_capacitacao_focus_html(df_ref_of, df_4m_of, nome_map_oficial, turno_map_oficial, 'Visao de capacitacao · Formatacao/Fonte (REF)', meta_of2)}
                    <details class='cap-analise'>
                        <summary>Analise completa (opcional)</summary>
                        <div style='font-weight:800;margin:10px 0 6px;font-size:12px;'>🎯 Resumo de prioridade e ação imediata</div>
                        <div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>Radar de capacitação = tamanho do problema + priorização operacional (Top 8 para ação).</div>
                        {radar_of}
                        {scen_mes_of}
                        {scen_qtr_of}
                        <div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>Detalhes sob demanda.</div>
                        {_render_table(df_of_tab, df_oficial, nome_map_oficial)}
                        <div style='font-weight:800;margin:12px 0 6px;font-size:12px;'>🧠 Diagnóstico dos dados</div>
                        {diag_of}
                    </details>
        </section>"""

    section_total = f"""
                <section data-metric='total' data-ult3m-panel style='display:none;max-width:100%;overflow:visible;'>
          <h3 style='margin:8px 0 6px;font-size:13px;'>📌 Total (Geral)</h3>
                    <div style='max-width:100%;overflow:visible;'>
                    {table_row_cols_blocks([
              _kpi_tile_legacy('Top agente (REF)', _agent_hover(safe_str(kpi_tot.get('top','—')), nome_map_total), f"Qtd no mês REF: {int(kpi_tot.get('qtd',0))}"),
              _kpi_tile_legacy('Agentes no Top', f"{int(kpi_tot.get('n',0))}/{int(tab_ult3m_top_n_agentes)}", 'Exibidos / Configurado'),
              _kpi_tile_legacy('Recorrentes (4M) no Top', str(int(kpi_tot.get('rec',0))), _janela_txt(kpi_tot)),
          ], gray_container=True)}
                    </div>
          {_exec_summary(df_tot_tab, df_ref_tot, df_4m_tot, meta_tot2, f"no Top {tab_ult3m_top_n_agentes}")}
          {_capacitacao_focus_html(df_ref_tot, df_4m_tot, nome_map_total, turno_map_total, 'Visao de capacitacao · Formatacao/Fonte (REF)', meta_tot2)}
                    <details class='cap-analise'>
                        <summary>Analise completa (opcional)</summary>
                        <div style='font-weight:800;margin:10px 0 6px;font-size:12px;'>🎯 Resumo de prioridade e ação imediata</div>
                        <div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>Radar de capacitação = tamanho do problema + priorização operacional (Top 8 para ação).</div>
                        {radar_tot}
                                                <div style='font-weight:800;margin:12px 0 6px;font-size:12px;'>📊 Cenários por mês — Últimos 3 meses (TOP 10)</div>
                                                <div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>
                                                    <span class='tip'>ⓘ M-2, M-1 e REF
                                                        <span class='tipbox'>Comparativo dos últimos 3 meses (M-2, M-1 e mês de referência até o último dia com falha).</span>
                                                    </span>
                                                </div>
                        {scen_mes_tot}
                        {scen_qtr_tot}
                        <div style='font-size:11px;color:#6B7280;margin:0 0 8px;'>Detalhes sob demanda.</div>
                        {_render_table(df_tot_tab, df_total, nome_map_total)}
                        <div style='font-weight:800;margin:12px 0 6px;font-size:12px;'>🧠 Diagnóstico dos dados</div>
                        {diag_tot}
                    </details>
        </section>"""

    metric_buttons_html = (
        """
        <div style='display:flex;gap:8px;margin:10px 0;'>
          <button onclick="showMetricPanelUlt3m('oficial')" data-metric='oficial' style='border:2px solid #174E97;background:#174E97;color:#fff;border-radius:10px;padding:6px 12px;font-size:11px;font-weight:700;cursor:pointer;'>✅ Oficial</button>
          <button onclick="showMetricPanelUlt3m('total')" data-metric='total' style='border:2px solid #e5e7eb;background:#fff;color:#111827;border-radius:10px;padding:6px 12px;font-size:11px;font-weight:700;cursor:pointer;'>🟣 Total</button>
        </div>"""
        if dual_metric_mode else ""
    )
    panels_html = section_oficial + (section_total if dual_metric_mode else "")
    metric_script_html = (
        """
  <script>
    function showMetricPanelUlt3m(metric) {{
      var sections = document.querySelectorAll('[data-ult3m-panel]');
      for (var i = 0; i < sections.length; i++) {{
        var sec = sections[i];
        sec.style.display = (sec.getAttribute('data-metric') === metric) ? 'block' : 'none';
      }}
      var buttons = document.querySelectorAll('[data-metric]');
      for (var i = 0; i < buttons.length; i++) {{
        var btn = buttons[i];
        if (btn.tagName === 'BUTTON') {{
          if (btn.getAttribute('data-metric') === metric) {{
            btn.style.background = '#174E97';
            btn.style.color = '#fff';
            btn.style.borderColor = '#174E97';
          }} else {{
            btn.style.background = '#fff';
            btn.style.color = '#111827';
            btn.style.borderColor = '#e5e7eb';
          }}
        }}
      }}
    }}
  </script>"""
        if dual_metric_mode else ""
    )

    html = f"""<!DOCTYPE html>
<html lang='pt-br'>
<head>
  <meta charset='utf-8'>
  <title>Falhas últimos 3 meses - {safe_str(scope_name)}</title>
  <style>
    * {{ box-sizing: border-box; }}
        .tip {{
            position: relative;
            display: inline-block;
            vertical-align: middle;
        }}
        .tip .tipbox {{
            position: absolute;
            left: 0;
            top: calc(100% + 6px);
            min-width: 220px;
            max-width: 360px;
            padding: 8px 10px;
            border-radius: 8px;
            background: #111827;
            color: #F9FAFB;
            font-size: 11px;
            line-height: 1.35;
            box-shadow: 0 8px 20px rgba(0, 0, 0, 0.18);
            white-space: normal;
            z-index: 30;
            visibility: hidden;
            opacity: 0;
            pointer-events: none;
            transition: opacity .12s ease;
        }}
        .tip:hover .tipbox {{
            visibility: visible;
            opacity: 1;
        }}
        /* Hover-detail (matrícula) - garante que o painel fica oculto até hover */
        .hover-detail{{position:relative;display:inline-block;max-width:100%;}}
        .hover-trigger{{display:inline-flex;align-items:center;gap:6px;cursor:help;border-bottom:1px dashed #93C5FD;}}
        .hover-panel{{display:none;position:absolute;left:0;top:calc(100% + 8px);z-index:99999;min-width:260px;max-width:420px;background:#fff;border:1px solid #dbe4f0;border-radius:12px;box-shadow:0 12px 30px rgba(15,23,42,.16);padding:10px;}}
        .hover-detail:hover{{z-index:99999;}}
        .hover-detail:hover .hover-panel{{display:block;}}
        .hover-panel .ttl{{font-size:12px;font-weight:900;color:#0f172a;margin:0 0 4px;}}
        .hover-panel .sub{{font-size:12px;color:#64748b;}}
  </style>
</head>
<body style=\"font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif; color:#212529; background:#fff; line-height:1.4; margin:0; padding:8px; box-sizing:border-box; width:100%; overflow-x: hidden;\">
  <div class='report-shell' style='width:100%; max-width:100%; margin:0; border:1px solid #e9ecef;border-radius:14px;background:#fff;box-sizing:border-box;'>
  <table role='presentation' class='container' width='100%' cellpadding='0' cellspacing='0' style='width:100%;border-collapse:separate;border-spacing:0;'>
    <tr><td style='padding:0;background:#fff;border-top-left-radius:14px;border-top-right-radius:14px;overflow:visible;'>
      <table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='width:100%;background:#174E97;color:#fff;border-collapse:separate;border-spacing:0;'><tr><td style='padding:14px 18px;border-top-left-radius:14px;border-top-right-radius:14px;'>
        <h2 style='margin:0;font-weight:700;'>🧭 Falhas últimos 3 meses</h2>
        <div style='margin-top:4px;font-size:12px;opacity:0.9;'>Escopo: {safe_str(scope_name)} • Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</div>
      </td></tr></table>

      <table role='presentation' width='100%' cellpadding='0' cellspacing='0'><tr><td style='padding:12px 14px;background:#fff;'>
        <div style='background:#F8FAFC;border:1px solid #E5E7EB;border-radius:10px;padding:10px;margin:0 0 12px;'>
          <div style='font-weight:800;margin:0 0 4px;font-size:12px;'>Como ler</div>
          <div style='font-size:11px;color:#334155;line-height:1.4;'>
            <b>Top de agentes</b> é calculado pelo <b>mês de referência (REF)</b> escolhido no input.<br>
            Esta visão considera <b>REF + 3 meses anteriores</b> (janela de 4 meses).<br>
            <b>Recorrente (4M)</b> = teve falha em <b>2 ou mais</b> meses dentro da janela.
                        <br><b>KPIs de recorrência</b> referem-se ao <b>Top exibido</b> na tabela.
          </div>
        </div>

        {metric_buttons_html}

        {panels_html}

      </td></tr></table>
    </td></tr>
  </table>
  </div>

  {metric_script_html}
</body>
</html>"""

    return html


def filter_by_date_range(df: pd.DataFrame, start_d: date, end_d: date) -> pd.DataFrame:
    return _periods_mod.filter_by_date_range(df, start_d, end_d, COL_DATA)


def _topn_join_counts(series: pd.Series, top_n: int = 3) -> str:
    """Retorna string 'Item (n), Item (n)' com Top N de value_counts."""
    if series is None or len(series) == 0:
        return ''
    s = series.apply(safe_str)
    s = s[s != '']
    if s.empty:
        return ''
    vc = s.value_counts().head(int(top_n))
    return ', '.join([f"{k} ({int(v)})" for k, v in vc.items()])


def _resumo_doc_uf(df_agent: pd.DataFrame,
                   col_doc: str,
                   col_uf: str,
                   top_docs: int = 6,
                   top_ufs: int = 3) -> str:
    """Resumo compacto: "RG: 3 (SP, RJ); DNI: 2 (PE, PB)"."""
    if df_agent is None or df_agent.empty:
        return ''
    if col_doc not in df_agent.columns:
        return ''

    d = df_agent.copy()
    d['_doc'] = d[col_doc].apply(safe_str)
    d['_doc'] = d['_doc'].replace({'': None})
    d = d.dropna(subset=['_doc'])
    if d.empty:
        return ''

    if col_uf in d.columns:
        d['_uf'] = d[col_uf].apply(safe_str)
        d['_uf'] = d['_uf'].replace({'': None})
    else:
        d['_uf'] = None

    doc_counts = d['_doc'].value_counts().head(int(top_docs))
    parts = []
    for doc, qtd in doc_counts.items():
        sub = d[d['_doc'] == doc]
        ufs = []
        if sub is not None and not sub.empty:
            if sub['_uf'].notna().any():
                uf_counts = sub['_uf'].dropna().value_counts().head(int(top_ufs))
                ufs = [str(x) for x in uf_counts.index.tolist() if safe_str(x)]
        uf_txt = f" ({', '.join(ufs)})" if ufs else ''
        parts.append(f"{doc}: {int(qtd)}{uf_txt}")

    return '; '.join(parts)


def ult4m_frames(df_scope: pd.DataFrame, cur_start: date, cur_end: date):
    """Gera recortes de data para a janela: REF + 3 meses anteriores.

    REF = mês escolhido no input (cur_start..cur_end)
    Janela 4M = REF, REF-1, REF-2, REF-3

    Retorna: (df_ref, df_4m, df_m1, df_m2, df_m3, meta)
    """
    meta = {
        'ref': fmt_mes_ano_br(first_day(cur_start)),
        'ref_first': first_day(cur_start),
        'ref_last': cur_end,
        'prev': [],
        'prev_firsts': [],
        'janela': [],
        'janela_txt': ''
    }
    if df_scope is None or df_scope.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), meta

    dfx = df_scope.copy()
    if COL_DATA not in dfx.columns:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), meta
    if not pd.api.types.is_datetime64_any_dtype(dfx[COL_DATA]):
        dfx[COL_DATA] = safe_to_datetime(dfx[COL_DATA])
    dfx = dfx.dropna(subset=[COL_DATA])

    ref_first = first_day(cur_start)
    ref_last = cur_end

    m1 = add_months_first_day(ref_first, -1)
    m2 = add_months_first_day(ref_first, -2)
    m3 = add_months_first_day(ref_first, -3)
    prev_firsts = [m3, m2, m1]
    prev_labels = [fmt_mes_ano_br(d) for d in prev_firsts]

    meta['prev'] = prev_labels
    meta['prev_firsts'] = prev_firsts
    meta['janela'] = prev_labels + [meta['ref']]
    meta['janela_txt'] = f"{prev_labels[0]} a {meta['ref']}"

    df_ref = filter_by_date_range(dfx, ref_first, ref_last)
    df_m1 = filter_by_date_range(dfx, m1, month_last_day(m1))
    df_m2 = filter_by_date_range(dfx, m2, month_last_day(m2))
    df_m3 = filter_by_date_range(dfx, m3, month_last_day(m3))
    df_4m = pd.concat([df_m3, df_m2, df_m1, df_ref], ignore_index=True) if (not df_ref.empty or not df_m1.empty or not df_m2.empty or not df_m3.empty) else dfx.iloc[0:0].copy()

    return df_ref, df_4m, df_m1, df_m2, df_m3, meta


def _diagnostico_bloco_html(df_ref: pd.DataFrame, df_4m: pd.DataFrame, meta: dict) -> str:
    """Bloco de diagnóstico (texto) vinculando Cenário ↔ UF ↔ Tipo de documento.

    Gera um resumo humano baseado em Top N da janela 4M.
    """
    try:
        from html import escape as _esc

        if df_4m is None or df_4m.empty:
            return "<div style='font-size:12px;color:#6c757d;'>Sem dados suficientes para diagnóstico.</div>"

        def _vc(df: pd.DataFrame, col: str, n: int = 5):
            if df is None or df.empty or col not in df.columns:
                return pd.Series(dtype=int)
            s = df[col].apply(safe_str)
            s = s[s != '']
            if s.empty:
                return pd.Series(dtype=int)
            return s.value_counts().head(int(n))

        ref = safe_str(meta.get('ref', ''))
        janela_txt = safe_str(meta.get('janela_txt', ''))

        # Top cenários
        top_scen_ref = _vc(df_ref, COL_CENARIO, 1)
        top_scen_4m = _vc(df_4m, COL_CENARIO, 1)
        scen_ref = top_scen_ref.index[0] if len(top_scen_ref) else ''
        scen_ref_n = int(top_scen_ref.iloc[0]) if len(top_scen_ref) else 0
        scen_4m = top_scen_4m.index[0] if len(top_scen_4m) else ''
        scen_4m_n = int(top_scen_4m.iloc[0]) if len(top_scen_4m) else 0

        # Top UF e Doc na janela
        top_uf_4m = _vc(df_4m, COL_UF_DOCUMENTO, 3)
        top_doc_4m = _vc(df_4m, COL_TIPO_DOCUMENTO, 3)

        # Pares Cenário → UF
        pares_txt = ''
        if (COL_CENARIO in df_4m.columns) and (COL_UF_DOCUMENTO in df_4m.columns):
            tmp = df_4m[[COL_CENARIO, COL_UF_DOCUMENTO]].copy()
            tmp['_sc'] = tmp[COL_CENARIO].apply(safe_str)
            tmp['_uf'] = tmp[COL_UF_DOCUMENTO].apply(safe_str)
            tmp = tmp[(tmp['_sc'] != '') & (tmp['_uf'] != '')]
            if not tmp.empty:
                pair = tmp.groupby(['_sc', '_uf']).size().sort_values(ascending=False).head(5)
                pares_txt = '; '.join([f"{_esc(sc)} → {_esc(uf)} ({int(q)})" for (sc, uf), q in pair.items()])

        # Para o cenário líder (prioriza REF), quais UFs e Docs mais comuns
        scen_focus = scen_ref or scen_4m
        uf_focus_txt = ''
        doc_focus_txt = ''
        if scen_focus and (COL_CENARIO in df_4m.columns):
            sub = df_4m[df_4m[COL_CENARIO].apply(safe_str) == safe_str(scen_focus)].copy()
            if not sub.empty:
                uf_focus = _vc(sub, COL_UF_DOCUMENTO, 5)
                doc_focus = _vc(sub, COL_TIPO_DOCUMENTO, 5)
                if len(uf_focus):
                    uf_focus_txt = ', '.join([f"{_esc(k)} ({int(v)})" for k, v in uf_focus.items()])
                if len(doc_focus):
                    doc_focus_txt = ', '.join([f"{_esc(k)} ({int(v)})" for k, v in doc_focus.items()])

        bullets = []
        if scen_ref:
            bullets.append(f"<b>Cenário dominante (REF {_esc(ref)})</b>: {_esc(scen_ref)} ({scen_ref_n}).")
        if scen_4m:
            bullets.append(f"<b>Cenário líder (janela {_esc(janela_txt)})</b>: {_esc(scen_4m)} ({scen_4m_n}).")
        if len(top_uf_4m):
            bullets.append("<b>Concentração por UF</b>: " + ', '.join([f"{_esc(k)} ({int(v)})" for k, v in top_uf_4m.items()]) + ".")
        if len(top_doc_4m):
            bullets.append("<b>Documentos mais impactados</b>: " + ', '.join([f"{_esc(k)} ({int(v)})" for k, v in top_doc_4m.items()]) + ".")
        if pares_txt:
            bullets.append("<b>Combinações críticas (Cenário → UF)</b>: " + pares_txt + ".")
        if scen_focus and (uf_focus_txt or doc_focus_txt):
            foco_parts = []
            if uf_focus_txt:
                foco_parts.append("UFs: " + uf_focus_txt)
            if doc_focus_txt:
                foco_parts.append("Docs: " + doc_focus_txt)
            bullets.append(f"<b>Foco principal ({_esc(scen_focus)})</b>: " + ' | '.join(foco_parts) + ".")

        if not bullets:
            return "<div style='font-size:12px;color:#6c757d;'>Sem diagnóstico gerado (colunas insuficientes).</div>"

        lis = ''.join([f"<li style='margin:6px 0'>{b}</li>" for b in bullets])
        return (
            "<div style='background:#FFF7ED;border:1px solid #FED7AA;border-radius:10px;padding:12px;margin:24px 0 12px;'>"
            "<div style='font-weight:800;margin:0 0 6px;'>📌 Leitura estratégica</div>"
            "<div style='font-size:12px;color:#7C2D12;line-height:1.45;'>"
            "<ul style='padding-left:18px;margin:6px 0 0;'>" + lis + "</ul>"
            "</div></div>"
        )
    except Exception:
        return "<div style='font-size:12px;color:#6c757d;'>Não foi possível gerar diagnóstico automático.</div>"


def build_ult3m_agent_table(
    df_scope: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    nome_map: dict | None = None,
    turno_map: dict | None = None,
    top_n_agents: int = 15,
    top_n_scen: int = 3,
) -> tuple[pd.DataFrame, dict]:
    """Tabela: Top agentes do mês de referência (REF) + janela REF + 3 meses anteriores.

    Mantém o nome da função por compatibilidade.

    - Top agentes: pelo volume do mês REF.
    - Janela: REF + REF-1 + REF-2 + REF-3.
    - Recorrência (4M): agente é recorrente se tem falha em 2+ meses dentro da janela.

    Retorna: (df_result, meta)
      meta = {'ref': 'MM/AAAA', 'prev': ['MM/AAAA',...], 'janela': [...], 'janela_txt': 'MM/AAAA a MM/AAAA', ...}
    """
    nome_map = nome_map or {}
    turno_map = turno_map or {}

    df_ref, df_4m, df_m1, df_m2, df_m3, meta = ult4m_frames(df_scope, cur_start, cur_end)

    if df_ref is None or df_ref.empty or (COL_MATRICULA not in df_ref.columns):
        return pd.DataFrame(), meta

    # Top agentes no REF
    vc_agents = df_ref[COL_MATRICULA].apply(safe_str)
    vc_agents = vc_agents[vc_agents != '']
    top_agents = vc_agents.value_counts().head(int(top_n_agents)).index.tolist()

    prev_labels = meta.get('prev') or []  # [REF-3, REF-2, REF-1]
    lab_m3, lab_m2, lab_m1 = prev_labels[0], prev_labels[1], prev_labels[2]

    def _mat_norm(m):
        return norm_matricula(m)

    rows = []
    for mat in top_agents:
        mat_s = safe_str(mat)
        mat_n = _mat_norm(mat_s)
        nome = safe_str(nome_map.get(mat_n, ''))
        turno = safe_str(turno_map.get(mat_n, ''))

        c_ref = int((df_ref[COL_MATRICULA].apply(_mat_norm) == mat_n).sum())
        c_m1 = int((df_m1[COL_MATRICULA].apply(_mat_norm) == mat_n).sum()) if df_m1 is not None and not df_m1.empty else 0
        c_m2 = int((df_m2[COL_MATRICULA].apply(_mat_norm) == mat_n).sum()) if df_m2 is not None and not df_m2.empty else 0
        c_m3 = int((df_m3[COL_MATRICULA].apply(_mat_norm) == mat_n).sum()) if df_m3 is not None and not df_m3.empty else 0

        total4 = int(c_ref + c_m1 + c_m2 + c_m3)
        meses_com = int(sum([1 if c > 0 else 0 for c in (c_m3, c_m2, c_m1, c_ref)]))
        recorr = 'Sim' if meses_com >= 2 else 'Não'

        # Cenários
        scen_ref = ''
        scen_4m = ''
        if COL_CENARIO in df_ref.columns:
            scen_ref = _topn_join_counts(df_ref.loc[df_ref[COL_MATRICULA].apply(_mat_norm) == mat_n, COL_CENARIO], top_n=top_n_scen)
        if df_4m is not None and not df_4m.empty and COL_CENARIO in df_4m.columns:
            scen_4m = _topn_join_counts(df_4m.loc[df_4m[COL_MATRICULA].apply(_mat_norm) == mat_n, COL_CENARIO], top_n=top_n_scen)

        # Resumo Doc/UF
        resumo_ref = ''
        resumo_4m = ''
        if (df_ref is not None and not df_ref.empty) and ((COL_TIPO_DOCUMENTO in df_ref.columns) or (COL_UF_DOCUMENTO in df_ref.columns)):
            df_ref_ag = df_ref.loc[df_ref[COL_MATRICULA].apply(_mat_norm) == mat_n].copy()
            resumo_ref = _resumo_doc_uf(df_ref_ag, COL_TIPO_DOCUMENTO, COL_UF_DOCUMENTO)
        if (df_4m is not None and not df_4m.empty) and ((COL_TIPO_DOCUMENTO in df_4m.columns) or (COL_UF_DOCUMENTO in df_4m.columns)):
            df_4m_ag = df_4m.loc[df_4m[COL_MATRICULA].apply(_mat_norm) == mat_n].copy()
            resumo_4m = _resumo_doc_uf(df_4m_ag, COL_TIPO_DOCUMENTO, COL_UF_DOCUMENTO)

        # Última ocorrência
        try:
            last_dt = df_4m.loc[df_4m[COL_MATRICULA].apply(_mat_norm) == mat_n, COL_DATA].max() if (df_4m is not None and not df_4m.empty) else pd.NaT
            last_txt = last_dt.strftime('%d/%m/%Y') if not pd.isna(last_dt) else '—'
        except Exception:
            last_txt = '—'

        rows.append({
            'Matrícula': _clean_matricula_ult3m(mat_s) or mat_s,  # ✅ Limpa a matrícula antes de salvar
            'Turno': turno if turno else '—',
            f'Falhas REF ({meta["ref"]})': c_ref,
            f'Falhas {lab_m1}': c_m1,
            f'Falhas {lab_m2}': c_m2,
            f'Falhas {lab_m3}': c_m3,
            'Total 4M (REF+3)': total4,
            'Meses com falha (4M)': meses_com,
            'Recorrente (4M)': recorr,
            f'Top cenários REF ({meta["ref"]})': scen_ref,
            'Top cenários (4M)': scen_4m,
            f'Resumo Doc/UF REF ({meta["ref"]})': resumo_ref,
            'Resumo Doc/UF (4M)': resumo_4m,
            'Última ocorrência': last_txt,
        })

    df_out = pd.DataFrame(rows)
    if not df_out.empty:
        df_out['_ord_rec'] = df_out['Recorrente (4M)'].map({'Sim': 1, 'Não': 0}).fillna(0)
        df_out = df_out.sort_values(
            by=['_ord_rec', f'Falhas REF ({meta["ref"]})', 'Total 4M (REF+3)', 'Matrícula'],
            ascending=[False, False, False, True]
        ).drop(columns=['_ord_rec'])

    return df_out, meta
