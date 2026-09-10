# -*- coding: utf-8 -*-
"""Enriquece NA_Falhas: DEMANDA vazia e DATA DE NOTIFICAÇÃO via NA_Demandas."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from report_brb.brb_filters import is_brb, parse_excel_date, safe_str
from report_brb.config_brb import EXCEL_PATH
from report_brb.na_falhas_normalize import read_na_falhas_excel, resolve_na_falhas_sheet_name


def _find_col(df: pd.DataFrame, *needles: str) -> str | None:
    for col in df.columns:
        up = safe_str(col).upper()
        if all(n.upper() in up for n in needles):
            return col
    return None


def _is_empty_demanda(val) -> bool:
    s = safe_str(val)
    return not s or s.lower() in ("nan", "none")


@dataclass
class EnrichStats:
    demanda_ja_preenchida: int = 0
    notif_por_demanda: int = 0
    demanda_match_exato: int = 0
    demanda_inferida: int = 0
    sem_match: int = 0
    total_brb: int = 0


def _build_demanda_lookup(dem: pd.DataFrame) -> tuple[dict, dict, pd.DataFrame]:
    """Retorna mapa demanda→abertura, mapa dia→lista demandas, tabela BRB demandas."""
    dem_col = _find_col(dem, "Demanda") or "Demanda"
    abert_col = _find_col(dem, "Data", "Abertura") or "Data da Abertura"
    cliente_col = _find_col(dem, "Cliente") or "Cliente"

    sub = dem[dem[cliente_col].map(is_brb)].copy()
    sub["_abert"] = parse_excel_date(sub[abert_col])
    sub["_abert_d"] = pd.to_datetime(sub["_abert"]).dt.normalize()
    sub["_demanda"] = sub[dem_col].map(safe_str)

    by_demanda: dict[str, pd.Timestamp] = {}
    for _, row in sub.iterrows():
        d = row["_demanda"]
        if d and pd.notna(row["_abert"]):
            by_demanda[d.upper()] = pd.Timestamp(row["_abert"])

    by_day: dict[pd.Timestamp, list[tuple[str, pd.Timestamp]]] = {}
    for _, row in sub.iterrows():
        if pd.isna(row["_abert_d"]) or not row["_demanda"]:
            continue
        day = row["_abert_d"]
        by_day.setdefault(day, []).append((row["_demanda"], pd.Timestamp(row["_abert"])))

    return by_demanda, by_day, sub


def _nearest_demanda(
    cadastro: pd.Timestamp,
    candidates: list[tuple[str, pd.Timestamp]],
) -> tuple[str, pd.Timestamp] | None:
    if not candidates or pd.isna(cadastro):
        return None
    cad = pd.Timestamp(cadastro)
    best = min(candidates, key=lambda x: abs((x[1] - cad).total_seconds()))
    return best


def enrich_na_falhas_sheet(
    na: pd.DataFrame,
    dem: pd.DataFrame,
) -> tuple[pd.DataFrame, EnrichStats]:
    stats = EnrichStats()
    out = na.copy()

    cliente_col = _find_col(out, "CLIENTE") or "CLIENTE"
    demanda_col = _find_col(out, "DEMANDA") or "DEMANDA"
    cad_col = _find_col(out, "DATA", "CADASTRO") or "DATA DE CADASTRO"
    notif_col = _find_col(out, "DATA", "NOTIFICA") or "DATA DE NOTIFICAÇÃO"

    if "demanda_inferida" not in out.columns:
        out["demanda_inferida"] = False

    by_demanda, by_day, dem_brb = _build_demanda_lookup(dem)
    brb_idx = out.index[out[cliente_col].map(is_brb)]
    stats.total_brb = len(brb_idx)

    out["_cad"] = pd.NaT
    out.loc[brb_idx, "_cad"] = parse_excel_date(out.loc[brb_idx, cad_col])
    out["_cad_d"] = pd.to_datetime(out["_cad"]).dt.normalize()

    for idx in brb_idx:
        demanda_val = safe_str(out.at[idx, demanda_col])
        cad = out.at[idx, "_cad"]
        cad_d = out.at[idx, "_cad_d"]

        if not _is_empty_demanda(demanda_val):
            stats.demanda_ja_preenchida += 1
            key = demanda_val.upper()
            if key in by_demanda:
                out.at[idx, notif_col] = by_demanda[key]
                stats.notif_por_demanda += 1
            continue

        if pd.isna(cad_d):
            stats.sem_match += 1
            continue

        if cad_d in by_day:
            cands = by_day[cad_d]
            if len(cands) == 1:
                dem_name, abert = cands[0]
                out.at[idx, demanda_col] = dem_name
                out.at[idx, notif_col] = abert
                out.at[idx, "demanda_inferida"] = False
                stats.demanda_match_exato += 1
                continue

        month_cands: list[tuple[str, pd.Timestamp]] = []
        if pd.notna(cad):
            ym = pd.Timestamp(cad).to_period("M")
            for _, row in dem_brb.iterrows():
                if pd.notna(row["_abert"]) and pd.Timestamp(row["_abert"]).to_period("M") == ym:
                    month_cands.append((row["_demanda"], pd.Timestamp(row["_abert"])))

        picked = _nearest_demanda(cad, month_cands) if month_cands else None
        if picked:
            dem_name, abert = picked
            out.at[idx, demanda_col] = dem_name
            out.at[idx, notif_col] = abert
            out.at[idx, "demanda_inferida"] = True
            stats.demanda_inferida += 1
        else:
            stats.sem_match += 1

    out = out.drop(columns=[c for c in ("_cad", "_cad_d") if c in out.columns])
    return out, stats


def enrich_workbook(
    path: Path | None = None,
    backup: bool = True,
) -> tuple[Path, EnrichStats]:
    path = Path(path or EXCEL_PATH)
    if backup:
        bak = path.with_suffix(
            f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        )
        shutil.copy2(path, bak)

    xl = pd.ExcelFile(path)
    sheets = {name: pd.read_excel(path, name) for name in xl.sheet_names}

    na_sheet = resolve_na_falhas_sheet_name(xl.sheet_names)
    dem_sheet = "NA_Demandas"
    if not na_sheet or dem_sheet not in sheets:
        raise ValueError(f"Abas NA_Falhas/Falhas e/ou {dem_sheet} ausentes em {path.name}")

    enriched, stats = enrich_na_falhas_sheet(read_na_falhas_excel(path, sheet_name=na_sheet), sheets[dem_sheet])
    sheets[na_sheet] = enriched

    tmp = path.with_suffix(".enrich_tmp.xlsx")
    try:
        with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
            for name, df in sheets.items():
                df.to_excel(writer, sheet_name=name, index=False)
        tmp.replace(path)
    except PermissionError as exc:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise PermissionError(
            f"Não foi possível gravar {path.name}. Feche o arquivo no Excel e tente novamente."
        ) from exc
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise

    return path, stats
