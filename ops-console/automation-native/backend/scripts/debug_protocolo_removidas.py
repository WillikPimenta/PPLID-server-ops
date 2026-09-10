# -*- coding: utf-8 -*-
"""Diagnóstico pontual: por que um protocolo não foi removido."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from report_falhas.contestacoes import _apply_contestacoes_to_base, _read_contestacoes
from report_falhas.data_base import read_base
from report_falhas.falhas_removidas import (
    _read_falhas_removidas,
    extract_failure_fingerprint,
    get_removidas_state,
    match_base_rows,
    parse_tbga_json,
)
from report_falhas.io.data_loader import norm_matricula, norm_protocolo


def _show_df(df: pd.DataFrame, cols: list[str]) -> None:
    use = [c for c in cols if c in df.columns]
    if use and not df.empty:
        print(df[use].to_string(index=False))


from report_falhas.config_report import require_excel_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default=None, help="Planilha (padrao: IDF-Bots via config)")
    parser.add_argument("--protocolo", default="18242521")
    args = parser.parse_args()

    path = str(Path(args.path)) if args.path else require_excel_path()
    prot_alvo = norm_protocolo(args.protocolo)
    if not Path(path).is_file():
        print("ARQUIVO NAO ENCONTRADO:", path)
        return 1

    xl = pd.ExcelFile(path, engine="openpyxl")
    print("Abas:", xl.sheet_names)
    print()

    base = pd.read_excel(path, sheet_name="Base", engine="openpyxl")
    base["Protocolo"] = base["Protocolo"].apply(norm_protocolo)
    hits_base = base[base["Protocolo"] == prot_alvo]
    print("=== BASE (bruta) ===")
    print(f"Linhas com protocolo {prot_alvo}: {len(hits_base)}")
    _show_df(hits_base, [
        "Protocolo", "Matrícula Agente", "Novo cenário", "Cenário", "Etapa",
        "Data de Análise", "Cliente", "Localidade",
    ])
    print()

    rem = _read_falhas_removidas(path)
    print("=== FALHAS REMOVIDAS (log) ===")
    print("Total linhas aba:", len(rem))
    hits_rem = pd.DataFrame()
    if not rem.empty:
        mask = rem["__PROTOCOLO__"].astype(str) == prot_alvo
        for col in ("FailureDetailJSON", "BeforeJSON"):
            if col in rem.columns:
                mask = mask | rem[col].astype(str).str.contains(prot_alvo, na=False)
        hits_rem = rem[mask]
        print(f"Linhas log com protocolo {prot_alvo}: {len(hits_rem)}")
        if hits_rem.empty:
            print("Protocolo NAO encontrado na aba Falhas removidas")
        else:
            for _, row in hits_rem.iterrows():
                print("--- linha log ---")
                for k in (
                    "ID", "Type", "DateRemoved", "UserRemove", "DeleteMotive", "ChangeMotive",
                    "__KIND__", "__APLICAVEL__", "__PROTOCOLO__", "__MATRICULA__",
                    "__MATRICULA_NORM__", "__MOTIVO__", "__ETAPA__", "Path",
                ):
                    if k in row.index:
                        print(f"  {k}: {row[k]}")
                kind = row.get("__KIND__", "")
                src = row.get("BeforeJSON", "") if kind == "edit" else row.get("FailureDetailJSON", "")
                fp = extract_failure_fingerprint(parse_tbga_json(str(src)))
                print("  fingerprint extraido:", fp)
    print()

    base2 = base.copy()
    if "Matrícula Agente" in base2.columns:
        base2["_mat_norm"] = base2["Matrícula Agente"].apply(norm_matricula)
    ci, ce = _read_contestacoes(path)
    base_pos_cont, _, _ = _apply_contestacoes_to_base(base2, ci, ce)

    print("=== BASE pos-contestacao ===")
    hits_pos = base_pos_cont[base_pos_cont["Protocolo"] == prot_alvo]
    print(f"Linhas protocolo {prot_alvo}: {len(hits_pos)}")
    _show_df(hits_pos, ["Protocolo", "Matrícula Agente", "Novo cenário", "Cenário Unificado", "Etapa", "Data de Análise"])
    print()

    if not hits_rem.empty:
        print("=== SIMULACAO MATCH (pos-contestacao) ===")
        for _, row in hits_rem.iterrows():
            fp = {
                "protocolo": str(row.get("__PROTOCOLO__", "")),
                "matricula_norm": str(row.get("__MATRICULA_NORM__", "")),
                "motivo_norm": str(row.get("__MOTIVO_NORM__", "")),
                "etapa_norm": str(row.get("__ETAPA_NORM__", "")),
            }
            matches, status = match_base_rows(base_pos_cont, "Protocolo", fp)
            print(f"Log ID={row.get('ID')} Type={row.get('Type')} -> status={status} matches={len(matches)}")
            _show_df(matches, ["Protocolo", "Matrícula Agente", "Novo cenário", "Etapa"])
        print()

    filtered = read_base(path, "Base")
    hits_filt = filtered[filtered["Protocolo"] == prot_alvo]
    print("=== RESULTADO read_base (final) ===")
    print(f"Linhas protocolo {prot_alvo} na base filtrada: {len(hits_filt)}")
    if not hits_filt.empty:
        _show_df(hits_filt, ["Protocolo", "Matrícula Agente", "Novo cenário", "Etapa"])

    _, det, _ = get_removidas_state()
    if det is not None and not det.empty:
        d = det[det["Protocolo"].astype(str) == prot_alvo]
        print(f"\nRegistros no detalhe removidas: {len(d)}")
        if not d.empty:
            _show_df(d, [
                "ID", "Type", "Protocolo", "Matrícula Log", "Motivo Falha Log",
                "Etapa Log", "Status Match",
            ])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
