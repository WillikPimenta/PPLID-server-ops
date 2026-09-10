# report_falhas/reincidence.py
# -*- coding: utf-8 -*-
"""Reincidência — implementação alinhada ao fluxo principal (report_falhas_criticas)."""

from __future__ import annotations

import pandas as pd

from report_falhas.io.data_loader import norm_protocolo, safe_str
import report_falhas.config_report as cfg_report
from report_falhas.matricula_utils import clean_matricula_unified, format_cenario_text


def _col_matricula() -> str:
    return cfg_report.COL_MATRICULA


def _col_cenario() -> str:
    return cfg_report.COL_CENARIO


def _col_protocolo() -> str:
    return cfg_report.COL_PROTOCOLO


def _join_distinct_cenario(series: pd.Series, max_items: int = 100) -> str:
    vals = [format_cenario_text(x) for x in series.dropna().astype(str).unique().tolist()]
    vals = [v for v in vals if safe_str(v)]
    if not vals:
        return ""
    if len(vals) > max_items:
        vals = vals[:max_items] + ["..."]
    return ", ".join(vals)


def _join_distinct_protocolo(series: pd.Series, max_items: int = 100) -> str:
    vals = [norm_protocolo(x) for x in series.dropna().unique().tolist()]
    vals = [v for v in vals if safe_str(v)]
    if not vals:
        return ""
    if len(vals) > max_items:
        vals = vals[:max_items] + ["..."]
    return ", ".join(vals)


def reincidencia_table_full(df_prev: pd.DataFrame, df_cur: pd.DataFrame) -> pd.DataFrame:
    col_mat = _col_matricula()
    col_cen = _col_cenario()
    col_prot = _col_protocolo()

    if df_cur.empty:
        return pd.DataFrame(
            columns=[
                "Matrícula Agente",
                "Reincidente",
                "Ocorrências Mês Atual",
                "Cenário (Mês Atual)",
                "Protocolos (Mês Atual)",
            ]
        )

    def _clean_matricula_reinc(raw: str) -> str:
        mat, _ = clean_matricula_unified(raw)
        return mat

    df_cur = df_cur.copy()
    df_prev = df_prev.copy() if df_prev is not None else pd.DataFrame()
    df_cur["_mat_clean"] = df_cur[col_mat].apply(_clean_matricula_reinc)
    df_prev["_mat_clean"] = (
        df_prev[col_mat].apply(_clean_matricula_reinc)
        if not df_prev.empty and col_mat in df_prev.columns
        else pd.Series(dtype=str)
    )

    cur_counts = df_cur["_mat_clean"].value_counts()
    cur_agents = list(cur_counts.index)
    prev_agents = (
        set(df_prev["_mat_clean"].unique())
        if not df_prev.empty and "_mat_clean" in df_prev.columns
        else set()
    )
    has_cenario = col_cen in df_cur.columns
    has_protocolo = col_prot in df_cur.columns
    rows = []
    for ag in cur_agents:
        if not safe_str(ag):
            continue
        is_reinc = ag in prev_agents
        df_ag = df_cur[df_cur["_mat_clean"] == ag]
        cenarios = _join_distinct_cenario(df_ag[col_cen]) if has_cenario else ""
        protocolos = _join_distinct_protocolo(df_ag[col_prot]) if has_protocolo else ""
        rows.append(
            {
                "Matrícula Agente": ag,
                "Reincidente": "Sim" if is_reinc else "Não",
                "Ocorrências Mês Atual": int(cur_counts.get(ag, 0)),
                "Cenário (Mês Atual)": cenarios,
                "Protocolos (Mês Atual)": protocolos,
            }
        )
    out = pd.DataFrame(
        rows,
        columns=[
            "Matrícula Agente",
            "Reincidente",
            "Ocorrências Mês Atual",
            "Cenário (Mês Atual)",
            "Protocolos (Mês Atual)",
        ],
    )
    if not out.empty:
        out["_ord_reinc"] = out["Reincidente"].map({"Sim": 1, "Não": 0})
        out = out.sort_values(
            by=["_ord_reinc", "Ocorrências Mês Atual", "Matrícula Agente"],
            ascending=[False, False, True],
        ).drop(columns="_ord_reinc")
    return out


def novos_no_mes(df_prev: pd.DataFrame, df_cur: pd.DataFrame) -> list:
    col_mat = _col_matricula()
    prev_set = set(df_prev[col_mat].unique()) if not df_prev.empty else set()
    cur_counts = df_cur[col_mat].value_counts() if not df_cur.empty else pd.Series(dtype=int)
    pares = [(mat, int(cur_counts[mat])) for mat in cur_counts.index if mat not in prev_set]
    return sorted(pares, key=lambda x: (-x[1], x[0]))
