"""Relatório de tempo logado (soma diff_segundos por matrícula × dia)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.bots.produtividade_case.io_excel import write_excel_atomic


def build_pipeline_tempo_logado(data_inicio, data_fim) -> list[dict]:
    return [
        {
            "$match": {
                "transactionStatus": "COMPLETED",
                "origin.requestType": "AUDIT",
                "conclusionDate": {"$gte": data_inicio, "$lt": data_fim},
            }
        },
        {
            "$project": {
                "transacao": "$_id",
                "matricula": "$user.logon",
                "dia": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": {
                            "$dateAdd": {
                                "startDate": "$conclusionDate",
                                "unit": "hour",
                                "amount": -3,
                            }
                        },
                    }
                },
                "diff_segundos": {
                    "$dateDiff": {
                        "startDate": "$blockedDate",
                        "endDate": "$conclusionDate",
                        "unit": "second",
                    }
                },
            }
        },
        {
            "$project": {
                "_id": 0,
                "transacao": 1,
                "matricula": 1,
                "dia": 1,
                "diff_segundos": 1,
            }
        },
    ]


def pivot_tempo_logado(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return (
        df.groupby(["matricula", "dia"])["diff_segundos"]
        .sum()
        .reset_index()
        .pivot(index="matricula", columns="dia", values="diff_segundos")
        .fillna(0)
        .sort_index()
    )


def gerar_tempo_logado(
    collection,
    data_inicio,
    data_fim,
    arquivo_final: str | Path,
    sheet_nome: str,
    dir_temp: str | Path,
    csv_temp: str | Path | None = None,
) -> Path:
    df = pd.DataFrame(collection.aggregate(build_pipeline_tempo_logado(data_inicio, data_fim)))
    if csv_temp:
        Path(csv_temp).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_temp, index=False)

    pivot = pivot_tempo_logado(df)
    return write_excel_atomic(pivot, arquivo_final, sheet_nome, dir_temp)
