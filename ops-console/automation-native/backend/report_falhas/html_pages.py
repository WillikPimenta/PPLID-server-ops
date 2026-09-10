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



from report_falhas.render.pages.suporte import build_suporte_page_html
from report_falhas.render.pages.ult3m import (
    build_ult3m_agent_table, build_ult3m_training_focus_html,
    format_html_ultimos_3_meses, ult4m_frames, _diagnostico_bloco_html,
    _topn_join_counts, _resumo_doc_uf,
)
from report_falhas.render.pages.contestacoes import (
    build_contestacoes_page_html, build_falhas_retiradas_page_html,
    build_falhas_removidas_page_html,
)
from report_falhas.render.pages.common import wrap_simple_page
from report_falhas.render.pages.treinamentos import (
    build_treinamentos_tracking_page_html,
    _build_mats_ativos_hc,
)


def filter_by_date_range(df: pd.DataFrame, start_d: date, end_d: date) -> pd.DataFrame:
    return _periods_mod.filter_by_date_range(df, start_d, end_d, COL_DATA)


def format_html(*args, **kwargs):
    """Bridge de baixo risco para manter compatibilidade do pipeline atual."""
    return format_html_blocks(*args, **kwargs)


def build_cenario_mes_table_html(
    df_scope: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    top_n: int = 10,
    title: str = "🚨 Cenários por mês (TOP 10)",
) -> str:
    """Gera tabela HTML (Outlook-safe) Cenário x Mês para os últimos 3 meses (M-2, M-1, REF).
       Usa o recorte EXATO do mês de referência até o último dia com falha (cur_end).
    """
    if df_scope is None or df_scope.empty or COL_DATA not in df_scope.columns or COL_CENARIO not in df_scope.columns:
        return (
            "<div style='margin-top:8px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#6c757d;'>"
            "Sem dados de cenário para o período.</div>"
        )

    if not pd.api.types.is_datetime64_any_dtype(df_scope[COL_DATA]):
        dfx = df_scope.copy()
        dfx[COL_DATA] = safe_to_datetime(dfx[COL_DATA])
    else:
        dfx = df_scope.copy()

    if dfx.empty:
        return (
            "<div style='margin-top:8px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#6c757d;'>"
            "Sem dados de cenário para o período.</div>"
        )

    # Meses alvo: M-2, M-1 e REF
    ref_month = first_day(cur_start)
    m1 = prev_month_first_day(ref_month)
    m2 = prev_month_first_day(m1)
    month_labels = [fmt_mes_ano_br(m2), fmt_mes_ano_br(m1), fmt_mes_ano_br(ref_month)]

    # REF usa cur_end (último dia com falha), meses anteriores usam mês cheio
    refs = [
        (m2, month_last_day(m2)),
        (m1, month_last_day(m1)),
        (ref_month, cur_end),
    ]

    frames = []
    for start_d, end_d in refs:
        part = filter_by_date_range(dfx, start_d, end_d)
        if not part.empty:
            part = part.copy()
            part["_MES_REF_"] = fmt_mes_ano_br(start_d)
            frames.append(part)

    if not frames:
        return (
            "<div style='margin-top:8px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#6c757d;'>"
            "Sem dados de cenário para o período.</div>"
        )

    d3 = pd.concat(frames, ignore_index=True)

    # Pivot cenário x mês
    pvt = pd.pivot_table(
        d3,
        index=COL_CENARIO,
        columns="_MES_REF_",
        values=COL_DATA,
        aggfunc="count",
        fill_value=0,
    )
    for c in month_labels:
        if c not in pvt.columns:
            pvt[c] = 0
    pvt = pvt[month_labels]
    pvt["Total"] = pvt.sum(axis=1)
    pvt = pvt.sort_values(["Total"] + month_labels[::-1], ascending=False).head(int(top_n))

    # variação REF vs M-1
    prev_col = month_labels[1]
    ref_col = month_labels[2]

    def _delta(cur, prev):
        try:
            cur_i = int(cur)
            prev_i = int(prev)
        except Exception:
            return "—"
        return fmt_delta_html(cur_i, prev_i)

    rows_html = []
    for i, (cenario, row) in enumerate(pvt.iterrows()):
        bg = "#FAFAFA" if i % 2 == 1 else "#FFFFFF"
        rows_html.append(
            "<tr>"
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;font-family:Segoe UI,Arial,sans-serif;'>{safe_str(cenario)}</td>"
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;text-align:center;font-family:Segoe UI,Arial,sans-serif;'>{int(row[month_labels[0]])}</td>"
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;text-align:center;font-family:Segoe UI,Arial,sans-serif;'>{int(row[month_labels[1]])}</td>"
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;text-align:center;font-family:Segoe UI,Arial,sans-serif;'>{int(row[month_labels[2]])}</td>"
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;text-align:center;font-family:Segoe UI,Arial,sans-serif;font-weight:900;'>{int(row['Total'])}</td>"
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;text-align:center;font-family:Segoe UI,Arial,sans-serif;'>{_delta(row[ref_col], row[prev_col])}</td>"
            "</tr>"
        )

    return (
        "<div class='card'>"
        f"<div style='font-weight:900;margin:0 0 8px;font-size:14px;'>" + safe_str(title) + "</div>"
        "<div class='muted' style='font-size:12px;margin:0 0 10px;'>"
        "M-2, M-1 e REF.</div>"
        "<table role='presentation' cellspacing='0' cellpadding='0' style='width:100%;border-collapse:collapse;'>"
        "<thead><tr>"
        "<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:left;font-size:12px;'>Cenário</th>"
        f"<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:center;font-size:12px;'>{month_labels[0]}</th>"
        f"<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:center;font-size:12px;'>{month_labels[1]}</th>"
        f"<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:center;font-size:12px;'>{month_labels[2]}</th>"
        "<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:center;font-size:12px;'>Total</th>"
        "<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:center;font-size:12px;'>Δ REF vs M-1</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table></div>"
    )


