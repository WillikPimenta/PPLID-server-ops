# -*- coding: utf-8 -*-
"""Helpers de sync compartilhados (HC name→matrícula)."""
import re

import pandas as pd

from report_falhas.io.data_loader import normalize_text, norm_matricula, safe_str, safe_to_datetime
from report_falhas.training_utils import training_prefix, training_situacao_calculada

_MATRICULA_RE = re.compile(r"^[a-z]?\d{3,}[a-z]?$")

__all__ = [
    "build_name2mat_from_hc",
    "looks_like_matricula",
    "resolve_agent_from_suporte_row",
    "training_prefix",
    "training_situacao_calculada",
]


def looks_like_matricula(s: str) -> bool:
    if not s:
        return False
    return bool(_MATRICULA_RE.fullmatch(str(s).strip().lower()))


def build_name2mat_from_hc(dfh: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    if dfh is None or dfh.empty:
        return out

    def _hc_col(df, expected):
        exp = normalize_text(expected)
        for c in df.columns:
            cc = str(c).replace("\r", "").replace("\n", "").strip()
            if normalize_text(cc) == exp:
                return c
        return None

    col_nome = _hc_col(dfh, "nome_agente")
    col_mat = _hc_col(dfh, "matricula_agente")
    col_data = _hc_col(dfh, "data_inicial")
    col_id = _hc_col(dfh, "id")
    if not col_nome or not col_mat:
        return out

    dfx = dfh.copy()
    if col_data and (not pd.api.types.is_datetime64_any_dtype(dfx[col_data])):
        dfx[col_data] = safe_to_datetime(dfx[col_data])
    dfx["_id_num"] = pd.to_numeric(dfx[col_id], errors="coerce").fillna(-1) if col_id else -1
    dfx["_k_name"] = dfx[col_nome].apply(
        lambda x: normalize_text(str(x).replace("\r", " ").replace("\n", " ").strip())
    )
    dfx["_mat"] = dfx[col_mat].apply(safe_str)
    dfx = dfx[(dfx["_k_name"] != "") & (dfx["_mat"] != "")].copy()
    if dfx.empty:
        return out

    sort_cols = ["_k_name"] + ([col_data] if col_data else []) + ["_id_num"]
    dfx = dfx.sort_values(sort_cols, ascending=True)
    last = dfx.groupby("_k_name")["_mat"].last()
    for k, v in last.items():
        m = norm_matricula(v)
        if m:
            out[str(k)] = m
    return out


def resolve_agent_from_suporte_row(row, name2mat: dict, db_agents: dict):
    """Resolve agente do suporte: Agente (nome ou matrícula) ou colunas TBSO de matrícula."""
    nome_raw = safe_str(row.get("Agente", ""))
    key = normalize_text(nome_raw)
    m_norm = name2mat.get(key)
    if not m_norm:
        candidate = norm_matricula(nome_raw)
        m_norm = candidate if looks_like_matricula(candidate) else ""
    if not m_norm:
        for col in (
            "TBSO_MATRICULA_OPERACAO",
            "TBSO_MATRICULA_SOLICITANTE",
            "matricula_operacao",
            "matricula_solicitante",
        ):
            candidate = norm_matricula(row.get(col, ""))
            if looks_like_matricula(candidate):
                m_norm = candidate
                if not nome_raw:
                    nome_raw = safe_str(row.get(col, ""))
                break
    agent_obj = db_agents.get(m_norm) if m_norm else None
    return agent_obj, nome_raw, m_norm
