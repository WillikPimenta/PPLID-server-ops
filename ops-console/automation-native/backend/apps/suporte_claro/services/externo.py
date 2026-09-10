# -*- coding: utf-8 -*-
from __future__ import annotations

from django.conf import settings

from apps.suporte_claro.models import SuporteClaroRegistro

CHAMADO_SISTEMA_LABELS = dict(SuporteClaroRegistro.CHAMADO_SISTEMA_CHOICES)


def resolve_chamado_url(
    sistema: str,
    codigo: str = "",
    url: str = "",
) -> str:
    """Monta link do chamado externo (Jira / ServiceNow) quando possível."""
    direct = (url or "").strip()
    if direct:
        return direct
    code = (codigo or "").strip()
    if not sistema or not code:
        return ""
    if sistema == SuporteClaroRegistro.CHAMADO_JIRA:
        base = getattr(settings, "JIRA_BASE_URL", "").rstrip("/")
        if base:
            return f"{base}/browse/{code}"
    if sistema == SuporteClaroRegistro.CHAMADO_SERVICE:
        base = getattr(settings, "SERVICENOW_BASE_URL", "").rstrip("/")
        if base:
            return (
                f"{base}/nav_to.do?uri=task.do?"
                f"sysparm_query=number={code}"
            )
    return ""


def chamado_display_label(sistema: str, codigo: str) -> str:
    label = CHAMADO_SISTEMA_LABELS.get(sistema, "")
    code = (codigo or "").strip()
    if label and code:
        return f"{label} · {code}"
    return label or code or ""
