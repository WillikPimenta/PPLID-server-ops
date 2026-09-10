"""Tarefa produtividade D-1."""
from __future__ import annotations

import logging
import os
import time
from datetime import timedelta
from pathlib import Path

import pandas as pd

from app.config import *
from app.bots.rotina.constants import DOWNLOADS_TEMP_ROTINA
from app.bots.rotina.io import (
    _corrigir_colunas_dataframe,
    _ler_arquivo_csv_robusto,
    _ler_csv_tratamento,
    _listar_arquivos_baixados_validos,
    _limpar_downloads_temp_inicio,
    _obter_data_base_execucao,
    _preparar_dataframe_para_parquet,
    verifica_usuario,
)
from app.bots.rotina.selenium_brflow import _baixar_arquivos_rotina
from app.bots.rotina.state import (
    _registrar_arquivo_resumo,
    _registrar_erro,
    _set_progress,
    _set_status,
)

log = logging.getLogger("robots.bot_rotina")

def tratar_produtividade(arquivo):
    """Gera produtividade tratada por hora a partir do detalhado bruto."""
    try:
        os.makedirs(PASTA_PROD_TRATADO, exist_ok=True)

        df = _ler_csv_tratamento(str(arquivo))

        obrigatorias = {"datAnalise", "numTempoAnalise", "desMatricula", "nomCliente", "nomWorkflow", "nomEtapa"}
        faltantes = [c for c in obrigatorias if c not in df.columns]
        if faltantes:
            log.warning(f"Produtividade tratada ignorada, colunas ausentes: {faltantes}")
            return None

        df["datAnalise"] = pd.to_datetime(df["datAnalise"], dayfirst=True, errors="coerce")
        df = df[df["datAnalise"].notna()].copy()
        if df.empty:
            log.warning("Produtividade tratada ignorada: sem linhas válidas após parse de datAnalise")
            return None

        df["Data"] = df["datAnalise"].dt.date

        menor_data = df["Data"].min()
        nome_arquivo = f"{PREFIXO_PROD_TRATADA}{menor_data.strftime('%Y%m%d')}"

        df = df[df["Data"] == menor_data].copy()
        df["Hora"] = df["datAnalise"].dt.hour

        df_sorted = df.sort_values(by=["desMatricula", "datAnalise"]).copy()

        df_sorted["numTempoAnalise"] = (
            pd.to_timedelta(df_sorted["numTempoAnalise"], errors="coerce")
            .fillna(pd.Timedelta(seconds=0))
        )

        df_sorted["numTempoAnaliseSeconds"] = df_sorted["numTempoAnalise"].dt.total_seconds()

        df_sorted["datConclusao"] = (
            df_sorted["datAnalise"]
            + pd.to_timedelta(df_sorted["numTempoAnaliseSeconds"], unit="s")
        )

        df_sorted = df_sorted.drop(columns=["numTempoAnaliseSeconds"])

        df_sorted["gap"] = df_sorted["datAnalise"].shift(-1) - df_sorted["datConclusao"]

        df_sorted.loc[
            (df_sorted["desMatricula"] != df_sorted["desMatricula"].shift(-1)) |
            (df_sorted["Data"] != df_sorted["Data"].shift(-1)),
            "gap"
        ] = pd.Timedelta(seconds=0)

        df_sorted.loc[
            (df_sorted["desMatricula"] == df_sorted["desMatricula"].shift(-1)) &
            (df_sorted["datConclusao"] > df_sorted["datAnalise"].shift(-1)),
            "gap"
        ] = pd.Timedelta(seconds=0)

        df_sorted.loc[
            df_sorted["gap"] > pd.Timedelta(seconds=19800),
            "gap"
        ] = pd.Timedelta(seconds=0)

        resultado = (
            df_sorted
            .groupby(
                ["Data", "Hora", "desMatricula", "nomCliente", "nomWorkflow", "nomEtapa"]
            )
            .agg(
                tempoAnalise=("numTempoAnalise", "sum"),
                contagem=("desMatricula", "count")
            )
            .reset_index()
        )

        resultado["tempoAnalise"] = (
            resultado["tempoAnalise"]
            .dt.total_seconds()
            .astype(int)
        )

        caminho_out = os.path.join(str(PASTA_PROD_TRATADO), f"{nome_arquivo}.parquet")
        resultado_parquet = _preparar_dataframe_para_parquet(resultado)
        resultado_parquet.to_parquet(str(caminho_out), index=False, compression='snappy')
        log.info(f"✅ Produtividade tratada: {caminho_out} | Destino: {os.path.abspath(caminho_out)}")
        _set_status(f"✅ Produtividade tratada salva | Destino: {os.path.abspath(caminho_out)}")
        return caminho_out
    except Exception as exc:
        log.error(f"Erro ao tratar produtividade: {exc}", exc_info=True)
        return None

