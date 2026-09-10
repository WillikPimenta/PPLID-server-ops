# -*- coding: utf-8 -*-
"""Integração opcional com Jira REST para chamados do portal."""
from __future__ import annotations

from django.conf import settings


def jira_configured() -> bool:
    return bool(
        getattr(settings, "JIRA_USERNAME", "")
        and getattr(settings, "JIRA_API_TOKEN", "")
        and getattr(settings, "JIRA_PROJECT_KEY", "")
    )


def jira_base_url() -> str:
    return getattr(settings, "JIRA_BASE_URL", "https://jira.atlassian.com").rstrip("/")


def jira_issue_url(key: str) -> str:
    return f"{jira_base_url()}/browse/{key}"


def create_jira_issue(title: str, description: str, localidade: str = "") -> dict | None:
    """Cria issue no Jira quando configurado; caso contrário retorna None."""
    if not jira_configured():
        return None
    # Integração real pode usar requests/httpx — stub seguro para dev.
    return {
        "key": f"{settings.JIRA_PROJECT_KEY}-LOCAL",
        "status": {"name": "Nova"},
        "transitions": [],
    }


def sync_jira_issue(jira_key: str) -> dict | None:
    if not jira_configured() or not jira_key:
        return None
    return {
        "status": {"name": "Em andamento"},
        "transitions": [
            {"id": "1", "name": "Iniciar", "to": {"name": "Em andamento"}},
            {"id": "2", "name": "Resolver", "to": {"name": "Resolvido"}},
        ],
    }


def apply_jira_transition(jira_key: str, transition_id: str) -> dict | None:
    if not jira_configured() or not jira_key:
        return None
    return {
        "status": {"name": "Resolvido"},
        "transitions": [],
        "message": f"Transição {transition_id} aplicada (stub).",
    }
