"""Gera plano D-1 apenas para workflows da fila Documentoscopia 3.1."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_d1_planning import gerar_plano_replicacao_d1
from app.config import REPLICACAO_FILA_DOCUMENTOSCOPIA_31


def main() -> None:
    settings = {
        "replicacao_aud_data_ref": "20260630",
        "replicacao_apenas_fila": REPLICACAO_FILA_DOCUMENTOSCOPIA_31,
        "gerar_novo_plano": True,
        "excluir_historico": False,
        "limpar_planos_ao_gerar": False,
    }

    print("Gerando plano D-1 apenas fila 3.1 (data_ref=20260630)...")
    plano = gerar_plano_replicacao_d1(settings=settings)

    print(f"run_id: {plano.run_id}")
    print(f"workflows: {len(plano.workflows)}")
    print(f"auditores_ativos (G): {plano.auditores_ativos}")
    print(f"pasta_protocolos: {plano.pasta_protocolos}")
    print(f"relatorio: {plano.relatorio_excel_path}")
    print()
    print("--- Workflows fila 3.1 ---")
    for row in plano.resumo:
        wf = str(row.get("Workflow", "") or "")
        if not wf or wf == "TOTAL":
            continue
        amostra = row.get("Amostra Efetiva", row.get("Amostra Solicitada", "?"))
        protocolos = row.get("Protocolos Salvos", "?")
        status = row.get("Status", "?")
        fila = plano.workflow_fila.get(wf, "?")
        print(f"  {wf[:55]:55} | efetiva={amostra} | salvos={protocolos} | {status} | fila={fila}")

    avisos_31 = [w for w in plano.warnings if any(wf in w for wf in plano.workflows)]
    if avisos_31:
        print()
        print(f"Avisos dos workflows 3.1 ({len(avisos_31)}):")
        for aviso in avisos_31[:10]:
            print(f"  - {aviso}")


if __name__ == "__main__":
    main()
