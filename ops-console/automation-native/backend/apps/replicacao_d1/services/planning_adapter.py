# -*- coding: utf-8 -*-
"""Adaptador snapshot/banco → estruturas consumidas pelo planejamento D-1."""
from __future__ import annotations

from typing import Any

import pandas as pd

from apps.replicacao_d1.exceptions import ConfigBancoIndisponivelError
from apps.replicacao_d1.services.config_dto import ExecutionSnapshot, RunOptions
from apps.replicacao_d1.services.config_snapshot import build_execution_snapshot, is_fonte_banco_ativa
from apps.replicacao_d1.services.workflows_pending import get_or_create_workflow_pendente

# Nomes de colunas alinhados a automacoes/app/config/paths.py
COL_WORKFLOW = "Workflow"
COL_WORKFLOW_D1 = "Workflow d-1"
COL_WORKFLOW_SEL = "Workflow - selenium"
COL_CLIENTE = "Cliente"
COL_SEGMENTO = "Segmento"
COL_CATEGORIA = "Categoria"
COL_META_CLIENTE = "Meta Cliente"
COL_FILA = "Fila"
COL_USAR_ARQUIVO_CSV = "Usar arquivo CSV"
COL_ESCALA_DATA = "data"
COL_ESCALA_BR = "auditores_ativos"
COL_ESCALA_CASE = "auditores_ativos_case"
COL_ESCALA_BIO = "auditores_ativos_bio"
COL_ESCALA_REDOC = "auditores_ativos_redoc"


def load_snapshot_for_planning(
    run_options: RunOptions | None = None,
    *,
    run_id: str | None = None,
) -> ExecutionSnapshot:
    """Ponto de integração: consulta única ao banco no início do ciclo."""
    if not is_fonte_banco_ativa():
        raise ConfigBancoIndisponivelError(
            "Planejamento pelo banco requer fonte_banco_ativa=True."
        )
    return build_execution_snapshot(run_id=run_id, run_options=run_options, persist=bool(run_id))


def snapshot_to_dataframes(snapshot: ExecutionSnapshot) -> dict[str, pd.DataFrame]:
    """
    Converte snapshot congelado em DataFrames com schema esperado pelo planejamento.
    Volumetria/parquet continua sendo lida separadamente pelo bot.
    """
    persistent = snapshot.persistent
    mapa_rows: list[dict[str, Any]] = []
    for wf in persistent.get("workflows") or []:
        mapa_rows.append(
            {
                COL_WORKFLOW: wf.get("nome_canonico", ""),
                COL_CLIENTE: wf.get("cliente_nome", ""),
                COL_WORKFLOW_D1: wf.get("nome_d1") or wf.get("nome_canonico", ""),
                COL_WORKFLOW_SEL: wf.get("nome_selenium") or wf.get("nome_canonico", ""),
                COL_FILA: wf.get("fila", "G auditoria"),
                COL_USAR_ARQUIVO_CSV: bool(
                    wf.get(
                        "usar_arquivo_csv",
                        str(wf.get("fila") or "").strip().casefold() not in ("bio", "redoc"),
                    )
                ),
                "nome_regra_brflow": wf.get("nome_regra_brflow") or "",
                "_wf_key": wf.get("chave_normalizada", ""),
            }
        )

    cat_rows: list[dict[str, Any]] = []
    for cli in persistent.get("clientes") or []:
        row = {
            COL_CLIENTE: cli.get("nome", ""),
            COL_SEGMENTO: cli.get("segmento_nome", ""),
            COL_CATEGORIA: cli.get("categoria_nome", ""),
            "_cli_key": cli.get("chave_normalizada", ""),
        }
        meta = cli.get("meta_mensal")
        if meta is not None:
            row[COL_META_CLIENTE] = meta
        cat_rows.append(row)

    escala_rows: list[dict[str, Any]] = []
    for dia in persistent.get("escala") or []:
        escala_rows.append(
            {
                COL_ESCALA_DATA: dia.get("data", ""),
                COL_ESCALA_BR: int(dia.get("auditores_brflow", 0)),
                COL_ESCALA_CASE: int(dia.get("auditores_case", 0)),
                COL_ESCALA_BIO: int(dia.get("auditores_bio", 0)),
                COL_ESCALA_REDOC: int(dia.get("auditores_redoc", 0)),
            }
        )

    return {
        "mapa_workflow_d1": pd.DataFrame(mapa_rows),
        "categoria_clientes": pd.DataFrame(cat_rows),
        "escala_auditores": pd.DataFrame(escala_rows),
    }


def registrar_workflow_desconhecido_parquet(
    nome_observado: str,
    *,
    cliente_nome: str | None = None,
) -> tuple[str, bool]:
    """
    Hook para nomes ausentes no cadastro: cria pendente/inativo e retorna chave.
    Não inclui o workflow no plano (status PENDENTE).
    """
    wf, created = get_or_create_workflow_pendente(nome_observado, cliente_nome=cliente_nome)
    return wf.chave_normalizada, created
