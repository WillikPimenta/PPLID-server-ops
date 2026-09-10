"""Normaliza colunas e linhas dos parquets monitor-unificado em MONITOR_EVENTOS_FY_27."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.config import (
    PASTA_MONITOR_CONFER_TRATADO,
    PASTA_MONITOR_CONFER_UNIFICADO,
    PASTA_MONITOR_TRATADO,
    PREFIXO_MONITOR_CONFER_TRATADO,
    PREFIXO_MONITOR_CONFER_UNIFICADO,
    PREFIXO_MONITOR_TRATADO,
)
from app.bots.rotina.constants import COLUNAS_MONITOR_UNIFICADO
from app.bots.rotina.io import _preparar_dataframe_para_parquet
from app.bots.rotina.tasks.monitor import _normalizar_df_monitor_sessoes
from app.bots.rotina.tasks.unificados import _juntar_monitor_tratado_com_monitor_confer_dia

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


def _listar_datas(data_inicio: str | None, data_fim: str | None) -> list[str]:
    pasta = Path(PASTA_MONITOR_CONFER_UNIFICADO)
    datas = sorted(
        p.stem.replace("monitor-unificado_", "")
        for p in pasta.glob("monitor-unificado_*.parquet")
    )
    if data_inicio:
        datas = [d for d in datas if d >= data_inicio]
    if data_fim:
        datas = [d for d in datas if d <= data_fim]
    return datas


def _limpar_arquivo_unificado(caminho: Path) -> tuple[int, int]:
    df = pd.read_parquet(caminho)
    antes = len(df)
    df_limpo = _normalizar_df_monitor_sessoes(df)
    df_parquet = _preparar_dataframe_para_parquet(df_limpo)
    df_parquet.to_parquet(caminho, index=False, compression="snappy")
    return antes, len(df_limpo)


def _limpar_confer_tratado(data_ref: str) -> tuple[int, int] | None:
    data_confer = datetime.strptime(data_ref, "%Y%m%d").strftime("%d%m%Y")
    caminho = PASTA_MONITOR_CONFER_TRATADO / f"{PREFIXO_MONITOR_CONFER_TRATADO}{data_confer}.parquet"
    if not caminho.exists():
        return None
    df = pd.read_parquet(caminho)
    antes = len(df)
    df_limpo = _normalizar_df_monitor_sessoes(df)
    df_parquet = _preparar_dataframe_para_parquet(df_limpo)
    df_parquet.to_parquet(caminho, index=False, compression="snappy")
    return antes, len(df_limpo)


def _reunificar_dia(data_ref: str) -> bool:
    brflow = PASTA_MONITOR_TRATADO / f"{PREFIXO_MONITOR_TRATADO}{data_ref}.parquet"
    data_confer = datetime.strptime(data_ref, "%Y%m%d").strftime("%d%m%Y")
    confer = PASTA_MONITOR_CONFER_TRATADO / f"{PREFIXO_MONITOR_CONFER_TRATADO}{data_confer}.parquet"
    if not brflow.exists() or not confer.exists():
        return False
    return _juntar_monitor_tratado_com_monitor_confer_dia(data_ref) is not None


def main() -> int:
    parser = argparse.ArgumentParser(description="Limpa colunas extras do monitor-unificado FY_27")
    parser.add_argument("--data-inicio", help="Filtrar a partir de YYYYMMDD")
    parser.add_argument("--data-fim", help="Filtrar até YYYYMMDD")
    parser.add_argument(
        "--reunificar",
        action="store_true",
        help="Regerar monitor-unificado a partir dos tratados quando ambos existirem",
    )
    args = parser.parse_args()

    datas = _listar_datas(args.data_inicio, args.data_fim)
    if not datas:
        log.error("Nenhuma data encontrada em %s", PASTA_MONITOR_CONFER_UNIFICADO)
        return 1

    ajustados = 0
    reunificados = 0
    confer_ajustados = 0

    for data_ref in datas:
        caminho_unificado = Path(PASTA_MONITOR_CONFER_UNIFICADO) / f"{PREFIXO_MONITOR_CONFER_UNIFICADO}{data_ref}.parquet"
        if not caminho_unificado.exists():
            continue

        cols_antes = list(pd.read_parquet(caminho_unificado).columns)
        confer_stats = _limpar_confer_tratado(data_ref)
        if confer_stats:
            antes_conf, depois_conf = confer_stats
            if antes_conf != depois_conf or len(cols_antes) != len(COLUNAS_MONITOR_UNIFICADO):
                confer_ajustados += 1
                log.info("%s confer-tratado: %s -> %s linhas", data_ref, antes_conf, depois_conf)

        if args.reunificar and _reunificar_dia(data_ref):
            reunificados += 1
            df_final = pd.read_parquet(caminho_unificado)
            log.info(
                "%s reunificado | linhas=%s | colunas=%s",
                data_ref,
                len(df_final),
                list(df_final.columns),
            )
        else:
            antes, depois = _limpar_arquivo_unificado(caminho_unificado)
            if list(pd.read_parquet(caminho_unificado).columns) != COLUNAS_MONITOR_UNIFICADO or antes != depois:
                ajustados += 1
                log.info(
                    "%s unificado: %s -> %s linhas | colunas=%s",
                    data_ref,
                    antes,
                    depois,
                    list(pd.read_parquet(caminho_unificado).columns),
                )

    print()
    print("=" * 60)
    print(f"Dias processados: {len(datas)}")
    print(f"Confer-tratado ajustados: {confer_ajustados}")
    print(f"Monitor-unificado ajustados: {ajustados}")
    print(f"Monitor-unificado reunificados: {reunificados}")
    print(f"Schema final: {COLUNAS_MONITOR_UNIFICADO}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
