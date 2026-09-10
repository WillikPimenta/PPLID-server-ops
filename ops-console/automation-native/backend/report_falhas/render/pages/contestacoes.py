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
from report_falhas.falhas_removidas import (
    filter_removidas_period,
    filter_removidas_scope,
    get_removidas_state,
    summarize_removidas_period,
)
from report_falhas.html_blocks import format_html as format_html_blocks
from report_falhas.html_blocks import table_row_cols as table_row_cols_blocks
from report_falhas.kpis import is_formatacao_fonte
from report_falhas.matricula_utils import clean_matricula_unified as _clean_matricula_pair
from report_falhas.periods import (
    add_months_first_day,
    april_start,
    filter_by_date_range_on,
    first_day,
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

from report_falhas.render.pages.common import wrap_simple_page


# ===== Função Centralizada de Limpeza de Matrícula =====
def _contest_filter_scope(df: pd.DataFrame, scope_name: str = "") -> pd.DataFrame:
    if df is None or df.empty or not safe_str(scope_name):
        return df.copy() if df is not None else pd.DataFrame()
    dfx = df.copy()
    candidates = []
    for c in ["Localidade Base", "Cidade"]:
        if c in dfx.columns:
            candidates.append(c)
    if not candidates:
        return dfx
    scope_norm = normalize_text(scope_name)
    mask = pd.Series(False, index=dfx.index)
    for c in candidates:
        mask = mask | (dfx[c].apply(normalize_text).str.contains(scope_norm, na=False))
    if mask.any():
        return dfx[mask].copy()
    return dfx


def _contest_filter_period(df: pd.DataFrame, start_d: date | None, end_d: date | None) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame() if df is None else df.copy()
    dfx = df.copy()
    date_candidates = []
    for c in ["Data de Análise Base", "Data", "Data Contestação", "Created"]:
        if c in dfx.columns:
            if not pd.api.types.is_datetime64_any_dtype(dfx[c]):
                dfx[c] = safe_to_datetime(dfx[c])
            date_candidates.append(c)
    if not date_candidates or start_d is None or end_d is None:
        return dfx
    c = date_candidates[0]
    mask = dfx[c].notna() & (dfx[c].dt.date >= start_d) & (dfx[c].dt.date <= end_d)
    return dfx[mask].copy()


def _fmt_dt_br(x):
    try:
        if pd.isna(x):
            return ""
        return pd.Timestamp(x).strftime("%d/%m/%Y")
    except Exception:
        return safe_str(x)


def build_contestacoes_page_html(cur_start: date, cur_end: date, scope_name: str = "") -> str:
    res, det, _ = get_contest_state()
    det = _contest_filter_scope(det, scope_name)
    det_periodo = _contest_filter_period(det, cur_start, cur_end)

    prot_ret = int(det_periodo["Protocolo"].nunique()) if (det_periodo is not None and not det_periodo.empty and "Protocolo" in det_periodo.columns) else 0
    linhas_ret = int(len(det_periodo)) if det_periodo is not None else 0
    base_before = int(res.iloc[0]["Registros base antes"]) if (res is not None and not res.empty and "Registros base antes" in res.columns) else 0
    base_after = int(res.iloc[0]["Registros base depois"]) if (res is not None and not res.empty and "Registros base depois" in res.columns) else 0

    tiles = (
        "<div class='card'>"
        "<div style='display:grid;grid-template-columns:repeat(3,1fr);gap:12px;'>"
        f"<div style='border:1px solid #E5E7EB;border-radius:12px;padding:12px;'><div class='muted' style='font-size:12px;'>Protocolos retirados (únicos)</div><div style='font-size:28px;font-weight:900;'>{prot_ret}</div><div class='muted' style='font-size:12px;'>Base x contestação por Protocolo</div></div>"
        f"<div style='border:1px solid #E5E7EB;border-radius:12px;padding:12px;'><div class='muted' style='font-size:12px;'>Linhas removidas</div><div style='font-size:28px;font-weight:900;'>{linhas_ret}</div><div class='muted' style='font-size:12px;'>No período filtrado</div></div>"
        f"<div style='border:1px solid #E5E7EB;border-radius:12px;padding:12px;'><div class='muted' style='font-size:12px;'>Base (antes → depois)</div><div style='font-size:28px;font-weight:900;'>{base_before} → {base_after}</div><div class='muted' style='font-size:12px;'>Contagem em linhas na Base</div></div>"
        "</div>"
        "</div>"
    )

    if det_periodo is None or det_periodo.empty:
        detalhe_html = "<div class='card'><div style='font-size:12px;color:#6B7280;'>Nenhuma contestação elegível no período.</div></div>"
    else:
        cols = [
            "ID",
            "Data",
            "Fonte",
            "Protocolo",
            "Falha Procedente?",
            "Status Falha Retirada",
            "Cliente Base",
            "Workflow Base",
            "Localidade Base",
            "Novo cenário Base",
        ]
        for c in cols:
            if c not in det_periodo.columns:
                det_periodo[c] = ""
        rows = []
        for _, r in det_periodo.sort_values(["Data de Análise Base", "Data"], ascending=[False, False], na_position="last").head(500).iterrows():
            rows.append(
                "<tr>"
                f"<td data-label='ID'>{safe_str(r.get('ID',''))}</td>"
                f"<td data-label='Data'>{_fmt_dt_br(r.get('Data'))}</td>"
                f"<td data-label='Fonte'>{safe_str(r.get('Fonte',''))}</td>"
                f"<td data-label='Protocolo'>{safe_str(r.get('Protocolo',''))}</td>"
                f"<td data-label='Falha Procedente?'>{safe_str(r.get('Falha Procedente?',''))}</td>"
                f"<td data-label='Status Falha Retirada'>{safe_str(r.get('Status Falha Retirada',''))}</td>"
                f"<td data-label='Cliente Base'>{safe_str(r.get('Cliente Base',''))}</td>"
                f"<td data-label='Workflow Base'>{safe_str(r.get('Workflow Base',''))}</td>"
                f"<td data-label='Localidade Base'>{safe_str(r.get('Localidade Base',''))}</td>"
                f"<td data-label='Novo cenário Base'>{safe_str(r.get('Novo cenário Base',''))}</td>"
                "</tr>"
            )
        detalhe_html = (
            "<div class='card'>"
            "<div style='font-weight:900;margin:0 0 8px;'>Detalhe</div>"
            "<table class='table-stack' role='presentation' cellspacing='0' cellpadding='0' style='width:100%;'>"
            "<thead><tr>"
            "<th>ID</th><th>Data</th><th>Fonte</th><th>Protocolo</th><th>Falha Procedente?</th><th>Status Falha Retirada</th><th>Cliente Base</th><th>Workflow Base</th><th>Localidade Base</th><th>Novo cenário Base</th>"
            "</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
            "</div>"
        )

    body = (
        "<div class='card'>"
        "<h3 style='margin:0 0 6px;'>Contestações</h3>"
        f"<div class='muted' style='font-size:12px;'>Escopo: {safe_str(scope_name) or 'Geral'} • Período: {cur_start.strftime('%d/%m/%Y')} → {cur_end.strftime('%d/%m/%Y')}</div>"
        "</div>"
        + tiles
        + detalhe_html
    )
    title = "Contestações"
    if safe_str(scope_name):
        title += f" - {safe_str(scope_name)}"
    return wrap_simple_page(title, body)


def build_falhas_retiradas_page_html(cur_start: date, cur_end: date, scope_name: str = "") -> str:
    _, det, _ = get_contest_state()
    det = _contest_filter_scope(det, scope_name)
    det = _contest_filter_period(det, cur_start, cur_end)
    if det is None or det.empty:
        body = "<div class='card'><div style='font-size:12px;color:#6B7280;'>Nenhum protocolo foi retirado por contestação no período.</div></div>"
        title = "Falhas retiradas"
        if safe_str(scope_name):
            title += f" - {safe_str(scope_name)}"
        return wrap_simple_page(title, body)

    cols = ["ID", "Data", "Fonte", "Protocolo", "Status Falha Retirada"]
    for c in cols:
        if c not in det.columns:
            det[c] = ""

    rows = []
    for _, r in det.sort_values(["Data de Análise Base", "Data"], ascending=[False, False], na_position="last").head(500).iterrows():
        rows.append(
            "<tr>"
            f"<td data-label='ID'>{safe_str(r.get('ID',''))}</td>"
            f"<td data-label='Data'>{_fmt_dt_br(r.get('Data'))}</td>"
            f"<td data-label='Fonte'>{safe_str(r.get('Fonte',''))}</td>"
            f"<td data-label='Protocolo'>{safe_str(r.get('Protocolo',''))}</td>"
            f"<td data-label='Status Falha Retirada'>{safe_str(r.get('Status Falha Retirada',''))}</td>"
            "</tr>"
        )
    body = (
        "<div class='card'>"
        "<h3 style='margin:0 0 6px;'>Falhas retiradas</h3>"
        f"<div class='muted' style='font-size:12px;'>Escopo: {safe_str(scope_name) or 'Geral'} • Período: {cur_start.strftime('%d/%m/%Y')} → {cur_end.strftime('%d/%m/%Y')}</div>"
        "</div>"
        "<div class='card'>"
        "<table class='table-stack' role='presentation' cellspacing='0' cellpadding='0' style='width:100%;'>"
        "<thead><tr><th>ID</th><th>Data</th><th>Fonte</th><th>Protocolo</th><th>Status Falha Retirada</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
    )
    title = "Falhas retiradas"
    if safe_str(scope_name):
        title += f" - {safe_str(scope_name)}"
    return wrap_simple_page(title, body)


def _fmt_dt_br(val) -> str:
    if val is None or (hasattr(pd, "isna") and pd.isna(val)):
        return ""
    if isinstance(val, (datetime, date)):
        return val.strftime("%d/%m/%Y %H:%M") if isinstance(val, datetime) else val.strftime("%d/%m/%Y")
    try:
        ts = pd.Timestamp(val)
        if pd.isna(ts):
            return ""
        return ts.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return safe_str(val)


def build_falhas_removidas_page_html(cur_start: date, cur_end: date, scope_name: str = "") -> str:
    _, det, _ = get_removidas_state()
    det = filter_removidas_scope(det, scope_name)
    det = filter_removidas_period(det, cur_start, cur_end)
    stats = summarize_removidas_period(det, cur_start)

    resumo_html = ""
    if int(stats.get("unicas", 0)) > 0:
        ret_txt = ""
        if int(stats.get("retroativas", 0)) > 0:
            meses = safe_str(stats.get("meses_retroativos_txt", ""))
            ret_txt = f" • Retroativas: {int(stats['retroativas'])}"
            if meses:
                ret_txt += f" ({meses})"
        resumo_html = (
            "<div class='card'>"
            "<h3 style='margin:0 0 6px;'>Resumo do período</h3>"
            f"<div class='muted' style='font-size:12px;'>"
            f"Eventos no log: {int(stats.get('eventos', 0))} • "
            f"Falhas distintas removidas: {int(stats.get('unicas', 0))}"
            f"{ret_txt}"
            "</div></div>"
        )

    if det is None or det.empty:
        body = (
            resumo_html
            + "<div class='card'><div style='font-size:12px;color:#6B7280;'>"
            "Nenhuma falha removida (log SharePoint) no período/escopo.</div></div>"
        )
        title = "Falhas removidas"
        if safe_str(scope_name):
            title += f" - {safe_str(scope_name)}"
        return wrap_simple_page(title, body)

    cols = [
        "ID", "DateRemoved", "Type", "Protocolo", "Matrícula Log",
        "Motivo Falha Log", "Etapa Log", "UserRemove", "Motivo", "Status Match",
    ]
    for c in cols:
        if c not in det.columns:
            det[c] = ""

    rows = []
    for _, r in det.sort_values(["DateRemoved"], ascending=False, na_position="last").head(500).iterrows():
        status = safe_str(r.get("Status Match", ""))
        status_cls = "color:#059669;" if status == "removido" else "color:#B45309;"
        rows.append(
            "<tr>"
            f"<td data-label='ID'>{safe_str(r.get('ID',''))}</td>"
            f"<td data-label='DateRemoved'>{_fmt_dt_br(r.get('DateRemoved'))}</td>"
            f"<td data-label='Type'>{safe_str(r.get('Type',''))}</td>"
            f"<td data-label='Protocolo'>{safe_str(r.get('Protocolo',''))}</td>"
            f"<td data-label='Matrícula'>{safe_str(r.get('Matrícula Log',''))}</td>"
            f"<td data-label='Motivo Falha'>{safe_str(r.get('Motivo Falha Log',''))}</td>"
            f"<td data-label='Etapa'>{safe_str(r.get('Etapa Log',''))}</td>"
            f"<td data-label='UserRemove'>{safe_str(r.get('UserRemove',''))}</td>"
            f"<td data-label='Motivo'>{safe_str(r.get('Motivo',''))}</td>"
            f"<td data-label='Status' style='{status_cls}'>{status}</td>"
            "</tr>"
        )

    body = (
        resumo_html
        + "<div class='card'>"
        "<h3 style='margin:0 0 6px;'>Falhas removidas (log SharePoint)</h3>"
        f"<div class='muted' style='font-size:12px;'>Escopo: {safe_str(scope_name) or 'Geral'} • "
        f"Período remoção: {cur_start.strftime('%d/%m/%Y')} → {cur_end.strftime('%d/%m/%Y')}</div>"
        "</div>"
        "<div class='card'>"
        "<table class='table-stack' role='presentation' cellspacing='0' cellpadding='0' style='width:100%;'>"
        "<thead><tr>"
        "<th>ID</th><th>Data remoção</th><th>Type</th><th>Protocolo</th><th>Matrícula</th>"
        "<th>Motivo falha</th><th>Etapa</th><th>Usuário</th><th>Motivo log</th><th>Status</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
    )
    title = "Falhas removidas"
    if safe_str(scope_name):
        title += f" - {safe_str(scope_name)}"
    return wrap_simple_page(title, body)


