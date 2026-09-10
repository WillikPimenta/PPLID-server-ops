# -*- coding: utf-8 -*-
"""Mapas HC (atividade, turno) e classificação FN/FP."""
from __future__ import annotations

import pandas as pd

from report_falhas.io.data_loader import norm_matricula, normalize_text, safe_str, safe_to_datetime
from report_falhas.team_category import map_team_to_categoria

CLS_FN = 'FN'
CLS_FP = 'FP'
CLS_OTHER = 'OUTROS'


def build_atividade_atual_hc_map(dfh: pd.DataFrame) -> dict[str, str]:
    """Retorna a atividade mais recente do HC por matrícula (mat_norm)."""
    out: dict[str, str] = {}
    if dfh is None or dfh.empty:
        return out

    dfx = dfh.copy()
    if 'mat_norm' not in dfx.columns and 'matricula_agente' in dfx.columns:
        dfx['mat_norm'] = dfx['matricula_agente'].apply(norm_matricula)

    if 'data_inicial' in dfx.columns and (not pd.api.types.is_datetime64_any_dtype(dfx['data_inicial'])):
        dfx['data_inicial'] = safe_to_datetime(dfx['data_inicial'])

    if 'id' in dfx.columns:
        dfx['_id_num'] = pd.to_numeric(dfx['id'], errors='coerce').fillna(-1)
    else:
        dfx['_id_num'] = -1

    dfx = dfx.dropna(subset=['mat_norm']).copy()
    if dfx.empty:
        return out

    sort_cols = ['mat_norm'] + (['data_inicial'] if 'data_inicial' in dfx.columns else []) + ['_id_num']
    dfx = dfx.sort_values(sort_cols, ascending=True)

    if 'atividade' in dfx.columns:
        last = dfx.groupby('mat_norm')['atividade'].last()
        out = {str(k): safe_str(v) for k, v in last.items()}

    return out


def build_turno_hc_map(dfh: pd.DataFrame) -> dict[str, str]:
    """Retorna o turno mais recente do HC por matrícula (mat_norm)."""
    out: dict[str, str] = {}
    if dfh is None or dfh.empty:
        return out
    dfx = dfh.copy()
    if 'mat_norm' not in dfx.columns and 'matricula_agente' in dfx.columns:
        dfx['mat_norm'] = dfx['matricula_agente'].apply(norm_matricula)
    if 'turno' not in dfx.columns:
        return out
    dfx['turno'] = dfx['turno'].apply(safe_str)
    dfx = dfx[dfx['turno'] != ''].copy()
    if dfx.empty:
        return out
    if 'data_inicial' in dfx.columns and (not pd.api.types.is_datetime64_any_dtype(dfx['data_inicial'])):
        dfx['data_inicial'] = safe_to_datetime(dfx['data_inicial'])
    if 'id' in dfx.columns:
        dfx['_id_num'] = pd.to_numeric(dfx['id'], errors='coerce').fillna(-1)
    else:
        dfx['_id_num'] = -1
    dfx = dfx.dropna(subset=['mat_norm']).copy()
    if dfx.empty:
        return out
    sort_cols = ['mat_norm'] + (['data_inicial'] if 'data_inicial' in dfx.columns else []) + ['_id_num']
    dfx = dfx.sort_values(sort_cols, ascending=True)
    last = dfx.groupby('mat_norm')['turno'].last()
    return {str(k): safe_str(v) for k, v in last.items()}


def _latest_hc_field_map(dfh: pd.DataFrame, field: str) -> dict[str, str]:
    """Último valor de `field` por mat_norm (sort data_inicial + id)."""
    out: dict[str, str] = {}
    if dfh is None or dfh.empty or field not in dfh.columns:
        return out
    dfx = dfh.copy()
    if 'mat_norm' not in dfx.columns and 'matricula_agente' in dfx.columns:
        dfx['mat_norm'] = dfx['matricula_agente'].apply(norm_matricula)
    if 'data_inicial' in dfx.columns and (not pd.api.types.is_datetime64_any_dtype(dfx['data_inicial'])):
        dfx['data_inicial'] = safe_to_datetime(dfx['data_inicial'])
    if 'id' in dfx.columns:
        dfx['_id_num'] = pd.to_numeric(dfx['id'], errors='coerce').fillna(-1)
    else:
        dfx['_id_num'] = -1
    dfx = dfx.dropna(subset=['mat_norm']).copy()
    if dfx.empty:
        return out
    sort_cols = ['mat_norm'] + (['data_inicial'] if 'data_inicial' in dfx.columns else []) + ['_id_num']
    dfx = dfx.sort_values(sort_cols, ascending=True)
    last = dfx.groupby('mat_norm')[field].last()
    return {str(k): safe_str(v) for k, v in last.items()}


def build_team_hc_map(dfh: pd.DataFrame) -> dict[str, str]:
    """Retorna o Team mais recente do HC por matrícula (mat_norm)."""
    return _latest_hc_field_map(dfh, 'team')


def build_team_categoria_map(dfh: pd.DataFrame) -> dict[str, str]:
    """Retorna Team/Category por matrícula (mat_norm)."""
    team_map = build_team_hc_map(dfh)
    return {mat: map_team_to_categoria(team) for mat, team in team_map.items()}


def classify_fn_fp_from_novo_cenario(novo_cenario: str) -> str:
    raw = safe_str(novo_cenario)
    norm = normalize_text(raw)
    if norm.startswith('nao sinalizado'):
        return CLS_FN
    if norm.startswith('sinalizacao incorreta'):
        return CLS_FP
    return CLS_OTHER
