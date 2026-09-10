# -*- coding: utf-8 -*-
"""Agregações, matrizes e blocos HTML de dimensões (fluxo principal)."""

from __future__ import annotations

from datetime import date

import pandas as pd

from report_falhas.io.data_loader import safe_str
import report_falhas.config_report as cfg_report
from report_falhas.periods import april_start, filter_by_date_range as _filter_by_date_range_col
from report_falhas.utils_report import _collapse_topn_with_outros

COL_DATA = cfg_report.COL_DATA
COL_DIFICULDADE = "Nível de Dificuldade"

MIN_COUNT_RANK = 2
TOP_CENARIOS_IMPACTO = 10
TOP_CENARIOS_CRESCIMENTO = 10
TOP_UF_FF = 10


def filter_by_date_range(df: pd.DataFrame, start_d: date, end_d: date) -> pd.DataFrame:
    return _filter_by_date_range_col(df, start_d, end_d, COL_DATA)


def build_quarterly_counts(df: pd.DataFrame | None, start_apr: date, end_date: date) -> list[tuple[str,int]]:
    if df is None or df.empty:
        return []
    if COL_DIFICULDADE in df.columns:
        df_facil = df[df[COL_DIFICULDADE].apply(safe_str).str.contains(r"(?:fácil|facil)", case=False, regex=True)]
    else:
        df_facil = df
    if df_facil.empty:
        return []
    quarters = []
    cur = pd.Timestamp(start_apr)
    fim = pd.Timestamp(end_date)
    while cur <= fim:
        q_start = cur
        q_end = (q_start + pd.DateOffset(months=3)) - pd.DateOffset(days=1)
        if q_end > fim:
            q_end = fim
        qtd = len(filter_by_date_range(df_facil, q_start.date(), q_end.date()))
        fy_year = (q_start.year + 1) if q_start.month >= 4 else q_start.year
        q_num = (((q_start.month - 4) % 12) // 3) + 1
        q_label = f"Q{q_num} FY{fy_year}"
        quarters.append((q_label, int(qtd)))
        cur = q_start + pd.DateOffset(months=3)
    return quarters


def top_dimension_items(df_current: pd.DataFrame, col_name: str, top_n: int = 5, cls_filter: str | None = None) -> list[dict]:
    """Top N de uma dimensão no mês atual.
    Retorna: [{'label':..., 'qtd':..., 'percent':...}, ...]
    Se cls_filter (FN/FP/OUTROS) for informado, filtra por df['__Classe__'].
    """
    if df_current is None or df_current.empty or col_name not in df_current.columns:
        return []

    sub = df_current
    if cls_filter is not None:
        if '__Classe__' not in sub.columns:
            return []
        sub = sub[sub['__Classe__'] == cls_filter]

    total = max(int(len(sub)), 1)
    s = sub[col_name].apply(safe_str)
    s = s[s != '']
    if s.empty:
        return []

    vc = s.value_counts().head(top_n)
    out: list[dict] = []
    for label, qtd in vc.items():
        pct = (int(qtd) / total) * 100.0
        out.append({'label': str(label), 'qtd': int(qtd), 'percent': float(pct)})
    return out


def render_dimension_triplet_outlook(section_title: str, col_label: str,
                                     items_total: list, items_fn: list, items_fp: list) -> str:
    """Bloco em 3 colunas (Total/FN/FP) com tabelas compactas (Outlook-safe)."""

    def mini_table(items: list) -> str:
        if not items:
            return "<div style='font-size:12px;color:#6c757d;'>Sem dados.</div>"

        html = (
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
            "style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
            "<colgroup><col style='width:66%'><col style='width:17%'><col style='width:17%'></colgroup>"
            "<thead><tr style='background:#F3F4F6;'>"
            f"<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:left;'>{col_label}</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>Qtd</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>%</th>"
            "</tr></thead><tbody>"
        )
        for i, it in enumerate(items):
            bg = "background:#FAFAFA;" if i % 2 == 1 else ""
            html += (
                "<tr>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;{bg}overflow-wrap:break-word;'>{it['label']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{it['qtd']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{it['percent']:.1f}%</td>"
                "</tr>"
            )
        html += "</tbody></table>"
        return html

    return (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        f"<h3 style='margin:0 0 6px;'>{section_title}</h3>"
        "<div style='font-size:12px;color:#6c757d;margin:0 0 6px;'>Legenda: 🟣 Total • 🟥 FN • 🟧 FP</div>"
        "<div style='font-size:12px;color:#6c757d;margin:0 0 10px;line-height:1.35;'>"
        "<b>FN</b> (Não sinalizado): o caso deveria ter sido sinalizado, mas não foi. "
        "<b>FP</b> (Sinalização incorreta): o caso foi sinalizado indevidamente.</div>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;'>"
        "<tr>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟣 Total</div>"
        f"{mini_table(items_total)}"
        "</td>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟥 FN</div>"
        f"{mini_table(items_fn)}"
        "</td>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟧 FP</div>"
        f"{mini_table(items_fp)}"
        "</td>"
        "</tr></table></div>"
    )

def render_dimension_single_outlook(section_title: str, col_label: str, items_total: list) -> str:
    """Bloco em 1 coluna (visao unica) com tabela compacta (Outlook-safe)."""
    def mini_table(items: list) -> str:
        if not items:
            return "<div style='font-size:12px;color:#6c757d;'>Sem dados.</div>"
        html = (
            "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
            "<colgroup><col style='width:72%'><col style='width:14%'><col style='width:14%'></colgroup>"
            "<thead><tr style='background:#F3F4F6;'>"
            f"<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:left;'>{col_label}</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>Qtd</th>"
            "<th style='padding:6px 8px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>%</th>"
            "</tr></thead><tbody>"
        )
        for i, it in enumerate(items):
            bg = "background:#FAFAFA;" if i % 2 == 1 else ""
            html += (
                "<tr>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;{bg}overflow-wrap:break-word;'>{it['label']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{it['qtd']}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{it['percent']:.1f}%</td>"
                "</tr>"
            )
        html += "</tbody></table>"
        return html

    return (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        f"<h3 style='margin:0 0 10px;'>{section_title}</h3>"
        f"{mini_table(items_total)}"
        "</div>"
    )


def render_matrix_single_outlook(section_title: str, mat_total) -> str:
    if mat_total is None or getattr(mat_total, 'empty', True):
        return "<div style='font-size:12px;color:#6c757d;'>Sem dados.</div>"
    return (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        f"<h3 style='margin:0 0 10px;'>{section_title}</h3>"
        + render_matrix_html(mat_total, 'Matriz (Falhas)', row_label='Tipo') +
        "</div>"
    )


def render_scenarios_single_outlook(section_title: str, rows_total: list, dim_label: str) -> str:
    return (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        f"<h3 style='margin:0 0 10px;'>{section_title}</h3>"
        + render_scenarios_table(rows_total, 'Top cenarios', dim_label) +
        "</div>"
    )
def build_matrix_tipo_x_uf(df_current: pd.DataFrame, tipo_col: str, uf_col: str,
                           top_tipos: int = 5, top_ufs: int = 10, cls_filter: str | None = None) -> pd.DataFrame:
    """Matriz Tipo×UF (Top N + Outros) com total de linha/coluna."""
    if df_current is None or df_current.empty:
        return pd.DataFrame()

    sub = df_current
    if cls_filter is not None:
        if '__Classe__' not in sub.columns:
            return pd.DataFrame()
        sub = sub[sub['__Classe__'] == cls_filter]

    if sub.empty or tipo_col not in sub.columns or uf_col not in sub.columns:
        return pd.DataFrame()

    tipos = _collapse_topn_with_outros(sub[tipo_col], top_tipos)
    ufs = _collapse_topn_with_outros(sub[uf_col], top_ufs)

    tipo_set = set(tipos.index.tolist())
    uf_set = set(ufs.index.tolist())

    tmp = sub[[tipo_col, uf_col]].copy()
    tmp[tipo_col] = tmp[tipo_col].apply(safe_str).apply(lambda x: x if x in tipo_set else 'Outros')
    tmp[uf_col] = tmp[uf_col].apply(safe_str).apply(lambda x: x if x in uf_set else 'Outros')

    piv = pd.pivot_table(tmp, index=tipo_col, columns=uf_col, aggfunc='size', fill_value=0)

    # garantir ordem
    for t in tipos.index:
        if t not in piv.index:
            piv.loc[t] = 0
    for u in ufs.index:
        if u not in piv.columns:
            piv[u] = 0

    piv = piv.loc[list(tipos.index), list(ufs.index)]
    piv['Total'] = piv.sum(axis=1)
    piv.loc['Total'] = piv.sum(axis=0)
    return piv


def render_matrix_html(df_mat: pd.DataFrame, title: str, row_label: str = 'Tipo') -> str:
    """Renderiza a matriz Tipo×UF em HTML (Outlook-safe)."""
    if df_mat is None or df_mat.empty:
        return "<div style='font-size:12px;color:#6c757d;'>Sem dados.</div>"

    cols = list(df_mat.columns)
    # col widths: primeira larga, demais pequenas
    colgroup = "<colgroup><col style='width:30%'>" + "".join(["<col style='width:7%'>" for _ in cols]) + "</colgroup>"

    ths = "".join([f"<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;white-space:nowrap;'>{safe_str(c)}</th>" for c in cols])

    html = (
        f"<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>{title}</div>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
        "style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
        + colgroup +
        "<thead><tr style='background:#F3F4F6;'>"
        f"<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:left;'>{row_label}</th>"
        + ths +
        "</tr></thead><tbody>"
    )

    for i, (idx, row) in enumerate(df_mat.iterrows()):
        bg = "background:#FAFAFA;" if i % 2 == 1 else ""
        is_total = safe_str(idx).lower() == 'total'
        fw = "font-weight:800;" if is_total else ""
        html += f"<tr><td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}{fw}overflow-wrap:break-word;'>{safe_str(idx)}</td>"
        for c in cols:
            v = int(row.get(c, 0))
            html += f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}{fw}text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums;'>{v}</td>"
        html += "</tr>"

    html += "</tbody></table>"
    return html


