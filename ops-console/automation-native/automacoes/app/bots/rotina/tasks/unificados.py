"""Joins prod-unificado e monitor-unificado."""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import *
from app.bots.rotina.io import _preparar_dataframe_para_parquet
from app.bots.rotina.tasks.monitor import _normalizar_df_monitor_sessoes
from app.bots.rotina.state import _set_status
from app.infrastructure.file_mirror import espelhar_arquivo

log = logging.getLogger("robots.bot_rotina")

def _juntar_confer_prod_tratados_dia(data_ref: str):
    """Une arquivos confer-tratado e prod-tratada do mesmo dia em um único parquet."""
    try:
        PASTA_CONFER_PROD_UNIFICADO.mkdir(parents=True, exist_ok=True)

        arquivo_confer = PASTA_PRODUCAO_CONFER_TRATADO / f"{PREFIXO_CONF_TRATADO}{data_ref}.parquet"
        arquivo_prod = PASTA_PROD_TRATADO / f"{PREFIXO_PROD_TRATADA}{data_ref}.parquet"

        if not arquivo_confer.exists() or not arquivo_prod.exists():
            faltantes = []
            if not arquivo_confer.exists():
                faltantes.append(arquivo_confer.name)
            if not arquivo_prod.exists():
                faltantes.append(arquivo_prod.name)
            log.warning(f"União confer/prod não executada para {data_ref}: arquivo(s) ausente(s): {', '.join(faltantes)}")
            return None

        df_confer = pd.read_parquet(str(arquivo_confer))
        df_prod = pd.read_parquet(str(arquivo_prod))

        if "desMatricula" in df_prod.columns and "matricula" not in df_prod.columns:
            df_prod = df_prod.rename(columns={"desMatricula": "matricula"})

        df_unificado = pd.concat([df_confer, df_prod], ignore_index=True, sort=False)
        if df_unificado.empty:
            log.warning(f"União confer/prod resultou vazia para {data_ref}")
            return None

        colunas_ordenacao = [c for c in ["Data", "Hora", "matricula"] if c in df_unificado.columns]
        if colunas_ordenacao:
            df_unificado = df_unificado.sort_values(by=colunas_ordenacao, kind="stable").reset_index(drop=True)

        arquivo_saida = PASTA_PROD_UNIFICADO_BOTS / f"{PREFIXO_CONF_PROD_UNIFICADO}{data_ref}.parquet"
        df_unificado = _preparar_dataframe_para_parquet(df_unificado)
        PASTA_PROD_UNIFICADO_BOTS.mkdir(parents=True, exist_ok=True)
        df_unificado.to_parquet(str(arquivo_saida), index=False, compression="snappy")
        espelhar_arquivo(
            arquivo_saida,
            [PASTA_CONFER_PROD_UNIFICADO / arquivo_saida.name],
            log=log,
        )

        log.info(f"✅ Confer/Prod unificado salvo: {arquivo_saida.name} ({len(df_unificado)} linhas) | Destino: {os.path.abspath(arquivo_saida)}")
        _set_status(f"✅ Confer/Prod unificado salvo | Destino: {os.path.abspath(arquivo_saida)}")
        return arquivo_saida
    except Exception as exc:
        log.error(f"Erro ao unir confer-tratado e prod-tratada ({data_ref}): {exc}", exc_info=True)
        return None

def _juntar_monitor_tratado_com_monitor_confer_dia(data_ref: str):
    """Une monitor-tratado (YYYYMMDD) com Monitor_confer (DDMMYYYY) do mesmo dia."""
    try:
        PASTA_MONITOR_CONFER_UNIFICADO.mkdir(parents=True, exist_ok=True)

        arquivo_monitor_tratado = PASTA_MONITOR_TRATADO / f"{PREFIXO_MONITOR_TRATADO}{data_ref}.parquet"
        data_ref_confer = datetime.strptime(data_ref, "%Y%m%d").strftime("%d%m%Y")
        arquivo_monitor_confer = PASTA_MONITOR_CONFER_TRATADO / f"{PREFIXO_MONITOR_CONFER_TRATADO}{data_ref_confer}.parquet"

        if not arquivo_monitor_tratado.exists() or not arquivo_monitor_confer.exists():
            faltantes = []
            if not arquivo_monitor_tratado.exists():
                faltantes.append(arquivo_monitor_tratado.name)
            if not arquivo_monitor_confer.exists():
                faltantes.append(arquivo_monitor_confer.name)
            log.warning(f"União monitor/confer não executada para {data_ref}: arquivo(s) ausente(s): {', '.join(faltantes)}")
            return None

        df_monitor_tratado = pd.read_parquet(str(arquivo_monitor_tratado))
        df_monitor_confer = pd.read_parquet(str(arquivo_monitor_confer))

        df_monitor_tratado = _normalizar_df_monitor_sessoes(df_monitor_tratado)
        df_monitor_confer = _normalizar_df_monitor_sessoes(df_monitor_confer)

        df_unificado = pd.concat([df_monitor_tratado, df_monitor_confer], ignore_index=True, sort=False)
        if df_unificado.empty:
            log.warning(f"União monitor/confer resultou vazia para {data_ref}")
            return None

        colunas_ordenacao = [c for c in ["Data", "Hora", "Usuário", "Data do Evento"] if c in df_unificado.columns]
        if colunas_ordenacao:
            df_unificado = df_unificado.sort_values(by=colunas_ordenacao, kind="stable").reset_index(drop=True)

        df_unificado = _normalizar_df_monitor_sessoes(df_unificado)

        arquivo_saida = PASTA_MONITOR_UNIFICADO_BOTS / f"{PREFIXO_MONITOR_CONFER_UNIFICADO}{data_ref}.parquet"
        df_unificado = _preparar_dataframe_para_parquet(df_unificado)
        PASTA_MONITOR_UNIFICADO_BOTS.mkdir(parents=True, exist_ok=True)
        df_unificado.to_parquet(str(arquivo_saida), index=False, compression="snappy")
        espelhar_arquivo(
            arquivo_saida,
            [PASTA_MONITOR_CONFER_UNIFICADO / arquivo_saida.name],
            log=log,
        )

        log.info(f"✅ Monitor/Confer unificado salvo: {arquivo_saida.name} ({len(df_unificado)} linhas) | Destino: {os.path.abspath(arquivo_saida)}")
        _set_status(f"✅ Monitor/Confer unificado salvo | Destino: {os.path.abspath(arquivo_saida)}")
        return arquivo_saida
    except Exception as exc:
        log.error(f"Erro ao unir monitor-tratado e Monitor_confer ({data_ref}): {exc}", exc_info=True)
        return None
