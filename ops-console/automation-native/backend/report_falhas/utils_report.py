# -*- coding: utf-8 -*-
"""Helpers de formatacao e agregacao leves para o report."""

from __future__ import annotations

import pandas as pd

from report_falhas.io.data_loader import safe_str


MESES_ABREV_BR = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

MESES_NOME_BR = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril", 5: "maio", 6: "junho",
    7: "julho", 8: "agosto", 9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


def fmt_mes_ano_br(dt) -> str:
    """Formata data/mês no padrão abreviado PT-BR: Jan/26, Fev/26, Dez/25."""
    if dt is None:
        return ""
    try:
        if hasattr(pd, "isna") and pd.isna(dt):
            return ""
    except Exception:
        pass
    try:
        ts = pd.Timestamp(dt)
        return f"{MESES_ABREV_BR.get(int(ts.month), f'{int(ts.month):02d}')}/{int(ts.year) % 100:02d}"
    except Exception:
        return safe_str(dt)


def fmt_data_br(data_str) -> str:
    """Converte data ISO (2026-01-15) para formato Mês/Ano (Jan/26)."""
    if not data_str:
        return ""
    try:
        if isinstance(data_str, str) and "-" in str(data_str):
            parts = str(data_str).split("-")
            if len(parts) >= 2:
                ano = int(parts[0]) % 100
                mes = int(parts[1])
                return f"{MESES_ABREV_BR.get(mes, f'{mes:02d}')}/{ano:02d}"
        ts = pd.Timestamp(data_str)
        return f"{MESES_ABREV_BR.get(int(ts.month), f'{int(ts.month):02d}')}/{int(ts.year) % 100:02d}"
    except Exception:
        return safe_str(data_str)


def fmt_delta_html(cur, prev) -> str:
    """Retorna HTML com seta/cor para variação vs período anterior."""
    try:
        cur_i = int(cur or 0)
        prev_i = int(prev or 0)
    except Exception:
        cur_i = int(pd.to_numeric(cur, errors="coerce") or 0)
        prev_i = int(pd.to_numeric(prev, errors="coerce") or 0)
    if cur_i > prev_i:
        return (
            f"<span style='color:#DC3545;font-weight:800;white-space:nowrap;'>"
            f"▲ +{cur_i - prev_i}</span>"
        )
    if cur_i < prev_i:
        return (
            f"<span style='color:#198754;font-weight:800;white-space:nowrap;'>"
            f"▼ {cur_i - prev_i}</span>"
        )
    return "<span style='color:#6B7280;font-weight:800;white-space:nowrap;'>→ 0</span>"


def build_metric_filter_buttons(filter_id: str, default_metric: str = "total") -> str:
    """Gera botões de filtro para alternar entre métricas (Total/Oficial)."""
    active_total = "active" if default_metric == "total" else ""
    active_oficial = "active" if default_metric == "oficial" else ""

    return (
        "<div class='filter-buttons'>"
        f"<button class='filter-btn {active_total}' data-filter-id='{filter_id}' data-metric='total' type='button'>🟣 Total (Geral)</button>"
        f"<button class='filter-btn {active_oficial}' data-filter-id='{filter_id}' data-metric='oficial' type='button'>✅ Métrica Oficial</button>"
        "</div>"
    )


def _collapse_topn_with_outros(series: pd.Series, top_n: int):
    s = series.apply(safe_str)
    s = s[s != ""]
    vc = s.value_counts()
    top = vc.head(top_n).copy()
    outros = int(vc.iloc[top_n:].sum())
    if outros > 0:
        top.loc["Outros"] = outros
    return top