def render_matrix_triplet_outlook(section_title: str, mat_total: pd.DataFrame, mat_fn: pd.DataFrame, mat_fp: pd.DataFrame) -> str:
    """Container com 3 colunas (Total/FN/FP) contendo a matriz Tipo×UF."""
    return (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        f"<h3 style='margin:0 0 6px;'>{section_title}</h3>"
        "<div style='font-size:12px;color:#6c757d;margin:0 0 10px;line-height:1.35;'>"
        "Cruzamento entre <b>Tipo de documento</b> e <b>UF</b> (Top N + Outros).<br>"
        "Dica: use a coluna <b>Total</b> para ver os tipos mais frequentes e as UFs mais frequentes.</div>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;'>"
        "<tr>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟣 Total</div>"
        + render_matrix_html(mat_total, 'Matriz (Total)', row_label='Tipo') +
        "</td>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟥 FN</div>"
        + render_matrix_html(mat_fn, 'Matriz (FN)', row_label='Tipo') +
        "</td>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟧 FP</div>"
        + render_matrix_html(mat_fp, 'Matriz (FP)', row_label='Tipo') +
        "</td>"
        "</tr></table></div>"
    )


def build_top_scenarios_by_dimension(df_current: pd.DataFrame, dim_col: str, scenario_col: str,
                                     top_dim: int = 5, top_scen: int = 3, cls_filter: str | None = None) -> list[dict]:
    """Para cada valor Top da dimensão, pega Top cenários."""
    if df_current is None or df_current.empty or dim_col not in df_current.columns or scenario_col not in df_current.columns:
        return []

    sub = df_current
    if cls_filter is not None:
        if '__Classe__' not in sub.columns:
            return []
        sub = sub[sub['__Classe__'] == cls_filter]

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
            rows.append({'dim': str(dv), 'cenario': str(scen), 'qtd': int(qtd), 'percent': (int(qtd)/total*100.0)})
    return rows


