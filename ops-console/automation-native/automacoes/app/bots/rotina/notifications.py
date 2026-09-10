"""Notificações Teams e resumo de execução."""
from __future__ import annotations

import logging
import os
from datetime import datetime

import pandas as pd

from app.infrastructure.teams_adaptive_card import build_status_card, humanizar_observacao
from app.bots.rotina.constants import DEMANDAS_TABELA, EXPECTED_COLUMNS, OBS_HUMANIZADA, TASK_TO_ROTINA_DESCRICAO
from app.bots.rotina.state import exec_summary
from app.bots.rotina.csv_merge import CSVReader

log = logging.getLogger("robots.bot_rotina")

def _task_id_por_descricao_rotina(descricao: str):
    """Mapeia descrição BRBR-4467 ao task_id correspondente."""
    for task_id, desc in TASK_TO_ROTINA_DESCRICAO.items():
        if desc == descricao:
            return task_id
    return None


def _extra_obs_rules_rotina(valor: str):
    if valor.lower().startswith("download incompleto"):
        return valor[:60]
    if valor.lower().startswith("erro tarefa"):
        return "Falha durante a execução da tarefa"
    if "sem arquivo" in valor.lower():
        return "Arquivo não encontrado"
    return None


def _humanizar_observacao(texto: str) -> str:
    return humanizar_observacao(
        texto,
        obs_map=OBS_HUMANIZADA,
        extra_rules=_extra_obs_rules_rotina,
    )


def _teams_notificacao_habilitada(settings=None) -> bool:
    """Teams só no modo agendado; execução imediata suprime notificações."""
    s = settings or {}
    if s.get("executar_imediatamente"):
        return False
    if os.getenv("ROTINA_EXECUTAR_IMEDIATAMENTE", "").strip() == "1":
        return False
    return True


def _build_adaptive_card_demandas(erros: list = None, status: str = "sucesso") -> dict:
    agora = datetime.now().strftime("%d/%m/%Y às %H:%M")
    return build_status_card(
        titulo_card="Rotina diária — status das demandas",
        subtitulo=f"Atualizado em {agora}",
        demandas_tabela=DEMANDAS_TABELA,
        task_status=exec_summary["task_status"],
        erros=erros,
        obs_map=OBS_HUMANIZADA,
        extra_obs_rules=_extra_obs_rules_rotina,
    )


def _registrar_erro(mensagem: str):
    """Acumula erros para serem incluídos na notificação final."""
    exec_summary["erros"].append(str(mensagem))


def _registrar_arquivo_resumo(nome_arquivo: str, linhas_finais: int, duplicatas_merge: int = 0, tipo: str = "novo"):
    """Registra estatísticas de um arquivo processado para resumo final."""
    exec_summary["arquivos"].append({
        "nome": nome_arquivo,
        "linhas_finais": max(0, int(linhas_finais or 0)),
        "duplicatas_merge": max(0, int(duplicatas_merge or 0)),
        "tipo": tipo,
    })
    exec_summary["total_duplicatas_merge"] += max(0, int(duplicatas_merge or 0))
    exec_summary["total_linhas_finais"] += max(0, int(linhas_finais or 0))


def _registrar_duplicatas_internas(qtd: int):
    """Acumula duplicatas removidas internamente durante a consolidação do dia."""
    exec_summary["total_duplicatas_internas"] += max(0, int(qtd or 0))


def _contar_linhas_arquivo(arquivo: str) -> int:
    """Conta linhas de um arquivo CSV/Parquet/Excel de forma robusta."""
    try:
        if str(arquivo).lower().endswith('.parquet'):
            df_parquet = pd.read_parquet(arquivo)
            if df_parquet is not None:
                return len(df_parquet)
    except Exception:
        pass

    try:
        df = CSVReader.ler_csv(arquivo, esperado_colunas=len(EXPECTED_COLUMNS))
        if df is not None:
            return len(df)
    except Exception:
        pass

    try:
        df_excel = pd.read_excel(arquivo)
        if df_excel is not None:
            return len(df_excel)
    except Exception:
        pass

    return 0


def _gerar_resumo_notificacao_sucesso() -> tuple:
    """Gera título e Adaptive Card da notificação final de sucesso."""
    titulo = "Rotina diária concluída"
    return titulo, _build_adaptive_card_demandas(status="sucesso")


def _gerar_resumo_notificacao_erro(erros: list) -> tuple:
    """Gera título e Adaptive Card da notificação final com erros."""
    titulo = "Rotina diária com pendências"
    return titulo, _build_adaptive_card_demandas(erros=erros, status="erro")

def _contar_linhas_arquivo(arquivo: str) -> int:
    """Conta linhas de um arquivo CSV/Parquet/Excel de forma robusta."""
    try:
        if str(arquivo).lower().endswith('.parquet'):
            df_parquet = pd.read_parquet(arquivo)
            if df_parquet is not None:
                return len(df_parquet)
    except Exception:
        pass

    try:
        df = CSVReader.ler_csv(arquivo, esperado_colunas=len(EXPECTED_COLUMNS))
        if df is not None:
            return len(df)
    except Exception:
        pass

    try:
        df_excel = pd.read_excel(arquivo)
        if df_excel is not None:
            return len(df_excel)
    except Exception:
        pass

    return 0
