"""Relatório de produtividade por hora (pivot matricula × hora)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from app.bots.produtividade_case.io_excel import write_excel_atomic


def build_pipeline_hora(data_inicio, data_fim) -> list[dict]:
    return [
        {
            "$match": {
                "transactionStatus": "COMPLETED",
                "origin.requestType": "AUDIT",
                "conclusionDate": {"$gte": data_inicio, "$lt": data_fim},
            }
        },
        {"$unwind": "$origin.stages"},
        {
            "$project": {
                "_id": 1,
                "matricula": "$user.logon",
                "hora": {
                    "$hour": {
                        "$dateAdd": {
                            "startDate": "$conclusionDate",
                            "unit": "hour",
                            "amount": -3,
                        }
                    }
                },
            }
        },
        {
            "$group": {
                "_id": {
                    "transacao": "$_id",
                    "matricula": "$matricula",
                    "hora": "$hora",
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "matricula": "$_id.matricula",
                "hora": "$_id.hora",
            }
        },
    ]


def pivot_hora(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        pivot = pd.DataFrame(columns=list(range(24)) + ["TOTAL AGENTE"])
        pivot.index.name = "matricula"
        return pivot

    pivot = (
        df.groupby(["matricula", "hora"])
        .size()
        .reset_index(name="QTD")
        .pivot(index="matricula", columns="hora", values="QTD")
        .fillna(0)
        .astype(int)
    )
    pivot = pivot.reindex(columns=range(24), fill_value=0)
    pivot["TOTAL AGENTE"] = pivot.sum(axis=1)
    pivot = pivot[pivot["TOTAL AGENTE"] >= 1]

    linha_total = pivot.sum(axis=0)
    linha_total.name = "TOTAL HORA"
    return pd.concat([pivot, linha_total.to_frame().T])


def gerar_prod_hora(
    collection,
    data_inicio,
    data_fim,
    arquivo_final: str | Path,
    sheet_nome: str,
    dir_temp: str | Path,
) -> Path:
    df = pd.DataFrame(collection.aggregate(build_pipeline_hora(data_inicio, data_fim)))
    pivot = pivot_hora(df)
    return write_excel_atomic(pivot, arquivo_final, sheet_nome, dir_temp)


def nome_arquivo_hora(agora_br: datetime) -> str:
    return f"prod_hora_{agora_br.strftime('%Y-%m-%d_%H-%M')}.xlsx"
