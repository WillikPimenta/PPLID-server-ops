"""Relatório consolidado de produtividade (Case Manager)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.bots.produtividade_case.transforms import match_consolidado_filter, rows_from_aggregate


def build_pipeline_cons(data_inicio_cons, data_fim_cons) -> list[dict]:
    return [
        {"$match": match_consolidado_filter(data_inicio_cons, data_fim_cons)},
        {"$unwind": "$origin.stages"},
        {
            "$group": {
                "_id": "$_id",
                "doc": {"$first": "$$ROOT"},
            }
        },
        {"$replaceRoot": {"newRoot": "$doc"}},
        {
            "$project": {
                "_id": 1,
                "cliente_origem": "$origin.customerName",
                "workflow_origem": "$origin.workflow",
                "nh_origem": "$origin.nHOrigin",
                "usuario_origem": "$origin.logon",
                "protocolo_origem": "$origin.customerId",
                "cpf": "$origin.person.document",
                "contrato": "$origin.customerContract",
                "cad_origem": "$origin.createdAt",
                "blocked_date": "$blockedDate",
                "conc_origem": "$origin.conclusionDate",
                "resultado_origem": "$origin.result",
                "status_origem": "$origin.transactionStatus",
                "alerts_origem": "$origin.alerts",
                "matricula_origem": "$origin.stages.credentialAnalyst",
                "tipo_conc_origem": {
                    "$cond": [
                        {"$eq": [{"$strLenCP": "$origin.stages.credentialAnalyst"}, 7]},
                        "Manual",
                        "Automático",
                    ]
                },
                "cad_dest": "$createdAt",
                "conc_dest": "$conclusionDate",
                "resultado_dest": "$auditResult",
                "status_dest": "$transactionStatus",
                "matricula_dest": "$user.logon",
                "alerts_dest": "$auditAlerts",
            }
        },
    ]


def gerar_consolidado(
    collection,
    data_inicio_cons,
    data_fim_cons,
    arquivo_cons: str | Path,
    *,
    label: str = "Consolidado",
) -> int:
    pipeline = build_pipeline_cons(data_inicio_cons, data_fim_cons)
    rows = rows_from_aggregate(collection.aggregate(pipeline, allowDiskUse=True))

    print(f"[INFO] {label}: {len(rows)} linhas | filtro {data_inicio_cons} -> {data_fim_cons}", flush=True)
    if not rows:
        print(f"[AVISO] {label} vazio — verifique janela de datas", flush=True)

    arquivo_cons = Path(arquivo_cons)
    arquivo_cons.parent.mkdir(parents=True, exist_ok=True)
    if arquivo_cons.exists():
        arquivo_cons.unlink()

    df_cons = pd.DataFrame(rows)
    with pd.ExcelWriter(arquivo_cons, engine="openpyxl") as writer:
        df_cons.to_excel(writer, index=False, sheet_name="Produtividade")

    return len(rows)
