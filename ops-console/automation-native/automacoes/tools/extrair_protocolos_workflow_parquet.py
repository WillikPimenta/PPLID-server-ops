"""Extrai protocolos de um workflow a partir dos parquets D-1 em um intervalo de datas."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.bots.replicacao_aud_planning import (
    COLUNA_DATA_ANALISE,
    COLUNA_PROTOCOLO,
    COLUNA_WORKFLOW_PARQUET,
    _filtrar_por_workflow,
    _parse_data_analise,
    _sanitizar_nome_arquivo,
)
from app.config import PASTA_DETALHADO_D1, PREFIXO_DETALHADO_FINAL
from app.infrastructure.parquet_reader import ler_parquet


def _parse_data(texto: str) -> datetime:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(texto.strip(), fmt)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"Data inválida: {texto!r} (use DD/MM/YYYY ou YYYY-MM-DD)")


def extrair_protocolos(
    workflow: str,
    data_inicio: datetime,
    data_fim: datetime,
    pasta_parquet: Path | None = None,
    saida: Path | None = None,
    somente_protocolos: bool = False,
) -> Path:
    pasta = pasta_parquet or PASTA_DETALHADO_D1
    if data_fim < data_inicio:
        raise ValueError("data_fim deve ser >= data_inicio")

    slug = _sanitizar_nome_arquivo(workflow)[:80]
    intervalo = f"{data_inicio.strftime('%Y%m%d')}_{data_fim.strftime('%Y%m%d')}"
    if saida is None:
        saida = Path.cwd() / f"protocolos_{slug}_{intervalo}.csv"

    partes: list[pd.DataFrame] = []
    cur = data_inicio
    while cur <= data_fim:
        nome = f"{PREFIXO_DETALHADO_FINAL}{cur.strftime('%Y%m%d')}.parquet"
        path = pasta / nome
        if not path.exists():
            print(f"AVISO: parquet ausente — {path}")
        else:
            df = ler_parquet(path)
            sub = _filtrar_por_workflow(df, workflow)
            if sub.empty:
                print(f"{nome}: 0 registros para o workflow")
            else:
                sub = sub.copy()
                sub[COLUNA_DATA_ANALISE] = _parse_data_analise(sub[COLUNA_DATA_ANALISE])
                sub = sub[sub[COLUNA_DATA_ANALISE].notna()]
                sub["data_referencia_d1"] = cur.strftime("%Y-%m-%d")
                sub["parquet_origem"] = nome
                print(
                    f"{nome}: {len(sub)} registros, "
                    f"{sub[COLUNA_PROTOCOLO].nunique()} protocolos únicos"
                )
                partes.append(sub)
        cur += timedelta(days=1)

    if not partes:
        raise FileNotFoundError(
            f"Nenhum protocolo encontrado para {workflow!r} entre "
            f"{data_inicio.date()} e {data_fim.date()}"
        )

    consolidado = pd.concat(partes, ignore_index=True)
    consolidado = consolidado.drop_duplicates(subset=[COLUNA_PROTOCOLO, "data_referencia_d1"])
    consolidado = consolidado.sort_values([COLUNA_DATA_ANALISE, COLUNA_PROTOCOLO]).reset_index(
        drop=True
    )

    saida.parent.mkdir(parents=True, exist_ok=True)
    if somente_protocolos:
        consolidado[COLUNA_PROTOCOLO].drop_duplicates().astype(str).to_csv(
            saida, index=False, header=False, encoding="utf-8-sig"
        )
    else:
        cols = [
            COLUNA_PROTOCOLO,
            COLUNA_WORKFLOW_PARQUET,
            COLUNA_DATA_ANALISE,
            "data_referencia_d1",
            "parquet_origem",
        ]
        consolidado[cols].to_csv(saida, index=False, encoding="utf-8-sig")

    print("---")
    print(f"Total registros: {len(consolidado)}")
    print(f"Protocolos únicos (geral): {consolidado[COLUNA_PROTOCOLO].nunique()}")
    print(f"CSV gerado: {saida}")
    return saida


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extrai protocolos de um workflow nos parquets D-1 (intervalo de datas)."
    )
    parser.add_argument("--workflow", required=True, help="Nome do workflow (coluna Workflow do parquet)")
    parser.add_argument("--de", required=True, type=_parse_data, help="Data inicial (DD/MM/YYYY)")
    parser.add_argument("--ate", required=True, type=_parse_data, help="Data final (DD/MM/YYYY)")
    parser.add_argument("--pasta-parquet", type=Path, default=None, help="Pasta dos parquets D-1")
    parser.add_argument("--saida", type=Path, default=None, help="Caminho do CSV de saída")
    parser.add_argument(
        "--somente-protocolos",
        action="store_true",
        help="Exporta só a coluna Protocolo, sem cabeçalho (formato bot)",
    )
    args = parser.parse_args()

    extrair_protocolos(
        workflow=args.workflow,
        data_inicio=args.de,
        data_fim=args.ate,
        pasta_parquet=args.pasta_parquet,
        saida=args.saida,
        somente_protocolos=args.somente_protocolos,
    )


if __name__ == "__main__":
    main()