# ====== Foco Treinamento: Comparativos (sem jargão) + filtro Formatação/Fonte ======
TOP_CENARIOS_IMPACTO = 10
TOP_CENARIOS_CRESCIMENTO = 10
TOP_UF_FF = 10
MIN_COUNT_RANK = 2

def build_rank_delta(df_cur: pd.DataFrame, df_prev: pd.DataFrame, col: str,
                     top_impact: int = 10, top_growth: int = 10,
                     min_count: int = MIN_COUNT_RANK):
    """Ranking simples com comparação vs período equivalente (para Treinamento).

    Retorna dict com:
      - impacto: lista (label, cur, share)
      - crescimento: lista (label, cur, prev, delta, delta_pct)
      - missing_pct: % vazios na coluna (cur)
    """
    if df_cur is None or df_cur.empty or col not in df_cur.columns:
        return {'impacto': [], 'crescimento': [], 'missing_pct': 0.0, 'total_valid': 0}

    s_cur_all = df_cur[col].apply(safe_str)
    missing = float((s_cur_all == '').mean() * 100.0) if len(s_cur_all) else 0.0
    s_cur = s_cur_all[s_cur_all != '']
    total_valid = int(len(s_cur))
    if total_valid == 0:
        return {'impacto': [], 'crescimento': [], 'missing_pct': missing, 'total_valid': 0}

    cur_counts = s_cur.value_counts()

    prev_counts = pd.Series(dtype=int)
    if df_prev is not None and (not df_prev.empty) and (col in df_prev.columns):
        s_prev = df_prev[col].apply(safe_str)
        s_prev = s_prev[s_prev != '']
        if not s_prev.empty:
            prev_counts = s_prev.value_counts()

    impacto_rows = []
    for lab, qtd in cur_counts.head(top_impact).items():
        if int(qtd) < min_count:
            continue
        impacto_rows.append({'label': str(lab), 'cur': int(qtd), 'share': (int(qtd) / total_valid) * 100.0})

    all_labels = set(cur_counts.index.tolist()) | set(prev_counts.index.tolist())
    grow = []
    for lab in all_labels:
        cur = int(cur_counts.get(lab, 0))
        prev = int(prev_counts.get(lab, 0))
        delta = cur - prev
        if cur < min_count and abs(delta) < min_count:
            continue
        delta_pct = None
        if prev > 0:
            delta_pct = ((cur - prev) / prev) * 100.0
        grow.append({'label': str(lab), 'cur': cur, 'prev': prev, 'delta': delta, 'delta_pct': delta_pct})

    grow = sorted(grow, key=lambda r: (r['delta'], r['cur']), reverse=True)[:top_growth]

    return {'impacto': impacto_rows, 'crescimento': grow, 'missing_pct': missing, 'total_valid': total_valid}


