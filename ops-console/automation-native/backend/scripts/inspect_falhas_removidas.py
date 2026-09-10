# -*- coding: utf-8 -*-
"""Diagnóstico da aba Falhas removidas vs Base da planilha."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from report_falhas.config_report import ABA_BASE, require_excel_path
from report_falhas.data_base import read_base
from report_falhas.falhas_removidas import (
    _read_falhas_removidas,
    classify_removal_type,
    get_removidas_state,
    match_base_rows,
)


def _load_raw_base(path: str) -> pd.DataFrame:
    """Base sem filtros de contestação/removidas (somente leitura bruta)."""
    from report_falhas.io.data_loader import norm_matricula, norm_protocolo, safe_str

    df = pd.read_excel(path, sheet_name=ABA_BASE, engine="openpyxl")
    if "Protocolo" in df.columns:
        df["Protocolo"] = df["Protocolo"].apply(norm_protocolo)
    if "Matrícula Agente" in df.columns:
        df["_mat_norm"] = df["Matrícula Agente"].apply(norm_matricula)
    return df


def _simulate_matches(path: str) -> pd.DataFrame:
    raw_base = _load_raw_base(path)
    df_rem = _read_falhas_removidas(path)
    if df_rem.empty:
        return pd.DataFrame()

    rows = []
    prot_col = "Protocolo"
    for _, r in df_rem.iterrows():
        kind = classify_removal_type(r.get("Type", ""))
        fp = {
            "protocolo": r.get("__PROTOCOLO__", ""),
            "matricula_norm": r.get("__MATRICULA_NORM__", ""),
            "motivo_norm": r.get("__MOTIVO_NORM__", ""),
            "etapa_norm": r.get("__ETAPA_NORM__", ""),
        }
        if kind == "unknown" or not r.get("__APLICAVEL__", False):
            status = "ignorado"
            n_matches = 0
        else:
            matches, status = match_base_rows(raw_base, prot_col, fp)
            n_matches = len(matches)
        rows.append({
            "ID": r.get("ID", ""),
            "Type": r.get("Type", ""),
            "Kind": kind,
            "Protocolo": r.get("__PROTOCOLO__", ""),
            "Matricula": r.get("__MATRICULA__", ""),
            "Motivo": r.get("__MOTIVO__", ""),
            "Etapa": r.get("__ETAPA__", ""),
            "Status": status,
            "MatchesBase": n_matches,
        })
    return pd.DataFrame(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspeciona aba Falhas removidas")
    parser.add_argument("--path", type=Path, default=None, help="Caminho do Excel")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="XLSX de saída (default: pasta do Excel)",
    )
    args = parser.parse_args()

    xlsx = args.path
    if xlsx is None:
        try:
            xlsx = Path(require_excel_path())
        except FileNotFoundError as exc:
            print(f"FAIL: {exc}")
            return 1
    else:
        xlsx = Path(xlsx)

    if not xlsx.is_file():
        print(f"FAIL: planilha não encontrada: {xlsx}")
        return 1

    path = str(xlsx)
    df_rem = _read_falhas_removidas(path)
    if df_rem.empty:
        print("Aba Falhas removidas não encontrada ou vazia.")
        return 0

    sim = _simulate_matches(path)
    read_base(path, ABA_BASE)
    resumo, detalhe, protocolos = get_removidas_state()

    print("=== Falhas removidas — diagnóstico ===")
    print(f"Planilha: {xlsx}")
    print(f"Total linhas log: {len(df_rem)}")
    if not sim.empty:
        print("\nPor Type:")
        print(sim.groupby("Type").size().to_string())
        print("\nPor Status simulado:")
        print(sim.groupby("Status").size().to_string())

    if resumo is not None and not resumo.empty:
        r = resumo.iloc[0]
        print("\n=== Aplicação na Base (após contestações) ===")
        for col in resumo.columns:
            print(f"  {col}: {r.get(col)}")

    sem_match = sim[sim["Status"] == "sem_match"] if not sim.empty else pd.DataFrame()
    if not sem_match.empty:
        print(f"\nAmostra sem match ({min(5, len(sem_match))} de {len(sem_match)}):")
        print(sem_match.head(5).to_string(index=False))

    amb = sim[sim["Status"] == "ambiguo"] if not sim.empty else pd.DataFrame()
    if not amb.empty:
        print(f"\nAmbíguos ({len(amb)}):")
        print(amb.head(10).to_string(index=False))

    out = args.output
    if out is None:
        out = xlsx.parent / f"_ACOMP_inspect_falhas_removidas.xlsx"
    else:
        out = Path(out)

    with pd.ExcelWriter(out, engine="openpyxl") as w:
        df_rem.to_excel(w, sheet_name="LogNormalizado", index=False)
        sim.to_excel(w, sheet_name="SimulacaoMatch", index=False)
        if resumo is not None and not resumo.empty:
            resumo.to_excel(w, sheet_name="ResumoAplicado", index=False)
        if detalhe is not None and not detalhe.empty:
            detalhe.to_excel(w, sheet_name="DetalheAplicado", index=False)

    print(f"\nRelatório salvo: {out}")
    print(f"Protocolos removidos: {len(protocolos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
