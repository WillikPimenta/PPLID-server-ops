# -*- coding: utf-8 -*-
"""Persistência do estado JSON por workflow durante a execução D-1."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from app.bots.replicacao_aud_d1_planning import _csv_relativo_plano
from app.bots.replicacao_aud_planning import _resolver_csv_workflow_plano

if TYPE_CHECKING:
    from app.bots.replicacao_aud_d1_planning import PlanoReplicacao


def update_workflow_state(
    estado: dict[str, Any],
    workflow: str,
    status: str,
    csv_path: Path | None = None,
    *,
    workflow_brflow: str | None = None,
    motivo: str | None = None,
    resultado: str | None = None,
    motivo_codigo: str | None = None,
    motivo_resumo: str | None = None,
    fase_execucao: str | None = None,
    quantidade_alvo: int | None = None,
    quantidade_encontrada: int | None = None,
    iniciar_tentativa: bool = False,
    plano: PlanoReplicacao | None = None,
) -> None:
    if not estado:
        return
    workflows = estado.setdefault("workflows", {})
    entry = workflows.setdefault(workflow, {})
    entry["status"] = status
    agora = datetime.now().isoformat(timespec="seconds")
    if iniciar_tentativa:
        entry["attempt_number"] = int(entry.get("attempt_number") or 0) + 1
        entry["started_at"] = agora
        entry.pop("finished_at", None)
    if csv_path is not None:
        if plano is not None:
            entry["csv"] = _csv_relativo_plano(plano, Path(csv_path))
        else:
            entry["csv"] = str(csv_path)
    if workflow_brflow:
        entry["workflow_brflow"] = workflow_brflow
    resumo = motivo_resumo if motivo_resumo is not None else motivo
    if resumo is not None:
        entry["motivo"] = resumo
        entry["motivo_resumo"] = resumo
    if motivo_codigo is not None:
        entry["motivo_codigo"] = motivo_codigo
    if resultado is not None:
        entry["resultado"] = resultado
    if fase_execucao is not None:
        entry["fase_execucao"] = fase_execucao
    if quantidade_alvo is not None:
        entry["quantidade_alvo"] = int(quantidade_alvo)
    if quantidade_encontrada is not None:
        entry["quantidade_encontrada"] = int(quantidade_encontrada)
    if status == "SALVO_OK":
        entry["motivo"] = ""
        entry["motivo_resumo"] = ""
        entry["motivo_codigo"] = ""
    if status in {"SALVO_OK", "SEM_ALTERACAO", "PULADO", "INATIVO", "NAO_SALVO", "ERRO", "CANCELADO"}:
        entry["finished_at"] = agora
    entry["atualizado_em"] = agora
    _recalcular_metricas(estado)


def _recalcular_metricas(estado: dict[str, Any]) -> None:
    workflows = dict(estado.get("workflows") or {})
    contagens: dict[str, int] = {}
    qtd_alvo = 0
    qtd_encontrada = 0
    for info in workflows.values():
        status = str(info.get("status") or "PENDENTE").upper()
        contagens[status] = contagens.get(status, 0) + 1
        qtd_alvo += int(info.get("quantidade_alvo") or info.get("qtd_calculada") or 0)
        qtd_encontrada += int(info.get("quantidade_encontrada") or 0)
    estado["metricas"] = {
        "workflows_total": len(workflows),
        "por_status": contagens,
        "quantidade_alvo_total": qtd_alvo,
        "quantidade_encontrada_total": qtd_encontrada,
        "quantidade_ja_configurada": contagens.get("SEM_ALTERACAO", 0),
        "quantidade_salva": contagens.get("SALVO_OK", 0),
    }


def resolve_csv_upload_workflow(
    plano: PlanoReplicacao,
    workflow: str,
    estado: dict[str, Any] | None,
) -> Path:
    """Resolve CSV do workflow na pasta do run (ignora path absoluto obsoleto)."""
    info = (estado or {}).get("workflows", {}).get(workflow, {}) if estado else {}
    csv_informado = str(info.get("csv", "") or "")
    return _resolver_csv_workflow_plano(plano, workflow, csv_informado)