def fiscal_q_label_global(dt: pd.Timestamp) -> str:
    """Rótulo do quarter fiscal (FY inicia em abril). Ex.: Q1 FY2026."""
    if pd.isna(dt):
        return ""
    m = int(dt.month)
    fy_year = (dt.year + 1) if m >= 4 else dt.year
    q_num = (((m - 4) % 12) // 3) + 1
    return f"Q{q_num} FY{fy_year}"


def build_cenario_quarter_fy_table_html(
    df_scope: pd.DataFrame,
    end_date: date | None,
    top_n: int = 10,
    title: str = "📊 Cenários · Quarter FY (TOP 10 • FYTD)",
) -> str:
    """Tabela HTML (Outlook-safe) Cenário x Quarter FY para FY-to-date (Opção 2)."""
    if df_scope is None or df_scope.empty or end_date is None or COL_DATA not in df_scope.columns or COL_CENARIO not in df_scope.columns:
        return (
            "<div style='margin-top:8px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#6c757d;'>"
            "Sem dados de cenário por quarter FY.</div>"
        )

    dfx = df_scope.copy()
    if not pd.api.types.is_datetime64_any_dtype(dfx[COL_DATA]):
        dfx[COL_DATA] = safe_to_datetime(dfx[COL_DATA])
    dfx = dfx.dropna(subset=[COL_DATA]).copy()
    if dfx.empty:
        return (
            "<div style='margin-top:8px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#6c757d;'>"
            "Sem dados de cenário por quarter FY.</div>"
        )

    start_fy = april_start(pd.Timestamp(end_date).date())
    dfx = filter_by_date_range(dfx, start_fy, end_date)
    if dfx.empty:
        return (
            "<div style='margin-top:8px;font-family:Segoe UI,Arial,sans-serif;font-size:12px;color:#6c757d;'>"
            "Sem dados de cenário por quarter FY.</div>"
        )

    dfx = dfx.copy()
    dfx["_QFY_"] = dfx[COL_DATA].apply(lambda x: fiscal_q_label_global(pd.Timestamp(x)))

    pvt = pd.pivot_table(
        dfx,
        index=COL_CENARIO,
        columns="_QFY_",
        values=COL_DATA,
        aggfunc="count",
        fill_value=0,
    )

    # ordem cronológica fiscal dentro do FY corrente
    q_labels = []
    seen = set()
    for d in sorted(dfx[COL_DATA].dropna().tolist()):
        lab = fiscal_q_label_global(pd.Timestamp(d))
        if lab not in seen:
            seen.add(lab)
            q_labels.append(lab)

    for c in q_labels:
        if c not in pvt.columns:
            pvt[c] = 0
    pvt = pvt[q_labels]
    pvt["Total"] = pvt.sum(axis=1)
    pvt = pvt.sort_values(["Total"] + q_labels[::-1], ascending=False).head(int(top_n))

    def _delta_style(cur_val: int, prev_val: int | None):
        if prev_val is None:
            return ("", "", "")
        delta = int(cur_val) - int(prev_val)
        if delta > 0:
            return ("▲ +" + str(delta), "#FDECEC", "#B42318")
        if delta < 0:
            return ("▼ " + str(delta), "#E8F5EE", "#05603A")
        return ("→ 0", "#F3F4F6", "#6B7280")

    rows_html = []
    for i, (cenario, row) in enumerate(pvt.iterrows()):
        bg = "#FAFAFA" if i % 2 == 1 else "#FFFFFF"
        cells = [
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;font-family:Segoe UI,Arial,sans-serif;'>{safe_str(cenario)}</td>"
        ]
        prev_val = None
        for q in q_labels:
            cur_val = int(row[q])
            delta_txt, delta_bg, delta_color = _delta_style(cur_val, prev_val)
            cell_bg = delta_bg if delta_bg else bg
            delta_html = (
                f"<div style='font-size:10px;color:{delta_color};font-weight:800;white-space:nowrap;margin-top:2px;'>{delta_txt}</div>"
                if delta_txt else ""
            )
            cells.append(
                "<td style='border:1px solid #e9ecef;padding:8px;"
                f"background:{cell_bg};font-size:12px;text-align:center;font-family:Segoe UI,Arial,sans-serif;'>"
                f"<div style='font-weight:700;'>{cur_val}</div>{delta_html}</td>"
            )
            prev_val = cur_val
        cells.append(
            f"<td style='border:1px solid #e9ecef;padding:8px;background:{bg};font-size:12px;text-align:center;font-family:Segoe UI,Arial,sans-serif;font-weight:900;'>{int(row['Total'])}</td>"
        )
        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    head_cols = "".join(
        [
            f"<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:center;font-size:12px;'>{q}</th>"
            for q in q_labels
        ]
    )

    return (
        "<div class='card' style='margin-top:24px;'>"
        f"<div style='font-weight:900;margin:0 0 8px;font-size:14px;'>" + safe_str(title) + "</div>"
        "<div class='muted' style='font-size:12px;margin:0 0 10px;'>FY-to-date por quarter fiscal (FY inicia em abril) com delta vs quarter anterior.</div>"
        "<table role='presentation' cellspacing='0' cellpadding='0' style='width:100%;border-collapse:collapse;'>"
        "<thead><tr>"
        "<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:left;font-size:12px;'>Cenário</th>"
        + head_cols +
        "<th style='border:1px solid #e9ecef;padding:8px;background:#F3F4F6;text-align:center;font-size:12px;'>Total</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows_html)}</tbody>"
        "</table></div>"
    )

def build_client_workflow_page_html(
    df_total: pd.DataFrame,
    df_oficial: pd.DataFrame,
    cur_start: date,
    cur_end: date,
    scope_name: str = '',
    *,
    dual_metric_mode: bool = True,
) -> str:
    """Gera HTML para Clientes/Workflows com visão FY-to-date (Opção 2) + Quarter FY.

    Inclui hover na tabela de clientes para mostrar workflows do cliente com qtd e %.
    """

    def fiscal_info(d: date):
        fy_start = april_start(d)
        fy_end = d
        fy_label = fy_start.year + 1
        return fy_start, fy_end, fy_label

    def fiscal_q_label(dt: pd.Timestamp) -> str:
        if pd.isna(dt):
            return ''
        m = int(dt.month)
        fy_year = (dt.year + 1) if m >= 4 else dt.year
        q_num = (((m - 4) % 12) // 3) + 1
        return f"Q{q_num} FY{fy_year}"

    def _clean_axes_local(ax):
        ax.grid(False)
        for side in ["top", "right"]:
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color("#CDD3DA")
        ax.spines["bottom"].set_color("#CDD3DA")

    def build_top_scenarios_by_dimension_local(
        df_current: pd.DataFrame,
        dim_col: str,
        scenario_col: str,
        top_dim: int = 5,
        top_scen: int = 3,
    ) -> list[dict]:
        if df_current is None or df_current.empty or dim_col not in df_current.columns or scenario_col not in df_current.columns:
            return []

        sub = df_current
        if sub.empty:
            return []

        top_vals = sub[dim_col].apply(safe_str)
        top_vals = top_vals[top_vals != ''].value_counts().head(top_dim).index.tolist()

        rows: list[dict] = []
        for dv in top_vals:
            g = sub[sub[dim_col].apply(safe_str) == dv]
            if g.empty:
                continue
            total = max(int(len(g)), 1)
            vc = g[scenario_col].apply(safe_str)
            vc = vc[vc != ''].value_counts().head(top_scen)
            for scen, qtd in vc.items():
                rows.append({'dim': str(dv), 'cenario': str(scen), 'qtd': int(qtd), 'percent': (int(qtd) / total * 100.0)})
        return rows

    def chart_top_barh_base64(pairs, title: str, color: str = '#174E97'):
        try:
            if not pairs:
                return None

            def _trunc(s: str, n: int = 36) -> str:
                s = str(s)
                return s if len(s) <= n else (s[: n - 1] + '…')

            pairs = list(pairs)[:10]
            labels_full = [str(p[0]) for p in pairs][::-1]
            labels = [_trunc(x) for x in labels_full]
            values = [int(p[1]) for p in pairs][::-1]

            fig_h = max(5.2, 0.62 * len(labels) + 2.4)
            fig, ax = plt.subplots(figsize=(20.0, fig_h))
            ax.barh(range(len(values)), values, color=color)
            ax.set_title(title, fontsize=12)
            ax.set_yticks(range(len(labels)))
            ax.set_yticklabels(labels, fontsize=10)
            ax.set_xlabel('Falhas')
            ax.grid(axis='x', linestyle='--', alpha=0.25)
            ax.set_axisbelow(True)
            mx = max(values) if values else 0
            ax.set_xlim(0, (mx * 1.22) if mx else 1)
            for i, v in enumerate(values):
                ax.text(v + (mx * 0.012 if mx else 0.2), i, str(v), va='center', fontsize=9)
            _clean_axes_local(ax)
            fig.tight_layout(pad=0.4)
            buf = BytesIO()
            fig.savefig(buf, format='png', dpi=300, bbox_inches='tight', pad_inches=0.05)
            plt.close(fig)
            return base64.b64encode(buf.getvalue()).decode('utf-8')
        except Exception:
            return None

    def build_fy_volume_table(df_fy: pd.DataFrame, dim_col: str, title: str, top_n: int = 10, include_tipo_breakdown: bool = False) -> str:
        if df_fy is None or df_fy.empty or dim_col not in df_fy.columns:
            return f"<div class='muted' style='font-size:12px;'>Sem dados para {title}.</div>"

        s = df_fy[dim_col].apply(safe_str)
        s = s[s != '']
        if s.empty:
            return f"<div class='muted' style='font-size:12px;'>Sem dados para {title}.</div>"

        vc = s.value_counts().head(top_n)
        total = int(s.shape[0])
        
        # Allow hover for Cliente with Workflow breakdown
        allow_hover = (dim_col == COL_CLIENTE) and (COL_WORKFLOW_BASE in df_fy.columns)
        
        # Check if we should include Tipo de Análise breakdown
        include_breakdown = (dim_col == COL_CLIENTE) and include_tipo_breakdown and ('Tipo de análise' in df_fy.columns)

        def _tooltip_html(label: str) -> str:
            if not allow_hover:
                return ''
            sub = df_fy[df_fy[dim_col].apply(safe_str) == safe_str(label)].copy()
            if sub.empty or COL_WORKFLOW_BASE not in sub.columns:
                return ''
            sw = sub[COL_WORKFLOW_BASE].apply(safe_str)
            sw = sw[sw != '']
            if sw.empty:
                return (
                    "<div class='hover-panel'>"
                    "<div class='ttl'>Workflows do cliente</div>"
                    "<div class='empty'>Sem workflows preenchidos.</div>"
                    "</div>"
                )
            subtotal = int(sw.shape[0])
            rows = []
            for wf, qtd in sw.value_counts().head(12).items():
                pct = (int(qtd) / subtotal * 100.0) if subtotal else 0.0
                rows.append(
                    "<tr>"
                    f"<td style='text-align:left;'>{safe_str(wf)}</td>"
                    f"<td style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{int(qtd)}</td>"
                    f"<td style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{pct:.1f}%</td>"
                    "</tr>"
                )
            return (
                "<div class='hover-panel'>"
                f"<div class='ttl'>🔎 {safe_str(label)}</div>"
                f"<div class='sub'>Workflows do cliente • Total do cliente: {subtotal}</div>"
                "<table role='presentation' cellspacing='0' cellpadding='0'>"
                "<thead><tr><th style='text-align:left;'>Workflow</th><th style='text-align:right;'>Qtd</th><th style='text-align:right;'>%</th></tr></thead>"
                f"<tbody>{''.join(rows)}</tbody>"
                "</table>"
                "</div>"
            )

        # Breakdown by Tipo de Análise for Clientes
        breakdown_data = {}
        if include_breakdown:
            for cliente in vc.index:
                sub = df_fy[df_fy[dim_col].apply(safe_str) == safe_str(cliente)].copy()
                tipo_counts = sub['Tipo de análise'].apply(safe_str).value_counts()
                breakdown_data[safe_str(cliente)] = {
                    'Auditoria': int(tipo_counts.get('Auditoria', 0)),
                    'Contestação': int(tipo_counts.get('Contestação', 0))
                }

        rows = []
        for i, (lab, qtd) in enumerate(vc.items(), start=1):
            pct = (int(qtd) / total * 100.0) if total else 0.0
            label_html = f"<b>{i}.</b> {safe_str(lab)}"
            if allow_hover:
                label_html = (
                    "<span class='hover-detail'>"
                    f"<span class='hover-trigger'>{label_html}<span class='hint-dot'>i</span></span>"
                    f"{_tooltip_html(safe_str(lab))}"
                    "</span>"
                )
            
            if include_breakdown:
                # Extended row with Tipo de Análise breakdown
                breakdown = breakdown_data.get(safe_str(lab), {'Auditoria': 0, 'Contestação': 0})
                audit_cnt = breakdown['Auditoria']
                contest_cnt = breakdown['Contestação']
                audit_pct = (audit_cnt / int(qtd) * 100.0) if int(qtd) > 0 else 0.0
                contest_pct = (contest_cnt / int(qtd) * 100.0) if int(qtd) > 0 else 0.0
                
                rows.append(
                    "<tr>"
                    f"<td data-label='Cliente' style='text-align:left;'>{label_html}</td>"
                    f"<td data-label='Auditoria' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{audit_cnt}<br><span style='font-size:11px;color:#6c757d;'>({audit_pct:.0f}%)</span></td>"
                    f"<td data-label='Contestação' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{contest_cnt}<br><span style='font-size:11px;color:#6c757d;'>({contest_pct:.0f}%)</span></td>"
                    f"<td data-label='Total Falhas' style='text-align:right;font-weight:800;'>{int(qtd)}</td>"
                    f"<td data-label='Participação (%)' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{pct:.1f}%</td>"
                    "</tr>"
                )
            else:
                # Standard row
                rows.append(
                    "<tr>"
                    f"<td data-label='Item' style='text-align:left;'>{label_html}</td>"
                    f"<td data-label='Falhas' style='text-align:right;'>{int(qtd)}</td>"
                    f"<td data-label='Participação (%)' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{pct:.1f}%</td>"
                    "</tr>"
                )

        if include_breakdown:
            dica = " 💡 Breakdown por Tipo de Análise: Auditoria vs Contestação."
            headers_html = f"<thead><tr><th style='text-align:left;'>Cliente</th><th style='text-align:right;'>Auditoria</th><th style='text-align:right;'>Contestação</th><th style='text-align:right;'>Total Falhas</th><th style='text-align:right;'>Participação (%)</th></tr></thead>"
        else:
            dica = " 💡 Passe o mouse no cliente para ver workflows, quantidade e participação." if allow_hover else ""
            headers_html = f"<thead><tr><th style='text-align:left;'>Item</th><th style='text-align:right;'>Falhas</th><th style='text-align:right;'>Participação (%)</th></tr></thead>"
        
        return (
            f"<div class='card'>"
            f"<div style='font-weight:800;margin:0 0 8px;'>{title}</div>"
            f"<div class='muted' style='font-size:12px;margin:0 0 8px;'>Top {min(top_n, len(vc))} • Total FYTD: {total} • Participação (%) = fatia de cada item no total.{dica}</div>"
            f"<table class='table-stack' role='presentation' cellspacing='0' cellpadding='0' style='width:100%;'>"
            f"{headers_html}"
            f"<tbody>{''.join(rows)}</tbody>"
            f"</table>"
            f"</div>"
        )

    def build_fy_quarter_pivot(df_fy: pd.DataFrame, dim_col: str, title: str, top_n: int = 10) -> str:
        if df_fy is None or df_fy.empty or dim_col not in df_fy.columns or COL_DATA not in df_fy.columns:
            return f"<div class='muted' style='font-size:12px;'>Sem dados para {title}.</div>"
        dfx = df_fy.copy()
        if not pd.api.types.is_datetime64_any_dtype(dfx[COL_DATA]):
            dfx[COL_DATA] = safe_to_datetime(dfx[COL_DATA])
        dfx = dfx.dropna(subset=[COL_DATA])
        if dfx.empty:
            return f"<div class='muted' style='font-size:12px;'>Sem dados para {title}.</div>"
        dfx['_dim'] = dfx[dim_col].apply(safe_str)
        dfx = dfx[dfx['_dim'] != ''].copy()
        if dfx.empty:
            return f"<div class='muted' style='font-size:12px;'>Sem dados para {title}.</div>"
        dfx['_q'] = dfx[COL_DATA].apply(lambda x: fiscal_q_label(pd.Timestamp(x)))
        totals = dfx['_dim'].value_counts().head(top_n)
        top_dims = totals.index.tolist()
        sub = dfx[dfx['_dim'].isin(top_dims)].copy()
        piv = pd.pivot_table(sub, index='_dim', columns='_q', aggfunc='size', fill_value=0)
        fy_present = None
        for c in piv.columns.tolist():
            mm = re.match(r"Q\d\s+FY(\d{4})", str(c))
            if mm:
                fy_present = mm.group(1)
                break
        ordered_cols = [f"Q{i} FY{fy_present}" for i in [1, 2, 3, 4]] if fy_present else sorted([str(c) for c in piv.columns.tolist()])
        for c in ordered_cols:
            if c not in piv.columns:
                piv[c] = 0
        piv = piv[ordered_cols]
        piv['Total'] = piv.sum(axis=1)
        piv = piv.sort_values('Total', ascending=False)
        ths = ''.join([f"<th style='text-align:right;'>{safe_str(c)}</th>" for c in ordered_cols])
        html_rows = []
        for dim, row in piv.iterrows():
            tds = []
            prev_val = None
            for c in ordered_cols:
                cur_val = int(row.get(c, 0))
                indicator = ''
                if prev_val is not None:
                    indicator = f"<div style='margin-top:2px;font-size:11px;line-height:1;'>{fmt_delta_html(cur_val, prev_val)}</div>"
                cell_bg = '#FFF5F5' if (prev_val is not None and cur_val > prev_val) else ('#F0FDF4' if (prev_val is not None and cur_val < prev_val) else '#FFFFFF')
                tds.append(f"<td data-label='{safe_str(c)}' style='text-align:right;white-space:nowrap;background:{cell_bg};font-variant-numeric:tabular-nums;'><div>{cur_val}</div>{indicator}</td>")
                prev_val = cur_val
            html_rows.append("<tr>" + f"<td data-label='Item' style='text-align:left;'><b>{safe_str(dim)}</b></td>" + ''.join(tds) + f"<td data-label='Total' style='text-align:right;font-weight:800;white-space:nowrap;font-variant-numeric:tabular-nums;'>{int(row.get('Total',0))}</td>" + "</tr>")
        legenda = "<div class='muted' style='font-size:12px;margin:0 0 8px;'>Top por volume FYTD • Quebra por quarter FY • <span style='color:#DC3545;font-weight:800;'>▲ aumento</span> • <span style='color:#198754;font-weight:800;'>▼ redução</span> • <span style='color:#6B7280;font-weight:800;'>→ estável</span></div>"
        return f"<div class='card'><div style='font-weight:800;margin:0 0 8px;'>{title}</div>{legenda}<table class='table-stack' role='presentation' cellspacing='0' cellpadding='0' style='width:100%;'><thead><tr><th style='text-align:left;'>Item</th>{ths}<th style='text-align:right;'>Total</th></tr></thead><tbody>{''.join(html_rows)}</tbody></table></div>"

    def build_fy_scenarios_table(rows: list, title: str) -> str:
        if not rows:
            return f"<div class='muted' style='font-size:12px;'>Sem cenários para {title}.</div>"
        rows_sorted = sorted(rows, key=lambda r: (-int(r.get('qtd', 0) or 0), -float(r.get('percent', 0.0) or 0.0), safe_str(r.get('dim')), safe_str(r.get('cenario'))))
        html_rows = []
        for r in rows_sorted:
            html_rows.append("<tr>" + f"<td data-label='Grupo' style='text-align:left;'>{safe_str(r.get('dim'))}</td>" + f"<td data-label='Cenário' style='text-align:left;'>{safe_str(r.get('cenario'))}</td>" + f"<td data-label='Qtd' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{int(r.get('qtd',0))}</td>" + f"<td data-label='Participação (%)' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{float(r.get('percent',0.0)):.1f}%</td>" + "</tr>")
        return f"<div class='card'><div style='font-weight:800;margin:0 0 8px;'>{title}</div><table class='table-stack' role='presentation' cellspacing='0' cellpadding='0' style='width:100%;'><thead><tr><th style='text-align:left;'>Grupo</th><th style='text-align:left;'>Cenário</th><th style='text-align:right;'>Qtd</th><th style='text-align:right;'>Participação (%)</th></tr></thead><tbody>{''.join(html_rows)}</tbody></table></div>"

    def build_fy_scenarios(df_fy: pd.DataFrame, dim_col: str, title: str) -> str:
        rows = build_top_scenarios_by_dimension_local(df_fy, dim_col, COL_CENARIO, top_dim=10, top_scen=3) if (df_fy is not None and not df_fy.empty) else []
        return build_fy_scenarios_table(rows, title)

    def build_cliente_tipo_analise_matrix(df_fy: pd.DataFrame, title: str = "📋 Clientes x Tipo de Análise (FY-to-date)", top_n: int = 10) -> str:
        """Cria matriz cruzada: Cliente x Tipo de Análise (Auditoria vs Contestação).
        
        Mostra quantas falhas cada cliente teve de cada tipo, com totais.
        """
        if df_fy is None or df_fy.empty or COL_CLIENTE not in df_fy.columns:
            return f"<div class='muted' style='font-size:12px;'>Sem dados para {title}.</div>"
        
        tipo_analise_col = 'Tipo de análise'
        if tipo_analise_col not in df_fy.columns:
            return f"<div class='muted' style='font-size:12px;'>Coluna '{tipo_analise_col}' não encontrada.</div>"
        
        # Preparar dados
        dfx = df_fy[[COL_CLIENTE, tipo_analise_col]].copy()
        dfx[COL_CLIENTE] = dfx[COL_CLIENTE].apply(safe_str)
        dfx[tipo_analise_col] = dfx[tipo_analise_col].apply(safe_str)
        dfx = dfx[(dfx[COL_CLIENTE] != '') & (dfx[tipo_analise_col] != '')].copy()
        
        if dfx.empty:
            return f"<div class='muted' style='font-size:12px;'>Sem dados válidos para {title}.</div>"
        
        # Contar por cliente (total)
        cliente_total = dfx[COL_CLIENTE].value_counts().head(top_n)
        top_clientes = cliente_total.index.tolist()
        
        # Filtrar só top clientes
        dfx_top = dfx[dfx[COL_CLIENTE].isin(top_clientes)].copy()
        
        # Criar pivô
        piv = pd.pivot_table(dfx_top, index=COL_CLIENTE, columns=tipo_analise_col, aggfunc='size', fill_value=0)
        piv['Total'] = piv.sum(axis=1)
        piv = piv.sort_values('Total', ascending=False)
        
        # Ordear colunas: Auditoria, Contestação, Total
        colnames = []
        if 'Auditoria' in piv.columns:
            colnames.append('Auditoria')
        if 'Contestação' in piv.columns:
            colnames.append('Contestação')
        colnames.append('Total')
        
        for col in piv.columns:
            if col not in colnames and col != 'Total':
                colnames.append(col)
        
        piv = piv[colnames]
        
        # Gerar HTML com porcentagens
        ths = ''.join([f"<th style='text-align:right;'>{safe_str(c)}</th>" for c in colnames])
        html_rows = []
        
        for cliente, row in piv.iterrows():
            tds = []
            total_cliente = int(row.get('Total', 1))
            
            for col in colnames:
                val = int(row.get(col, 0))
                is_total = (col == 'Total')
                
                if is_total:
                    # Total sem porcentagem, só o valor em negrito
                    tds.append(f"<td data-label='{safe_str(col)}' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;font-weight:800;'>{val}</td>")
                else:
                    # Mostrar valor + porcentagem em 2 linhas
                    pct = (val / total_cliente * 100.0) if total_cliente > 0 else 0.0
                    cell_content = f"{val}<br><span style='font-size:11px;color:#6c757d;'>({pct:.0f}%)</span>"
                    tds.append(f"<td data-label='{safe_str(col)}' style='text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{cell_content}</td>")
            
            html_rows.append("<tr>" + f"<td data-label='Cliente' style='text-align:left;'><b>{safe_str(cliente)}</b></td>" + ''.join(tds) + "</tr>")
        
        return (
            f"<div class='card'>"
            f"<div style='font-weight:800;margin:0 0 8px;'>{title}</div>"
            f"<div class='muted' style='font-size:12px;margin:0 0 8px;'>Top {min(top_n, len(cliente_total))} clientes • Distribuição por tipo de análise</div>"
            f"<table class='table-stack' role='presentation' cellspacing='0' cellpadding='0' style='width:100%;'>"
            f"<thead><tr><th style='text-align:left;'>Cliente</th>{ths}</tr></thead>"
            f"<tbody>{''.join(html_rows)}</tbody>"
            f"</table>"
            f"</div>"
        )

    def _panel(df_base: pd.DataFrame, label: str, fy_start: date, fy_end: date, fy_label: int) -> str:
        if df_base is None or df_base.empty:
            return f"<div class='muted' style='font-size:12px;'>Sem dados para {label}.</div>"
        df_fy = filter_by_date_range(df_base, fy_start, fy_end)
        if df_fy.empty:
            return f"<div class='muted' style='font-size:12px;'>Sem dados no FY-to-date para {label}.</div>"

        charts_html = ''
        try:
            pairs_cli = []
            pairs_wf = []
            if COL_CLIENTE in df_fy.columns:
                vc_cli = df_fy[COL_CLIENTE].apply(safe_str)
                vc_cli = vc_cli[vc_cli != ''].value_counts().head(10)
                pairs_cli = [(k, int(v)) for k, v in vc_cli.items()]
            if COL_WORKFLOW_BASE in df_fy.columns:
                vc_wf = df_fy[COL_WORKFLOW_BASE].apply(safe_str)
                vc_wf = vc_wf[vc_wf != ''].value_counts().head(10)
                pairs_wf = [(k, int(v)) for k, v in vc_wf.items()]
            b64_cli = chart_top_barh_base64(pairs_cli, f"Top 10 Clientes · FY{fy_label} (FYTD)")
            b64_wf = chart_top_barh_base64(pairs_wf, f"Top 10 Workflows · FY{fy_label} (FYTD)", color='#6D2077')

            def _img(b64):
                if not b64:
                    return "<div class='muted' style='font-size:12px;'>Sem dados.</div>"
                return f"<img class='zoomable' data-title='Gráfico' src='data:image/png;base64,{b64}' style='width:100%;height:auto;display:block;border-radius:10px;'>"

            charts_html = "<div class='grid2'>" + f"<div style='background:#fff;border:1px solid #e9ecef;border-radius:12px;padding:10px;'>{_img(b64_cli)}</div>" + f"<div style='background:#fff;border:1px solid #e9ecef;border-radius:12px;padding:10px;'>{_img(b64_wf)}</div>" + "</div>"
        except Exception:
            charts_html = ''

        diag_html = ''
        top5_cli = 0.0
        top5_wf = 0.0
        top_cli = ''
        top_wf = ''
        trend_txt = ''
        try:
            total_fy = int(len(df_fy))

            def _top_share(col: str):
                if col not in df_fy.columns:
                    return ('', 0, 0.0)
                s = df_fy[col].apply(safe_str)
                s = s[s != '']
                if s.empty:
                    return ('', 0, 0.0)
                vc = s.value_counts()
                return str(vc.index[0]), int(vc.iloc[0]), (int(vc.iloc[0]) / len(s) * 100.0 if len(s) else 0.0)

            top_cli, top_cli_cnt, top_cli_pct = _top_share(COL_CLIENTE)
            top_wf, top_wf_cnt, top_wf_pct = _top_share(COL_WORKFLOW_BASE)

            def _top5_pct(col: str):
                if col not in df_fy.columns:
                    return 0.0
                s = df_fy[col].apply(safe_str)
                s = s[s != '']
                if s.empty:
                    return 0.0
                vc = s.value_counts().head(5)
                return float(vc.sum()) / len(s) * 100.0 if len(s) else 0.0

            top5_cli = _top5_pct(COL_CLIENTE)
            top5_wf = _top5_pct(COL_WORKFLOW_BASE)
            if COL_DATA in df_fy.columns:
                dfx = df_fy[[COL_DATA]].copy()
                if not pd.api.types.is_datetime64_any_dtype(dfx[COL_DATA]):
                    dfx[COL_DATA] = safe_to_datetime(dfx[COL_DATA])
                dfx = dfx.dropna(subset=[COL_DATA])
                if not dfx.empty:
                    dfx['_q'] = dfx[COL_DATA].apply(lambda x: fiscal_q_label(pd.Timestamp(x)))
                    qvc = dfx['_q'].value_counts()
                    if len(qvc) >= 2:

                        def _qnum(q):
                            mm = re.match(r"Q(\d) FY(\d{4})", str(q))
                            return (int(mm.group(2)), int(mm.group(1))) if mm else (0, 0)

                        qs = sorted(qvc.index.tolist(), key=_qnum)
                        last_q, prev_q = qs[-1], qs[-2]
                        last_cnt, prev_cnt = int(qvc.get(last_q, 0)), int(qvc.get(prev_q, 0))
                        trend = '↑' if last_cnt > prev_cnt else ('↓' if last_cnt < prev_cnt else '→')
                        trend_txt = f"Tendência (volume) {trend}: {prev_q}={prev_cnt} → {last_q}={last_cnt}"
            top_scen = ''
            if COL_CENARIO in df_fy.columns:
                sc = df_fy[COL_CENARIO].apply(safe_str)
                sc = sc[sc != '']
                if not sc.empty:
                    vc = sc.value_counts()
                    top_scen = f"Top cenário FYTD: {str(vc.index[0])} ({int(vc.iloc[0])})"
            bullets = [f"Total FYTD: <b>{total_fy}</b>"]
            if top_cli:
                bullets.append(f"Cliente líder: <b>{safe_str(top_cli)}</b> ({top_cli_cnt} / {top_cli_pct:.1f}%)")
            if top_wf:
                bullets.append(f"Workflow líder: <b>{safe_str(top_wf)}</b> ({top_wf_cnt} / {top_wf_pct:.1f}%)")
            bullets.append(f"Concentração Top 5: Clientes <b>{top5_cli:.1f}%</b> / Workflows <b>{top5_wf:.1f}%</b>")
            if trend_txt:
                bullets.append(trend_txt)
            if top_scen:
                bullets.append(top_scen)
            diag_items = ''.join([f"<li style='margin:6px 0;'>{b}</li>" for b in bullets if b])
            diag_html = f"<div class='card' style='background:#fff;'><div style='font-weight:900;margin:0 0 6px;'>🧪 Diagnóstico rápido</div><ul style='margin:6px 0 0;padding-left:18px;font-size:12px;'>{diag_items}</ul></div>"
        except Exception:
            diag_html = ''

        left = build_fy_volume_table(df_fy, COL_CLIENTE, f"🏢 Clientes · FY{fy_label} (FYTD)", include_tipo_breakdown=True)
        right = build_fy_volume_table(df_fy, COL_WORKFLOW_BASE, f"⚙️ Workflows · FY{fy_label} (FYTD)")
        q_left = build_fy_quarter_pivot(df_fy, COL_CLIENTE, f"🏢 Clientes · Quarter FY{fy_label}")
        q_right = build_fy_quarter_pivot(df_fy, COL_WORKFLOW_BASE, f"⚙️ Workflows · Quarter FY{fy_label}")
        scen_left = build_fy_scenarios(df_fy, COL_CLIENTE, f"🏢 Clientes · Cenários ofensores (FY{fy_label} FYTD)")
        scen_right = build_fy_scenarios(df_fy, COL_WORKFLOW_BASE, f"⚙️ Workflows · Cenários ofensores (FY{fy_label} FYTD)")

        quick_cw_html = ''
        try:
            q1 = f"Total FYTD: <b>{int(len(df_fy))}</b>"
            q2 = f"Concentração Top 5: Clientes <b>{top5_cli:.1f}%</b> • Workflows <b>{top5_wf:.1f}%</b>"
            q3 = trend_txt if trend_txt else 'Tendência: insuficiente para comparação'
            ins = f"Insight: <b>{safe_str(top_cli)}</b> e <b>{safe_str(top_wf)}</b> lideram o volume" if (top_cli or top_wf) else 'Insight: sem líder claro'
            quick_cw_html = f"<div class='quick'><h3>⚡ Resumo · {label}</h3><ul><li>{q1}</li><li>{q2}</li><li>{q3}</li></ul><div class='insight'>{ins}</div></div>"
        except Exception:
            quick_cw_html = ''

        return (
            f"<div class='card'>"
            f"<div style='font-weight:900;margin:0 0 6px;font-size:14px;'>🧭 {label}</div>"
            f"<div class='muted' style='font-size:12px;margin:0 0 10px;'>FY-to-date: {fy_start.strftime('%d/%m/%Y')} → {fy_end.strftime('%d/%m/%Y')}</div>"
            f"{quick_cw_html}"
            f"{charts_html}"
            "<div class='muted' style='font-size:12px;margin:6px 0 0;'>💡 Dica: clique no gráfico para ampliar.</div>"
            f"{diag_html}"
            f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:12px;'>{left}{right}</div>"
            f"<details class='collapsible'><summary>📊 Quarter FY{fy_label}<span class='hint'>(clique para abrir)</span></summary><div class='inner'><div class='grid2'>{q_left}{q_right}</div></div></details>"
            f"<details class='collapsible'><summary>🚨 Cenários ofensores FY{fy_label}<span class='hint'>(clique para abrir)</span></summary><div class='inner'><div class='grid2'>{scen_left}{scen_right}</div></div></details>"
            "</div>"
        )

    fy_start, fy_end, fy_label = fiscal_info(cur_end)
    header = (
        "<div class='card'>"
        "<h3 style='margin:0 0 8px;'>🏢 Clientes / ⚙️ Workflows · Visão FY</h3>"
        f"<div class='muted' style='font-size:12px;'>FY{fy_label} • FY-to-date: {fy_start.strftime('%d/%m/%Y')} → {fy_end.strftime('%d/%m/%Y')} • (FY inicia em abril)</div>"
        "</div>"
    )

    if dual_metric_mode:
        filters_html = build_metric_filter_buttons('clients_workflows_filter', 'total')
        panel_total = f"<div data-metric-panel='total'>{_panel(df_total, '🟣 Total (Geral)', fy_start, fy_end, fy_label)}</div>"
        panel_oficial = f"<div data-metric-panel='oficial' style='display:none;'>{_panel(df_oficial, '✅ Métrica Oficial', fy_start, fy_end, fy_label)}</div>"
        body = (
            f"{header}"
            f"{filters_html}"
            f"<div data-metric-container-id='clients_workflows_filter'>"
            f"{panel_total}"
            f"{panel_oficial}"
            f"</div>"
        )
    else:
        body = f"{header}{_panel(df_oficial, 'Clientes / Workflows', fy_start, fy_end, fy_label)}"
    title = '🏢 Clientes & Workflows · FY-to-date'
    if safe_str(scope_name):
        title += f" - {safe_str(scope_name)}"
    return wrap_simple_page(title, body)
def build_tabs_index_html(
    title: str,
    file_oficial: str,
    file_total: str,
    file_clientwf: str,
    file_suporte: str,
    file_ult3m: str,
    *,
    html_oficial: str | None = None,
    html_total: str | None = None,
    html_clientwf: str | None = None,
    html_suporte: str | None = None,
    html_ult3m: str | None = None,
    html_treinamentos: str | None = None,
    logo_data_uri: str | None = None,
    embed_only: bool = False,
    include_total_tab: bool = True,
    primary_tab_label: str = "Métrica Oficial",
    **kwargs,
) -> str:
        """HTML com abas (para abrir no navegador).

        Abas:
            - Métrica Oficial
            - Total (Geral)
            - Clientes/Workflows
            - Suporte (TEAMS)
            - Falhas últimos 3 meses
            - Acompanhamento de Treinamentos

        Observação: esse índice é para navegação em navegador (usa JS + iframe).
        """
        logo_src = logo_data_uri or resolve_logo_data_uri()

        def _safe_html(s: str) -> str:
            # Evita encerrar o <script> onde o JSON sera injetado.
            return re.sub(r"</script>", "<\\/script>", s or "", flags=re.IGNORECASE)

        def _split_assets(raw_html: str) -> dict:
            if not raw_html:
                return {"body": "", "styles": "", "scripts": ""}
            styles = re.findall(r"<style[^>]*>(.*?)</style>", raw_html, flags=re.IGNORECASE | re.DOTALL)
            scripts = re.findall(r"<script[^>]*>(.*?)</script>", raw_html, flags=re.IGNORECASE | re.DOTALL)
            body_match = re.search(r"<body[^>]*>(.*?)</body>", raw_html, flags=re.IGNORECASE | re.DOTALL)
            body = body_match.group(1) if body_match else raw_html
            body = re.sub(r"<style[^>]*>.*?</style>", "", body, flags=re.IGNORECASE | re.DOTALL)
            body = re.sub(r"<script[^>]*>.*?</script>", "", body, flags=re.IGNORECASE | re.DOTALL)
            return {
                "body": _safe_html(body.strip()),
                "styles": _safe_html("\n".join(styles).strip()),
                "scripts": _safe_html("\n".join(scripts).strip()),
            }

        pages = {
            "oficial": _split_assets(html_oficial or ""),
            "total": _split_assets(html_total or "") if include_total_tab else {"body": "", "styles": "", "scripts": ""},
            "clientwf": _split_assets(html_clientwf or ""),
            "suporte": _split_assets(html_suporte or ""),
            "ult3m": _split_assets(html_ult3m or ""),
            "treinamentos": _split_assets(html_treinamentos or ""),
        }
        pages_js = "var _pages = " + json.dumps(pages, ensure_ascii=False) + ";"
        initial_body = pages["oficial"]["body"] or (pages["total"]["body"] if include_total_tab else "") or ""
        initial_styles = pages["oficial"]["styles"] or (pages["total"]["styles"] if include_total_tab else "") or ""
        link_items = [(primary_tab_label, file_oficial)]
        if include_total_tab and file_total:
            link_items.insert(1, ("Total", file_total))
        link_items.extend([
            ("Clientes/Workflows", file_clientwf),
            ("Suporte TEAMS", file_suporte),
            ("Últimos 3 meses", file_ult3m),
        ])
        file_links = " · ".join(
            f"<a href='{name}'>{label}</a>"
            for label, name in link_items
            if name
        )
        if embed_only or not file_links:
            noscript_hint = "Conteúdo incorporado nas abas abaixo (arquivos HTML individuais não foram gravados)."
        else:
            noscript_hint = f"JavaScript desabilitado neste visualizador. Abra os arquivos HTML anexos separadamente: {file_links}"

        tab_total_html = (
            "<a id='tab-total' data-tab='total' href='#total' onclick='setTab(\"total\");return false;'>Total (Geral)</a>"
            if include_total_tab
            else ""
        )

        html = """<!DOCTYPE html>
<html lang='pt-br'>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width, initial-scale=1'>
  <title>__TITLE__</title>
  <style id='tab-styles'>__INITIAL_TAB_STYLES__</style>
  <style>
    body{font-family:Segoe UI, Arial, sans-serif;background:#fff;margin:0;padding:10px;overflow-x:auto;}
    .wrap{max-width:1900px;margin:0 auto;overflow-x:visible;}
    .hero{background:linear-gradient(90deg,#174E97,#0f3d78);color:#fff;border:1px solid #174E97;border-radius:12px;padding:14px 18px;margin:0 0 10px;}
        .hero h2{display:flex;align-items:center;gap:12px;margin:0;font-size:22px;font-weight:800;}
        .brand-logo{height:34px;width:auto;vertical-align:middle;margin-right:12px;display:inline-block;}
        .hero .sub{margin-top:4px;font-size:12px;opacity:.95;}
    .tabs{display:flex;gap:8px;margin:8px 0 12px;flex-wrap:wrap;}
    .tabs a{text-decoration:none;padding:10px 12px;border:1px solid #e9ecef;border-radius:10px;color:#212529;background:#f8f9fa;font-weight:700;}
    .tabs a.active{background:#174E97;color:#fff;border-color:#174E97;}
    .panel{border:1px solid #e9ecef;border-radius:12px;overflow:visible;background:#fff;}
        .tab-content{min-height:760px;background:#fff;padding:0;}
  </style>
</head>
<body>
  <div class='wrap'>
        <div class='hero'><h2><img class='brand-logo' src='__LOGO_SRC__' alt='Serasa'> __TITLE__</h2><div class='sub'>Navegação rápida entre as visões do relatório</div></div>
    <div class='tabs'>
            <a id='tab-oficial' data-tab='oficial' class='active' href='#oficial' onclick='setTab("oficial");return false;'>__PRIMARY_TAB_LABEL__</a>
            __TAB_TOTAL__
            <a id='tab-clientwf' data-tab='clientwf' href='#clientwf' onclick='setTab("clientwf");return false;'>Clientes/Workflows</a>
            <a id='tab-suporte' data-tab='suporte' href='#suporte' onclick='setTab("suporte");return false;'>Suporte (TEAMS)</a>
            <a id='tab-ult3m' data-tab='ult3m' href='#ult3m' onclick='setTab("ult3m");return false;'>Falhas últimos 3 meses</a>
            <a id='tab-treinamentos' data-tab='treinamentos' href='#treinamentos' onclick='setTab("treinamentos");return false;'>Acompanhamento de Treinamentos</a>
    </div>
    <div class='panel'>
            <noscript>
              <div style='padding:12px 14px;background:#FFF7ED;border:1px solid #FED7AA;border-radius:10px;margin:0 0 10px;font-size:12px;line-height:1.5;'>
                __NOSCRIPT_HINT__
              </div>
            </noscript>
            <div id='tab-content' class='tab-content'>__INITIAL_TAB_BODY__</div>
    </div>
  </div>

<script>
__PAGES_JS__
function _clear(){
    ['tab-oficial','tab-total','tab-clientwf','tab-suporte','tab-ult3m','tab-treinamentos'].forEach(function(id){
        var el=document.getElementById(id);
        if(el) el.classList.remove('active');
    });
}
function _applyScripts(jsText){
    var old=document.getElementById('tab-scripts');
    if(old){ old.parentNode.removeChild(old); }
    var js=document.createElement('script');
    js.id='tab-scripts';
    js.text = jsText || '';
    document.body.appendChild(js);
}
function setTab(which){
    var container=document.getElementById('tab-content');
    var styleEl=document.getElementById('tab-styles');
    _clear();
    if(!container) return;

    if(which==='total'){
        document.getElementById('tab-total').classList.add('active');
        if(typeof _pages !== 'undefined'){
            container.innerHTML = _pages.total.body || '';
            if(styleEl) styleEl.textContent = _pages.total.styles || '';
            _applyScripts(_pages.total.scripts || '');
        }
    }else if(which==='clientwf'){
        document.getElementById('tab-clientwf').classList.add('active');
        if(typeof _pages !== 'undefined'){
            container.innerHTML = _pages.clientwf.body || '';
            if(styleEl) styleEl.textContent = _pages.clientwf.styles || '';
            _applyScripts(_pages.clientwf.scripts || '');
        }
    }else if(which==='suporte'){
        document.getElementById('tab-suporte').classList.add('active');
        if(typeof _pages !== 'undefined'){
            container.innerHTML = _pages.suporte.body || '';
            if(styleEl) styleEl.textContent = _pages.suporte.styles || '';
            _applyScripts(_pages.suporte.scripts || '');
        }
    }else if(which==='ult3m'){
        document.getElementById('tab-ult3m').classList.add('active');
        if(typeof _pages !== 'undefined'){
            container.innerHTML = _pages.ult3m.body || '';
            if(styleEl) styleEl.textContent = _pages.ult3m.styles || '';
            _applyScripts(_pages.ult3m.scripts || '');
        }
    }else if(which==='treinamentos'){
        document.getElementById('tab-treinamentos').classList.add('active');
        if(typeof _pages !== 'undefined'){
            container.innerHTML = _pages.treinamentos.body || '';
            if(styleEl) styleEl.textContent = _pages.treinamentos.styles || '';
            _applyScripts(_pages.treinamentos.scripts || '');
        }
    }else{
        document.getElementById('tab-oficial').classList.add('active');
        if(typeof _pages !== 'undefined'){
            container.innerHTML = _pages.oficial.body || '';
            if(styleEl) styleEl.textContent = _pages.oficial.styles || '';
            _applyScripts(_pages.oficial.scripts || '');
        }
    }
}
(function(){
    var hash=(window.location.hash||'').replace('#','').trim();
    var valid=['oficial','total','clientwf','suporte','ult3m','treinamentos'];
    if(hash && valid.indexOf(hash)>=0){
        setTab(hash);
        return;
    }
    if(typeof _pages !== 'undefined' && _pages.oficial && _pages.oficial.body){
        setTab('oficial');
    }
})();
</script>
</body>
</html>"""
        return (
                html.replace("__TITLE__", str(title))
                .replace("__LOGO_SRC__", str(logo_src))
                .replace("__PAGES_JS__", pages_js or "")
                .replace("__INITIAL_TAB_BODY__", initial_body)
                .replace("__INITIAL_TAB_STYLES__", initial_styles)
                .replace("__FILE_LINKS__", file_links)
                .replace("__NOSCRIPT_HINT__", noscript_hint)
                .replace("__TAB_TOTAL__", tab_total_html)
                .replace("__PRIMARY_TAB_LABEL__", str(primary_tab_label))
                .replace("__FILE_OFICIAL__", str(file_oficial))
                .replace("__FILE_TOTAL__", str(file_total))
                .replace("__FILE_CLIENTWF__", str(file_clientwf))
                .replace("__FILE_SUPORTE__", str(file_suporte))
                .replace("__FILE_ULT3M__", str(file_ult3m))
        )


def inject_email_intro(
    html: str,
    scope_name: str,
    periodo_txt: str,
    anexos_txt: str,
    kpis_total: dict | None = None,
    kpis_oficial: dict | None = None,
    removidas_html: str = "",
    dual_metric_mode: bool = True,
) -> str:
    """Insere um texto de saudação no HTML, mas FORA do container do report.

    Novo: inclui 2 blocos curtinhos de "Resumo 30s" (Total + Métrica Oficial) no corpo do e-mail,
    para orientar a leitura antes de abrir os anexos.
    """
    if not isinstance(html, str) or not html.strip():
        return html

    def _fmt_delta(k: dict | None) -> str:
        if not k:
            return "—"
        return f"{k.get('variacao_perc','—')} ({k.get('variacao_delta','—')})"

    def _safe(k: dict | None, key: str, default="—"):
        if not k:
            return default
        v = k.get(key, default)
        return default if v in (None, "") else v

    def _card(title: str, k: dict | None, accent: str = "#174E97") -> str:
        fail = _safe(k, "fail_mtd_atual", "—")
        prev = _safe(k, "fail_prev_equal", "—")
        delta = _fmt_delta(k)
        reinc = _safe(k, "reinc_total", "—")
        top_qtd = _safe(k, "top1_cenario_qtd", "—")
        top_nome = _safe(k, "top1_cenario_nome", "Sem dados")
        per = _safe(k, "periodo_mtd", periodo_txt)
        per_prev = _safe(k, "periodo_equal_prev", "Sem dados")

        return (
            f"<div style='border:1px solid #e9ecef;border-radius:12px;padding:12px 14px;margin:10px 0;'>"
            f"<div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif;font-size:13px;font-weight:900;color:{accent};margin:0 0 6px;'>{title}</div>"
            f"<div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif;font-size:12px;color:#6c757d;margin:0 0 8px;'>Período: {per} • Comparativo: {per_prev}</div>"
            f"<ul style='margin:0;padding-left:18px;font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif;font-size:12px;color:#212529;'>"
            f"<li style='margin:6px 0;'>Falhas (MTD): <b>{fail}</b> (mês anterior equivalente: <b>{prev}</b>)</li>"
            f"<li style='margin:6px 0;'>Variação vs comparativo: <b>{delta}</b></li>"
            f"<li style='margin:6px 0;'>Reincidentes: <b>{reinc}</b> • Top cenário: <b>{top_nome}</b> ({top_qtd})</li>"
            "</ul>"
            "<div style='margin-top:8px;background:#EEF2FF;border:1px solid #D9E0FF;border-radius:12px;padding:10px 12px;font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif;font-size:12px;color:#111827;'>"
            "<b>Insight:</b> começar pelo <b>HTML com abas</b> para a visão executiva e navegar para os detalhes; usar o <b>XLSX</b> quando precisar filtrar, ordenar ou extrair os dados."
            "</div>"
            "</div>"
        )

    removidas_notice = ""
    if safe_str(removidas_html):
        removidas_notice = (
            "<div style='margin-top:10px;background:#FEF2F2;border:1px solid #FECACA;"
            "border-radius:12px;padding:10px 12px;'>"
            "<div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif;"
            "font-size:12px;color:#374151;line-height:1.55;'>"
            "<b>Novidade:</b> os números deste report já consideram retificações oficiais "
            "do log SharePoint/BI. O detalhamento está no bloco "
            "<span style='color:#B91C1C;font-weight:800;'>Ajustes de base (log SharePoint/BI)</span>, "
            "logo abaixo."
            "</div></div>"
        )

    intro_block = f"""
<div style='margin:0 0 14px;'>
  <div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif; font-size:14px; color:#212529;'><b>Olá, pessoal! Tudo bem?</b></div>
  <div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif; font-size:13px; color:#212529; margin-top:6px;'>Segue o <b>Report de Falhas Críticas</b> ({scope_name}).</div>
  <div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif; font-size:12px; color:#6c757d; margin-top:6px;'>Período: {periodo_txt}</div>
  {removidas_notice}
  <div style='margin-top:10px;background:#F8FAFC;border:1px solid #E5E7EB;border-radius:12px;padding:10px 12px;'><div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif; font-size:12px; font-weight:800; color:#111827; margin:0 0 6px;'>📎 Anexos e como usar</div><div style='font-family:Segoe UI, Roboto, Arial, Helvetica, sans-serif; font-size:12px; color:#374151; line-height:1.5;'>{anexos_txt}</div></div>
</div>
"""

    removidas_block = removidas_html if safe_str(removidas_html) else ""

    insights_html = ""
    try:
        if kpis_total is not None or kpis_oficial is not None:
            if dual_metric_mode:
                insights_html = (
                    "<div style='margin:0 0 14px;'>"
                    + _card("🟣 Resumo 30s · Total (Geral)", kpis_total, accent="#174E97")
                    + _card("✅ Resumo 30s · Métrica Oficial", kpis_oficial, accent="#0f3d78")
                    + "</div>"
                )
            else:
                insights_html = (
                    "<div style='margin:0 0 14px;'>"
                    + _card("✅ Resumo 30s · Report de Falhas Críticas", kpis_oficial, accent="#0f3d78")
                    + "</div>"
                )
    except Exception:
        insights_html = ""

    sep = "<hr style='border:0; border-top:1px solid #e9ecef; margin:0 0 14px;'/>"
    payload = intro_block + removidas_block + insights_html + sep

    m = re.search(r"(<body[^>]*>)", html, flags=re.IGNORECASE)
    if m:
        pos = m.end()
        return html[:pos] + payload + html[pos:]
    return payload + html
