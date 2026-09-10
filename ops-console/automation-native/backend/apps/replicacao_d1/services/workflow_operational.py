# -*- coding: utf-8 -*-
"""Derivação de campos operacionais por workflow a partir do relatório."""
from __future__ import annotations

from apps.replicacao_d1.normalization import (
    RESULTADO_SEM_ALTERACAO,
    STATUS_FALHOU,
    STATUS_RECEBIDO,
    brflow_erro_codigo,
    normalizar_resultado_workflow,
)
from apps.replicacao_d1.services.excel_reader import ParsedWorkflow

_BRFLOW_OK = frozenset({"SALVO_OK", "UPLOAD_OK", "SEM_ALTERACAO"})


def protocolos_por_workflow(protocolos: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in protocolos:
        key = (item.workflow_config or "").strip()
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_workflow_operational_defaults(
    workflow: ParsedWorkflow,
    *,
    protocolos_planejados: int = 0,
    data_execucao=None,
    ingestion_id: int | None = None,
    motivo_codigo: str = "",
    motivo_resumo: str = "",
    fase_execucao: str = "",
    quantidade_alvo: int | None = None,
    quantidade_encontrada: int | None = None,
) -> dict:
    """Monta defaults operacionais para persistência em ReplicacaoD1WorkflowDia."""
    normalizado = normalizar_resultado_workflow(workflow.status_brflow, motivo_codigo)
    status_op = str(normalizado["status_operacional"])
    upper = (workflow.status_brflow or "").strip().upper()
    enviados = workflow.amostra_efetiva
    if enviados is None:
        enviados = protocolos_planejados
    aceitos = workflow.protocolos_salvos
    if aceitos is None and upper in _BRFLOW_OK:
        aceitos = enviados or protocolos_planejados
    aceitos = aceitos or 0
    enviados = enviados or 0
    if normalizado["resultado"] == RESULTADO_SEM_ALTERACAO:
        enviados = 0
        aceitos = 0

    upload_em = workflow.upload_em
    finished_at = upload_em
    if status_op == STATUS_RECEBIDO and upload_em:
        finished_at = upload_em
    elif status_op == STATUS_FALHOU and data_execucao:
        finished_at = data_execucao
    elif normalizado["resultado"] == RESULTADO_SEM_ALTERACAO and data_execucao:
        finished_at = data_execucao

    falha_bloqueante = bool(normalizado["falha_bloqueante"])
    erro_codigo = (motivo_codigo or brflow_erro_codigo(workflow.status_brflow))[:64] if falha_bloqueante else ""
    erro_resumo = (
        motivo_resumo or workflow.status_brflow or "Falha BRFlow"
    )[:255] if falha_bloqueante else ""

    qtd_alvo = quantidade_alvo
    if qtd_alvo is None:
        qtd_alvo = protocolos_planejados
    qtd_encontrada = quantidade_encontrada
    if qtd_encontrada is None and normalizado["resultado"] == RESULTADO_SEM_ALTERACAO:
        qtd_encontrada = qtd_alvo

    defaults = {
        "status_operacional": status_op,
        "resultado": normalizado["resultado"],
        "severidade": normalizado["severidade"],
        "motivo_codigo": str(motivo_codigo or "")[:64],
        "motivo_resumo": str(motivo_resumo or "")[:255],
        "fase_execucao": str(fase_execucao or "")[:64],
        "quantidade_alvo": qtd_alvo,
        "quantidade_encontrada": qtd_encontrada,
        "upload_em": upload_em,
        "protocolos_planejados": protocolos_planejados,
        "protocolos_enviados": int(enviados),
        "protocolos_aceitos": int(aceitos),
        "erro_codigo": erro_codigo,
        "erro_resumo": erro_resumo,
        "finished_at": finished_at,
    }
    if data_execucao and status_op != STATUS_FALHOU:
        defaults["started_at"] = data_execucao
    if ingestion_id:
        defaults["ingestion_id"] = ingestion_id
    return defaults
