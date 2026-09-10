# -*- coding: utf-8 -*-
"""Conferência rápida: falhas jun/2026 por BU (Data de Análise)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import report_falhas.config_report as cfg
from report_falhas.data_base import read_base
from report_falhas.filters import LOCALIDADES_ALVO, filtrar_metrica_oficial, filtrar_por_localidades
from report_falhas.periods import filter_by_date_range, get_comparativo_by_same_period
from report_falhas.kpis import make_kpis
from report_falhas.reincidence import novos_no_mes, reincidencia_table_full

from report_falhas.config_report import canonical_falhas_excel_path

EXCEL = canonical_falhas_excel_path()

CUR_START = date(2026, 6, 1)
CUR_END = date(2026, 6, 30)
COL = cfg.COL_DATA_ANALISE


def counts_for_excel(path: Path) -> dict[str, dict[str, int]]:
    df_base = read_base(str(path), cfg.ABA_BASE)
    out: dict[str, dict[str, int]] = {}
    for loc, aliases in LOCALIDADES_ALVO.items():
        df_loc = filtrar_por_localidades(df_base, set(aliases))
        df_mtd = filter_by_date_range(df_loc, CUR_START, CUR_END, COL)
        df_of = filtrar_metrica_oficial(df_mtd)
        out[loc] = {"total": len(df_mtd), "oficial": len(df_of)}
    aliases_all = set().union(*LOCALIDADES_ALVO.values())
    df_all = filtrar_por_localidades(df_base, aliases_all)
    df_mtd = filter_by_date_range(df_all, CUR_START, CUR_END, COL)
    df_of = filtrar_metrica_oficial(df_mtd)
    out["Consolidado"] = {"total": len(df_mtd), "oficial": len(df_of)}
    out["_base_rows"] = {"total": len(df_base), "oficial": 0}
    df_jun = filter_by_date_range(df_base, CUR_START, CUR_END, COL)
    in_map = filtrar_por_localidades(df_jun, aliases_all)
    out["_fora_bu"] = {"total": len(df_jun) - len(in_map), "oficial": 0}
    return out


def main() -> int:
    path = EXCEL
    if not path.exists():
        print("FAIL: Excel master não encontrado:", path)
        return 1

    c = counts_for_excel(path)
    bsb = c["Brasília"]["total"]
    sc = c["São Carlos"]["total"]
    cons = c["Consolidado"]["total"]

    print(f"Excel: {path}")
    print(f"Período: Data de Análise {CUR_START:%d/%m/%Y} a {CUR_END:%d/%m/%Y}")
    print(f"Linhas na Base (após read_base): {c['_base_rows']['total']}")
    print()
    print("=== Falhas por BU ===")
    for name in ("Brasília", "São Carlos", "Consolidado"):
        print(f"  {name:12}  total={c[name]['total']:4}  oficial={c[name]['oficial']:4}")
    print()
    print(f"Brasília + São Carlos = {bsb + sc}")
    print(f"Consolidado           = {cons}")
    ok = bsb + sc == cons
    print(f"Soma BUs bate consolidado? {'SIM' if ok else 'NAO'}")
    if c["_fora_bu"]["total"]:
        print(f"Falhas jun/2026 fora BSB/SC: {c['_fora_bu']['total']}")

    # Conferir KPI do pipeline (mesma logica do report) para jun/2026 cheio
    df_base = read_base(str(path), cfg.ABA_BASE)
    ref = date(2026, 6, 30)
    prev_start = date(2026, 5, 1)
    print()
    print("=== KPI pipeline (fail_mtd_atual) ===")
    for loc, aliases in LOCALIDADES_ALVO.items():
        df_loc = filtrar_por_localidades(df_base, set(aliases))
        total_atual, total_prev, p_ini, p_fim = get_comparativo_by_same_period(
            df_loc, CUR_START, CUR_END, prev_start, COL
        )
        df_cur = filter_by_date_range(df_loc, CUR_START, CUR_END, COL)
        df_prev = filter_by_date_range(df_loc, p_ini, p_fim, COL) if p_ini and p_fim else df_loc.iloc[0:0]
        kpis = make_kpis(
            CUR_START, CUR_END, total_atual, total_prev, p_ini, p_fim,
            reincidencia_table_full(df_prev, df_cur), [], novos_no_mes(df_prev, df_cur),
        )
        print(f"  {loc:12}  fail_mtd_atual={kpis['fail_mtd_atual']}  (comparativo maio: {total_prev})")

    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
