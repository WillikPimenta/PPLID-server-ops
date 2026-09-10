"""Analisa workflows com protocolos no Excel mas não salvos no BRFlow."""
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots.replicacao_aud_planning import (
    PlanoReplicacao,
    _ler_protocolos_de_csv,
    _sanitizar_nome_arquivo,
    csv_protocolos_e_limpeza,
    workflow_elegivel_upload,
)
from app.bots import replicacao_aud_d1_planning as d1
from app.config import PASTA_REPLICACAO_AUD_D1_RESUMO

RUN_ID = "20260707_090016"
BASE = Path(
    r"c:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
    r"\Planejamento - IDF - Bases\Bots\brflow-auditoria-replic-d1"
)
LOG = Path(r"c:\Users\c93123a\Downloads\robot_replicacao_auditoria_d1 (6).log")


def main():
    proto_dir = BASE / "protocolos" / RUN_ID
    estado = json.loads(
        (BASE / "resumo" / f"execucao_d1_{RUN_ID}.json").read_text(encoding="utf-8")
    )
    df = pd.read_excel(
        BASE / "resumo" / "relatorios" / f"replicacao_aud_d1_relatorio_{RUN_ID}.xlsx",
        sheet_name="Resumo",
        engine="openpyxl",
    )
    df = df[df["Workflow"].astype(str) != "TOTAL"].copy()
    df["Protocolos Salvos"] = pd.to_numeric(df["Protocolos Salvos"], errors="coerce").fillna(0)

    log = LOG.read_text(encoding="utf-8", errors="replace")
    salvos_log = set(re.findall(r"\[2026-07-07 12:.*STATUS\|Salvo: (.+)", log))

    com_proto = df[df["Protocolos Salvos"] > 0]
    pulados = [str(r.Workflow).strip() for _, r in com_proto.iterrows() if str(r.Workflow).strip() not in salvos_log]

    print(f"Excel com protocolos > 0: {len(com_proto)}")
    print(f"Salvos no log BRFlow: {len(salvos_log)}")
    print(f"Com protocolos mas NAO salvos: {len(pulados)}")
    print()

    plano = d1.carregar_plano_por_run_id(RUN_ID, settings={"apenas_pendentes": True})
    print(f"Workflows na fila carregar_plano (retomada): {len(plano.workflows)}")
    na_fila = set(plano.workflows)
    fora_fila = [wf for wf in pulados if wf not in na_fila]
    print(f"Pulados com proto FORA da fila retomada: {len(fora_fila)}")
    for wf in fora_fila[:20]:
        print(f"  FORA FILA: {wf}")
    print()

    print("--- Detalhe pulados com protocolos ---")
    for wf in pulados:
        info = estado.get("workflows", {}).get(wf, {})
        nome = _sanitizar_nome_arquivo(wf) + ".csv"
        found = list(proto_dir.rglob(nome))
        csv_path = found[0] if found else None
        proto_csv = len(_ler_protocolos_de_csv(csv_path)) if csv_path else 0
        limpeza = csv_protocolos_e_limpeza(csv_path) if csv_path else None
        elegivel = workflow_elegivel_upload(list(range(proto_csv)), csv_path) if csv_path else False
        excel_n = int(com_proto.loc[com_proto["Workflow"] == wf, "Protocolos Salvos"].iloc[0])
        in_fila = wf in na_fila
        print(
            f"{wf[:55]:55} | excel={excel_n:4} | csv={proto_csv:4} | limpeza={limpeza} | "
            f"fila={in_fila} | status={info.get('status')} | csv_estado={bool(info.get('csv'))}"
        )


if __name__ == "__main__":
    main()
