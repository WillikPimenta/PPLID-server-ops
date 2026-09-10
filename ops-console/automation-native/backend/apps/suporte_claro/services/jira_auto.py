# -*- coding: utf-8 -*-
"""Criação automática de issue Jira no cadastro de demanda."""
from __future__ import annotations

from typing import Any

from apps.suporte_claro.models import SuporteClaroRegistro
from apps.suporte_claro.services.jira_copy import build_registro_jira_copy, resolve_jira_profile_key
from apps.suporte_claro.services.jira_link import auto_link_jira_issue_to_registro, get_registro_jira_key
from apps.suporte_claro.services.jira_rest import (
    create_issue,
    resolve_jira_credentials,
    sync_portal_status_to_jira,
    user_jira_configured,
)


class JiraAutoCreateError(Exception):
    """Falha ao criar/vincular Jira no cadastro (dispara rollback)."""

    def __init__(self, message: str, *, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def require_user_jira_for_create(user) -> str | None:
    """Retorna mensagem de erro se o usuário não pode formalizar; None se ok."""
    if user_jira_configured(user):
        return None
    from apps.suporte_claro.services.jira_rest import jira_base_configured, resolve_jira_credentials

    if not jira_base_configured():
        return "Integração Jira não configurada no servidor (JIRA_BASE_URL)."
    creds = resolve_jira_credentials(user)
    if creds is None:
        try:
            from apps.escala_flex.services.permissions import get_agent_for_user

            agent = get_agent_for_user(user)
        except Exception:
            agent = None
        if agent is None:
            return (
                "Cadastre o Personal Access Token do Jira no Headcount "
                "(é necessário vínculo com colaborador/Agent)."
            )
        if not (agent.user_lan_id or "").strip():
            return "Seu colaborador (Agent) precisa ter LAN ID cadastrado para criar no Jira."
        return (
            "Cadastre seu Personal Access Token do Jira antes de registrar a demanda "
            "(modal Formalizar no Jira ou Headcount)."
        )
    return "Não foi possível validar as credenciais Jira."


def create_and_link_jira_for_registro(registro: SuporteClaroRegistro, user) -> dict[str, Any]:
    """
    Cria issue Jira (assignee = LAN do criador; reporter = autenticado via PAT) e vincula ao registro.
    Anexos do portal NÃO são enviados automaticamente ao Jira.
    Levanta JiraAutoCreateError se falhar (para rollback da transação).
    """
    err = require_user_jira_for_create(user)
    if err:
        raise JiraAutoCreateError(err, status_code=400)

    if get_registro_jira_key(registro):
        return {"ok": True, "skipped": True, "reason": "already_linked", "issue_key": get_registro_jira_key(registro)}

    creds = resolve_jira_credentials(user)
    assert creds is not None
    copy = build_registro_jira_copy(registro)
    profile_key = resolve_jira_profile_key(user)
    result = create_issue(
        profile_key,
        summary=copy["summary"],
        description=copy["description"],
        credentials=creds,
        assignee_name=creds.username,
        user=user,
    )
    if not result.get("ok"):
        raise JiraAutoCreateError(
            result.get("error") or "Falha ao criar chamado no Jira.",
            status_code=502,
        )

    issue_key = result["issue_key"]
    link = auto_link_jira_issue_to_registro(
        registro_id=registro.id,
        issue_key=issue_key,
        user=user,
    )
    registro.refresh_from_db()

    jira_sync = None
    if registro.status != SuporteClaroRegistro.STATUS_ABERTO:
        jira_sync = sync_portal_status_to_jira(registro, registro.status, user)

    return {
        "ok": True,
        "skipped": False,
        "issue_key": issue_key,
        "link": link,
        "attachments": {"ok": True, "skipped": True, "uploaded": 0, "failed": 0, "items": []},
        "jira_sync": jira_sync,
    }
