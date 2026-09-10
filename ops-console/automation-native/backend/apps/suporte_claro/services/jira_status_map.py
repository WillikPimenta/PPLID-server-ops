# -*- coding: utf-8 -*-
"""Mapeamento status portal → nomes de transição / status destino Jira."""
from __future__ import annotations

from django.conf import settings

from apps.suporte_claro.models import SuporteClaroRegistro


def _env_csv(name: str, defaults: list[str]) -> list[str]:
    raw = str(getattr(settings, name, "") or "").strip()
    if not raw:
        return list(defaults)
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return parts or list(defaults)


def transition_names_for_portal_status(portal_status: str) -> list[str]:
    """
    Nomes de transição a tentar (ordem de preferência).
    Status `aberto` não dispara transição (estado inicial do workflow).
    """
    status = (portal_status or "").strip().lower()
    if status == SuporteClaroRegistro.STATUS_EM_ATENDIMENTO:
        return _env_csv(
            "JIRA_TRANSITION_EM_ATENDIMENTO",
            [
                "In Progress",
                "Em andamento",
                "Start Progress",
                "Start",
                "Iniciar",
                "Iniciar progresso",
            ],
        )
    if status == SuporteClaroRegistro.STATUS_CONCLUIDO:
        return _env_csv(
            "JIRA_TRANSITION_CONCLUIDO",
            [
                "Fechada",
                "Fechar",
                "Done",
                "Concluído",
                "Concluido",
                "Resolved",
                "Resolve Issue",
                "Close Issue",
                "Close",
                "Finalizar",
                "Concluir",
                "Complete",
            ],
        )
    return []


def target_status_names_for_portal_status(portal_status: str) -> list[str]:
    """Nomes de status Jira de destino (match via transition.to.name)."""
    status = (portal_status or "").strip().lower()
    if status == SuporteClaroRegistro.STATUS_EM_ATENDIMENTO:
        return _env_csv(
            "JIRA_STATUS_EM_ATENDIMENTO",
            ["In Progress", "Em andamento", "Em Andamento", "Iniciado"],
        )
    if status == SuporteClaroRegistro.STATUS_CONCLUIDO:
        return _env_csv(
            "JIRA_STATUS_CONCLUIDO",
            [
                "Fechada",
                "Fechado",
                "Closed",
                "Done",
                "Concluído",
                "Concluido",
                "Resolved",
                "Complete",
                "Completed",
            ],
        )
    return []
