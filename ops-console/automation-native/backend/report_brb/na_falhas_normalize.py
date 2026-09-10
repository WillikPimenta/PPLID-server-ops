# -*- coding: utf-8 -*-
"""Normalização da aba NA_Falhas / Falhas (SharePoint — Power Query)."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pandas as pd

from report_brb.brb_filters import safe_str

NA_FALHAS_SHEET_ALIASES = ("NA_Falhas", "Falhas")

# Cabeçalhos canônicos do suplemento (``supplement_stub.NA_Falhas``).
NA_FALHAS_CANONICAL_COLUMNS = (
    "CLIENTE",
    "PROTOCOLO",
    "MATRÍCULA",
    "DATA DE CADASTRO",
    "DATA DE NOTIFICAÇÃO",
    "MOTIVO DA FALHA",
    "RESULTADO DO CLIENTE",
    "RESULTADO DA AUDITORIA",
    "DEMANDA",
)

_JUNK_COL_RE = re.compile(r"^Column\d+$", re.I)

# Aliases extras além do nome canônico (comparação accent/case-insensitive).
_NA_FALHAS_ALIASES: dict[str, tuple[str, ...]] = {
    "CLIENTE": ("CLIENTE",),
    "PROTOCOLO": ("PROTOCOLO",),
    "MATRÍCULA": ("MATRICULA", "MATRÍCULA"),
    "DATA DE CADASTRO": ("DATA DE CADASTRO", "DATA CADASTRO"),
    "DATA DE NOTIFICAÇÃO": (
        "DATA DE NOTIFICACAO",
        "DATA DE NOTIFICAÇÃO",
        "DATA NOTIFICACAO",
        "DATA NOTIFICAÇÃO",
    ),
    "MOTIVO DA FALHA": ("MOTIVO DA FALHA", "MOTIVO FALHA"),
    "RESULTADO DO CLIENTE": ("RESULTADO DO CLIENTE", "RESULTADO CLIENTE"),
    "RESULTADO DA AUDITORIA": ("RESULTADO DA AUDITORIA", "RESULTADO AUDITORIA"),
    "DEMANDA": ("DEMANDA",),
}


def _norm_col_key(name: str) -> str:
    s = safe_str(name)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().upper()


def _canonical_na_falhas_col(name: str) -> str | None:
    key = _norm_col_key(name)
    if not key:
        return None
    for canonical, aliases in _NA_FALHAS_ALIASES.items():
        if key == _norm_col_key(canonical):
            return canonical
        for alias in aliases:
            if key == _norm_col_key(alias):
                return canonical
    return None


def drop_junk_excel_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Remove colunas vazias geradas pelo Excel (Column19, Column20, …)."""
    junk = [c for c in df.columns if _JUNK_COL_RE.match(safe_str(c))]
    if not junk:
        return df
    return df.drop(columns=junk, errors="ignore")


def _is_empty_cell(val) -> bool:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return True
    s = safe_str(val)
    return not s or s.lower() in ("nan", "none", "null")


def drop_blank_na_falhas_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Remove linhas totalmente vazias (equivalente ao PQ List.RemoveMatchingItems)."""
    if df.empty:
        return df
    mask = df.apply(lambda row: any(not _is_empty_cell(v) for v in row), axis=1)
    return df.loc[mask].copy()


def normalize_na_falhas_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Mapeia cabeçalhos SharePoint (ex. ``Data de Cadastro``) para o schema canônico."""
    if df.empty:
        return df
    rename: dict[str, str] = {}
    used: set[str] = set()
    for col in df.columns:
        canonical = _canonical_na_falhas_col(safe_str(col))
        if canonical and canonical not in used:
            rename[col] = canonical
            used.add(canonical)
    out = df.rename(columns=rename)
    # Descarta colunas desconhecidas — mantém só o schema NA_Falhas (+ extras já renomeadas).
    keep = [c for c in out.columns if c in NA_FALHAS_CANONICAL_COLUMNS]
    if keep:
        out = out[keep].copy()
    return out


def normalize_na_falhas_raw(df: pd.DataFrame) -> pd.DataFrame:
    """Pipeline SharePoint: remove junk, normaliza cabeçalhos, remove linhas em branco."""
    if df is None or df.empty:
        return pd.DataFrame()
    out = drop_junk_excel_columns(df)
    out = normalize_na_falhas_columns(out)
    out = drop_blank_na_falhas_rows(out)
    return out.reset_index(drop=True)


def resolve_na_falhas_sheet_name(sheet_names: list[str] | set[str]) -> str | None:
    names = set(sheet_names)
    for alias in NA_FALHAS_SHEET_ALIASES:
        if alias in names:
            return alias
    return None


def supplement_na_falhas_satisfied(sheet_names: list[str] | set[str]) -> bool:
    return resolve_na_falhas_sheet_name(sheet_names) is not None


def read_na_falhas_excel(path: Path, *, sheet_name: str | None = None) -> pd.DataFrame:
    """Lê aba ``NA_Falhas`` ou ``Falhas`` e aplica normalização SharePoint."""
    path = Path(path)
    xl = pd.ExcelFile(path)
    name = sheet_name or resolve_na_falhas_sheet_name(xl.sheet_names)
    if not name:
        return pd.DataFrame()
    df = pd.read_excel(path, name)
    return normalize_na_falhas_raw(df)
