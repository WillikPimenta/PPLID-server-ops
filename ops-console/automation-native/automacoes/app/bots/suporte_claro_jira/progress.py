# -*- coding: utf-8 -*-
"""Progresso em tempo real para o portal (stdout JSON lines)."""
from __future__ import annotations

import json
import sys

PROGRESS_PREFIX = "@@JIRA_PROGRESS@@"

STEP_LABELS: dict[str, str] = {
    "init": "Iniciando automação…",
    "okta_login": "Abrindo Okta e fazendo login…",
    "open_jira": "Abrindo Jira pelo launcher Okta…",
    "wait_jira_ready": "Aguardando Jira carregar…",
    "open_create_form": "Abrindo formulário Criar Item…",
    "create_direct_url": "Navegando para Criar Item (URL direta)…",
    "create_click": "Procurando botão Criar…",
    "create_shortcut": "Tentando atalho de teclado (c)…",
    "create_wizard": "Assistente: projeto e tipo de item…",
    "fill_form": "Preenchendo campos do chamado…",
    "submit_issue": "Criando chamado no Jira…",
    "batch_item": "Formalizando demanda…",
    "waiting_user": "Chamado criado — vincule o código no portal…",
    "done": "Chamado criado no Jira.",
    "batch_done": "Lote concluído.",
    "error": "Falha na automação.",
}


def step_label(step: str) -> str:
    return STEP_LABELS.get(step, step.replace("_", " ").capitalize())


def report_progress(
    step: str,
    message: str = "",
    *,
    detail: str = "",
    ok: bool | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
    batch_items: list | None = None,
    batch_current_registro_id: int | None = None,
) -> None:
    """Emite linha de progresso para o robot_manager ler em tempo real."""
    payload: dict = {
        "step": step,
        "message": message or step_label(step),
        "step_label": step_label(step),
    }
    if detail:
        payload["detail"] = detail[:500]
    if ok is not None:
        payload["ok"] = ok
    if batch_index is not None:
        payload["batch_index"] = batch_index
    if batch_total is not None:
        payload["batch_total"] = batch_total
    if batch_items is not None:
        payload["batch_items"] = batch_items
    if batch_current_registro_id is not None:
        payload["batch_current_registro_id"] = batch_current_registro_id
    line = PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False)
    print(line, flush=True)
