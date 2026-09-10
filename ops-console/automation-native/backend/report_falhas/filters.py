# -*- coding: utf-8 -*-
"""Filtros compartilhados: localidades, métrica oficial."""
from __future__ import annotations

import re
from datetime import date

import pandas as pd

from report_falhas.io.data_loader import normalize_text, safe_str

COL_MODULO = 'Módulo'
COL_LOCALIDADE = 'Localidade'

MODULOS_METRICA_OFICIAL = [
    'Demais falhas',
    'G AUDITORIA',
    'AUDITORIA COMPLEMENTAR',
    'ATAQUE DE FRAUDE',
    'BLACK LIST',
]

# A partir deste mês (inclusive) todos os módulos entram na métrica; sem aba Total.
METRICA_UNIFICADA_CUTOVER = date(2026, 7, 1)

LOCALIDADES_ALVO = {
    'Brasília': {'brasília', 'brasilia', 'bsb'},
    'São Carlos': {'são carlos', 'sao carlos', 'sanca', 'São Carlos'},
}


def uses_dual_metric_mode(cur_start: date) -> bool:
    """True enquanto o report mantém Total (Geral) + filtro de Módulo (até jun/2026)."""
    if cur_start is None:
        return True
    return cur_start.replace(day=1) < METRICA_UNIFICADA_CUTOVER


def filtrar_por_localidades(df: pd.DataFrame, aliases_all: set) -> pd.DataFrame:
    if df is None or df.empty:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()
    if COL_LOCALIDADE not in df.columns:
        return df.copy()
    d = df.copy()
    d['localidade_norm'] = d[COL_LOCALIDADE].apply(normalize_text)
    pattern = '|'.join(re.escape(normalize_text(a)) for a in aliases_all)
    return d[d['localidade_norm'].str.contains(pattern, na=False, regex=True)].copy()


def filtrar_metrica_oficial(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return df.iloc[0:0].copy()
    if COL_MODULO not in df.columns:
        return df.iloc[0:0].copy()
    allowed = {safe_str(x) for x in MODULOS_METRICA_OFICIAL}
    mod = df[COL_MODULO].apply(safe_str)
    return df.loc[mod.isin(allowed)].copy()


def aplicar_recorte_oficial(df: pd.DataFrame, cur_start: date) -> pd.DataFrame:
    """Recorte de métrica oficial: filtro de módulo até jun/2026; todos os módulos a partir de jul/2026."""
    if df is None or df.empty:
        return df.iloc[0:0].copy() if df is not None else pd.DataFrame()
    if uses_dual_metric_mode(cur_start):
        return filtrar_metrica_oficial(df)
    return df.copy()
