"""Gera plano D-1 completo (BRFlow + Case) e valida subpastas brflow/ e case/."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_planning import eh_fila_documentoscopia_31, subpasta_protocolos_fila
from app.bots.replicacao_aud_d1_planning import gerar_plano_replicacao_d1
from app.config import (
    PASTA_REPLICACAO_AUD_D1_RELATORIOS,
    REPLICACAO_D1_SUBPASTA_BRFLOW,
    REPLICACAO_D1_SUBPASTA_CASE,
    REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
    REPLICACAO_FILA_G_AUDITORIA,
)


def _contar_csvs(pasta: Path) -> int:
    if not pasta.is_dir():
        return 0
    return len(list(pasta.glob("*.csv")))


def validar_subpastas_plano(plano) -> dict:
    pasta_run = Path(plano.pasta_protocolos)
    brflow_dir = pasta_run / REPLICACAO_D1_SUBPASTA_BRFLOW
    case_dir = pasta_run / REPLICACAO_D1_SUBPASTA_CASE
    root_csvs = list(pasta_run.glob("*.csv"))

    por_fila = Counter(plano.workflow_fila.get(wf, "?") for wf in plano.workflows)
    erros: list[str] = []

    for wf, path in plano.csv_paths.items():
        fila = plano.workflow_fila.get(wf, REPLICACAO_FILA_G_AUDITORIA)
        sub_esperada = subpasta_protocolos_fila(fila)
        path_norm = str(path).replace("\\", "/")
        if sub_esperada not in path_norm:
            erros.append(f"{wf}: CSV fora da subpasta {sub_esperada} -> {path}")

    if root_csvs:
        erros.append(f"{len(root_csvs)} CSV(s) na raiz (esperado brflow/ e case/)")

    rel_brflow = PASTA_REPLICACAO_AUD_D1_RELATORIOS / REPLICACAO_D1_SUBPASTA_BRFLOW
    rel_case = PASTA_REPLICACAO_AUD_D1_RELATORIOS / REPLICACAO_D1_SUBPASTA_CASE
    rel_run_br = rel_brflow / f"replicacao_aud_d1_relatorio_{plano.run_id}.xlsx"
    rel_run_case = rel_case / f"replicacao_aud_d1_relatorio_{plano.run_id}.xlsx"

    return {
        "run_id": plano.run_id,
        "workflows_total": len(plano.workflows),
        "por_fila": dict(por_fila),
        "csvs_brflow": _contar_csvs(brflow_dir),
        "csvs_case": _contar_csvs(case_dir),
        "csvs_raiz": len(root_csvs),
        "pastas_fila": dict(plano.pastas_fila),
        "relatorio_consolidado": str(plano.relatorio_excel_path or ""),
        "relatorio_brflow": str(rel_run_br) if rel_run_br.exists() else "",
        "relatorio_case": str(rel_run_case) if rel_run_case.exists() else "",
        "erros": erros,
        "ok": not erros,
    }


def main() -> None:
    settings = {
        "replicacao_aud_data_ref": "20260630",
        "gerar_novo_plano": True,
        "excluir_historico": False,
        "limpar_planos_ao_gerar": False,
    }

    print("Gerando plano D-1 completo (BRFlow + Case)...")
    plano = gerar_plano_replicacao_d1(settings=settings)
    resultado = validar_subpastas_plano(plano)

    print(f"run_id: {resultado['run_id']}")
    print(f"workflows: {resultado['workflows_total']} | por fila: {resultado['por_fila']}")
    print(
        f"CSVs | brflow={resultado['csvs_brflow']} | case={resultado['csvs_case']} | raiz={resultado['csvs_raiz']}"
    )
    print(f"pasta_protocolos: {plano.pasta_protocolos}")
    print(f"pastas_fila: {resultado['pastas_fila']}")
    print(f"relatorio consolidado: {resultado['relatorio_consolidado']}")
    if resultado["relatorio_brflow"]:
        print(f"relatorio brflow: {resultado['relatorio_brflow']}")
    if resultado["relatorio_case"]:
        print(f"relatorio case: {resultado['relatorio_case']}")

    if resultado["erros"]:
        print("\nFALHA na validacao de subpastas:")
        for err in resultado["erros"][:20]:
            print(f"  - {err}")
        raise SystemExit(1)

    print("\nOK: subpastas brflow/ e case/ validadas com sucesso.")


if __name__ == "__main__":
    main()
