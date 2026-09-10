"""Organiza artefatos D-1 separando BRFlow (G auditoria) e Case (Documentoscopia 3.1)."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_planning import (
    _normalizar_fila,
    _sanitizar_nome_arquivo,
    carregar_mapa_workflow_d1,
    normalizar_csv_escala_auditores,
    subpasta_protocolos_fila,
)
from app.config import (
    CONFIG_DEFAULT_D1_XLSX,
    ESCALA_AUDITORES_D1_CSV,
    PASTA_REPLICACAO_AUD_D1_PROTOCOLOS,
    PASTA_REPLICACAO_AUD_D1_RESUMO,
    PASTA_REPLICACAO_AUD_D1_RELATORIOS,
    REPLICACAO_AUD_D1_EXECUCAO_PREFIXO,
    REPLICACAO_AUD_D1_RELATORIO_PREFIXO,
    REPLICACAO_D1_SUBPASTA_BRFLOW,
    REPLICACAO_D1_SUBPASTA_CASE,
    REPLICACAO_FILA_G_AUDITORIA,
)


def _mapa_fila_default() -> dict[str, str]:
    mapa = carregar_mapa_workflow_d1(CONFIG_DEFAULT_D1_XLSX)
    out: dict[str, str] = {}
    for _, row in mapa.iterrows():
        wf = str(row.get("Workflow", "") or "").strip()
        if wf:
            out[wf] = _normalizar_fila(row.get("Fila", REPLICACAO_FILA_G_AUDITORIA))
    return out


def organizar_protocolos_run(run_id: str, *, dry_run: bool = False) -> dict:
    pasta_run = PASTA_REPLICACAO_AUD_D1_PROTOCOLOS / run_id
    if not pasta_run.is_dir():
        raise FileNotFoundError(f"Pasta do run não encontrada: {pasta_run}")

    mapa_fila = _mapa_fila_default()
    movidos: list[str] = []
    for csv_path in sorted(pasta_run.glob("*.csv")):
        wf = csv_path.stem
        wf_key = next((k for k in mapa_fila if _sanitizar_nome_arquivo(k) == wf), wf)
        fila = mapa_fila.get(wf_key, REPLICACAO_FILA_G_AUDITORIA)
        sub = subpasta_protocolos_fila(fila)
        dest_dir = pasta_run / sub
        dest = dest_dir / csv_path.name
        if dest.resolve() == csv_path.resolve():
            continue
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(csv_path), str(dest))
        movidos.append(f"{csv_path.name} -> {sub}/")

    estado_path = PASTA_REPLICACAO_AUD_D1_RESUMO / f"{REPLICACAO_AUD_D1_EXECUCAO_PREFIXO}{run_id}.json"
    if estado_path.exists() and movidos and not dry_run:
        with open(estado_path, encoding="utf-8") as fh:
            estado = json.load(fh)
        for wf, info in estado.get("workflows", {}).items():
            fila = mapa_fila.get(wf, REPLICACAO_FILA_G_AUDITORIA)
            sub = subpasta_protocolos_fila(fila)
            nome = f"{_sanitizar_nome_arquivo(wf)}.csv"
            info["fila"] = fila
            info["subpasta"] = sub
            info["csv"] = str(pasta_run / sub / nome)
        estado["pastas_fila"] = {
            REPLICACAO_D1_SUBPASTA_BRFLOW: str(pasta_run / REPLICACAO_D1_SUBPASTA_BRFLOW),
            REPLICACAO_D1_SUBPASTA_CASE: str(pasta_run / REPLICACAO_D1_SUBPASTA_CASE),
        }
        with open(estado_path, "w", encoding="utf-8") as fh:
            json.dump(estado, fh, ensure_ascii=False, indent=2)

    return {"run_id": run_id, "movidos": movidos, "dry_run": dry_run}


def normalizar_escala(*, dry_run: bool = False, copiar_case: bool = True) -> Path:
    path = ESCALA_AUDITORES_D1_CSV
    if not path.exists():
        raise FileNotFoundError(f"Escala não encontrada: {path}")
    df = normalizar_csv_escala_auditores(path, copiar_case_de_brflow=copiar_case)
    backup = path.with_suffix(".csv.bak")
    if not dry_run:
        if not backup.exists():
            shutil.copy2(path, backup)
        df.to_csv(path, sep=";", index=False, encoding="utf-8-sig")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Organiza arquivos D-1 (BRFlow vs Case)")
    parser.add_argument("--run-id", help="Move CSVs do run para subpastas brflow/ e case/")
    parser.add_argument("--normalizar-escala", action="store_true", help="Limpa escala_auditores.csv")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--nao-copiar-case",
        action="store_true",
        help="Não preenche auditores_ativos_case a partir de auditores_ativos",
    )
    args = parser.parse_args()

    if args.normalizar_escala:
        out = normalizar_escala(dry_run=args.dry_run, copiar_case=not args.nao_copiar_case)
        print(f"Escala normalizada: {out}")

    if args.run_id:
        res = organizar_protocolos_run(args.run_id, dry_run=args.dry_run)
        print(f"Run {res['run_id']}: {len(res['movidos'])} arquivo(s) reorganizado(s)")
        for linha in res["movidos"]:
            print(f"  {linha}")

    if not args.normalizar_escala and not args.run_id:
        parser.print_help()


if __name__ == "__main__":
    main()