# render_rank_delta_outlook / render_projecoes_* / render_como_ler_* → report_falhas/outlook_render.py


def render_scenarios_table(rows: list[dict], title: str, dim_label: str) -> str:
    if not rows:
        return "<div style='font-size:12px;color:#6c757d;'>Sem dados.</div>"

    html = (
        f"<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>{title}</div>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' "
        "style='border-collapse:collapse;width:100%;table-layout:fixed;font-size:11px;line-height:1.25;'>"
        "<colgroup><col style='width:30%'><col style='width:45%'><col style='width:12.5%'><col style='width:12.5%'></colgroup>"
        "<thead><tr style='background:#F3F4F6;'>"
        f"<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:left;'>{dim_label}</th>"
        "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:left;'>Cenário</th>"
        "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;'>Qtd</th>"
        "<th style='padding:6px 6px;border-bottom:1px solid #E5E7EB;text-align:right;'>%</th>"
        "</tr></thead><tbody>"
    )

    for i, r in enumerate(rows):
        bg = "background:#FAFAFA;" if i % 2 == 1 else ""
        html += (
            "<tr>"
            f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}overflow-wrap:break-word;'>{safe_str(r.get('dim'))}</td>"
            f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}overflow-wrap:break-word;'>{safe_str(r.get('cenario'))}</td>"
            f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;'>{int(r.get('qtd',0))}</td>"
            f"<td style='padding:6px 6px;border-bottom:1px solid #EEE;{bg}text-align:right;white-space:nowrap;'>{float(r.get('percent',0.0)):.1f}%</td>"
            "</tr>"
        )

    html += "</tbody></table>"
    return html


def render_scenarios_triplet_outlook(section_title: str, rows_total: list, rows_fn: list, rows_fp: list, dim_label: str) -> str:
    return (
        "<div style='background:#F6F7F9;border:1px solid #e9ecef;border-radius:10px;padding:12px;margin:10px 0 14px;'>"
        f"<h3 style='margin:0 0 6px;'>{section_title}</h3>"
        "<div style='font-size:12px;color:#6c757d;margin:0 0 10px;line-height:1.35;'>Top cenários por grupo (mês atual).</div>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;table-layout:fixed;'>"
        "<tr>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟣 Total</div>" + render_scenarios_table(rows_total, 'Top cenários (Total)', dim_label) +
        "</td>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟥 FN</div>" + render_scenarios_table(rows_fn, 'Top cenários (FN)', dim_label) +
        "</td>"
        "<td width='33.33%' valign='top' style='padding:6px;'>"
        "<div style='font-weight:800;margin-bottom:6px;font-size:12px;'>🟧 FP</div>" + render_scenarios_table(rows_fp, 'Top cenários (FP)', dim_label) +
        "</td>"
        "</tr></table></div>"
    )