def _baixar_produtividade_d1(drv, descricao):
    """
    Baixa a rotina de produtividade D-1 e apenas consolida as partes baixadas.
    Não aplica merge com arquivo existente nem limpeza adicional.
    """
    try:
        pasta_temp = DOWNLOADS_TEMP_ROTINA
        pasta_destino_final = PASTA_PRODUTIVIDADE_D1_BRUTA
        pasta_destino_final.mkdir(parents=True, exist_ok=True)

        _set_status(f"Iniciando download da produtividade: {descricao}")
        _set_progress(61, "Produtividade D-1: iniciando download")

        _limpar_downloads_temp_inicio("Extração Produtividade D-1")
        _baixar_arquivos_rotina(drv, descricao, str(pasta_temp))
        time.sleep(2)

        arquivos_na_pasta = _listar_arquivos_baixados_validos(pasta_temp)
        if not arquivos_na_pasta:
            log.warning(f"Nenhum arquivo baixado para produtividade: {descricao}")
            log.warning("[Produtividade] Download não reconhecido; finalizando e seguindo para o próximo dia")
            _set_status("Produtividade: nenhum arquivo encontrado")
            _registrar_erro(f"⚠️ Produtividade sem arquivo: {descricao}")
            _limpar_downloads_temp_inicio("Finalizando produtividade D-1 sem arquivo reconhecido")
            return

        dfs = []
        arquivos_na_pasta.sort(key=lambda x: os.path.getctime(x))
        for arquivo in arquivos_na_pasta:
            df = _ler_arquivo_csv_robusto(arquivo)
            if df is not None and len(df) > 0:
                dfs.append(df)

        if not dfs:
            log.warning(f"Não foi possível ler os arquivos baixados da produtividade: {descricao}")
            log.warning("[Produtividade] Download não reconhecido; finalizando e seguindo para o próximo dia")
            _set_status("Produtividade: arquivos inválidos para consolidação")
            _registrar_erro(f"⚠️ Produtividade com dados inválidos: {descricao}")
            _limpar_downloads_temp_inicio("Finalizando produtividade D-1 sem arquivo reconhecido")
            return

        df_consolidado = pd.concat(dfs, ignore_index=True)
        df_consolidado = _corrigir_colunas_dataframe(df_consolidado)

        if "desMatricula" in df_consolidado.columns:
            antes = len(df_consolidado)
            # Extrai os primeiros 7 caracteres (ex.: "c91123a - Nome" → "c91123a")
            matricula_raw = df_consolidado["desMatricula"].astype(str).str.strip().str[:7]
            # Valida usando a mesma regra de eh_matricula_val (c + dígitos + letra)
            matricula_valida = matricula_raw.apply(verifica_usuario)
            df_consolidado = df_consolidado[matricula_valida].copy()
            # Normaliza o campo: mantém apenas os 7 primeiros caracteres, sem nomes em sequência
            df_consolidado["desMatricula"] = matricula_raw[matricula_valida].values
            removidas = antes - len(df_consolidado)
            if removidas:
                log.info(f"[Produtividade] {removidas:,} linha(s) removidas por desMatricula inválida")

        data_d1 = _obter_data_base_execucao() - timedelta(days=1)
        nome_arquivo_final = f"{PREFIXO_PROD_D1}{data_d1.strftime('%Y%m%d')}.parquet"
        arquivo_final = pasta_destino_final /nome_arquivo_final

        df_consolidado_parquet = _preparar_dataframe_para_parquet(df_consolidado)
        df_consolidado_parquet.to_parquet(str(arquivo_final), index=False, compression='snappy')
        print(f"ROTINA_BRUTO_SAVED|prod|{arquivo_final.resolve()}", flush=True)

        for arquivo in arquivos_na_pasta:
            try:
                os.remove(arquivo)
            except Exception as exc:
                log.warning(f"Erro ao remover arquivo temporário da produtividade {arquivo}: {exc}")

        _registrar_arquivo_resumo(
            nome_arquivo=arquivo_final.name,
            linhas_finais=len(df_consolidado),
            duplicatas_merge=0,
            tipo="novo",
        )

        log.info(f"✅ Produtividade consolidada salva: {arquivo_final} | Destino: {os.path.abspath(arquivo_final)}")
        _set_status(f"✅ Produtividade consolidada: {arquivo_final.name}")
        _set_progress(62, "Produtividade D-1: concluida")
        tratar_produtividade(str(arquivo_final))

    except Exception as e:
        log.error(f"Erro ao baixar produtividade D-1: {e}", exc_info=True)
        _set_status(f"Erro na produtividade: {e}")
        _registrar_erro(f"❌ Erro BRBR-5336: {str(e)[:120]}")
