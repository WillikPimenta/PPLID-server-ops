# -*- coding: utf-8 -*-
"""Inferência de Segmento vazio a partir de Novo cenário / Cenário."""
from __future__ import annotations

import pandas as pd

from report_brb.brb_normalize import is_empty_value, normalize_text, strip_accents

SEG_RISCO = "RISCO"
SEG_NAO_PASSAVEL = "NÃO PASSÁVEL DE ANÁLISE"


def _norm_key(value) -> str:
    s = normalize_text(value, upper=True)
    if not s:
        return ""
    return " ".join(strip_accents(s).upper().split())


def _cenario_key(row: pd.Series) -> str:
    novo = _norm_key(row.get("Novo cenário"))
    if novo:
        return novo
    return _norm_key(row.get("Cenário"))


def _fuzzy_segmento(text_norm: str) -> str | None:
    if not text_norm or text_norm in ("-",):
        return None
    if "ILEG" in text_norm:
        return SEG_NAO_PASSAVEL
    if "INCOMPLET" in text_norm or "DETERIOR" in text_norm:
        return SEG_NAO_PASSAVEL
    if any(k in text_norm for k in ("FORMAT", "ADULTER", "SOBREPOS", "RASURA", "FRAUDADOR")):
        return SEG_RISCO
    return None


def build_segmento_lookup(fg: pd.DataFrame) -> dict[str, str]:
    """Mapa Novo cenário/Cenário → Segmento majoritário (só linhas já preenchidas)."""
    if fg.empty or "Segmento" not in fg.columns:
        return {}
    filled = fg[~fg["Segmento"].map(is_empty_value)].copy()
    if filled.empty:
        return {}
    filled["_key"] = filled.apply(_cenario_key, axis=1)
    filled = filled[filled["_key"].ne("")]
    if filled.empty:
        return {}
    lookup: dict[str, str] = {}
    for key, grp in filled.groupby("_key", sort=False):
        vc = grp["Segmento"].map(lambda x: normalize_text(x)).value_counts()
        if len(vc):
            lookup[str(key)] = str(vc.index[0])
    return lookup


def infer_segmento_value(row: pd.Series, lookup: dict[str, str]) -> tuple[str | None, str]:
    """Retorna (segmento, origem) — origem: exact | fuzzy | None."""
    if not is_empty_value(row.get("Segmento")):
        return normalize_text(row.get("Segmento")), "oficial"
    key = _cenario_key(row)
    if key and key in lookup:
        return lookup[key], "exact"
    fuzzy = _fuzzy_segmento(key)
    if fuzzy:
        return fuzzy, "fuzzy"
    # tenta só Cenário se Novo estava vazio no key
    cen = _norm_key(row.get("Cenário"))
    if cen and cen in lookup:
        return lookup[cen], "exact"
    fuzzy2 = _fuzzy_segmento(cen)
    if fuzzy2:
        return fuzzy2, "fuzzy"
    return None, "vazio"


def fill_segmento_vazio(fg: pd.DataFrame, *, inplace_oficial: bool = True) -> pd.DataFrame:
    """Preenche Segmento vazio com inferência.

    Se inplace_oficial=True, grava no próprio Segmento (e marca Segmento_origem).
    Sempre adiciona Segmento_sugerido / Segmento_origem.
    """
    if fg.empty:
        return fg
    out = fg.copy()
    if "Segmento" not in out.columns:
        out["Segmento"] = ""
    lookup = build_segmento_lookup(out)
    sugeridos: list[str] = []
    origens: list[str] = []
    for _, row in out.iterrows():
        seg, origem = infer_segmento_value(row, lookup)
        sugeridos.append(seg or "")
        origens.append(origem)
    out["Segmento_sugerido"] = sugeridos
    out["Segmento_origem"] = origens
    if inplace_oficial:
        mask = out["Segmento"].map(is_empty_value) & out["Segmento_sugerido"].ne("")
        out.loc[mask, "Segmento"] = out.loc[mask, "Segmento_sugerido"]
        out.loc[mask, "Segmento_origem"] = out.loc[mask, "Segmento_origem"].map(
            lambda o: f"inferido_{o}" if o in ("exact", "fuzzy") else o
        )
    return out
