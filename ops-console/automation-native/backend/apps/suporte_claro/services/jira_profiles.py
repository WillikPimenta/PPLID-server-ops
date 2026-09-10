# -*- coding: utf-8 -*-
"""Mapeamento REST dos perfis de formalização Jira (Suporte Claro).

Defaults usam nomes/keys estáveis derivados do fluxo Selenium antigo.
Sobrescreva via settings/env após validar com createmeta (conta de serviço).
"""
from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class JiraRestProfile:
    key: str
    label: str
    project_key: str
    issuetype_name: str
    component_name: str | None
    priority_name: str
    # Campo custom "Categoria" (ex.: customfield_12345) — só planejamento.
    category_field_id: str | None
    category_value: str | None
    # ANTIFRAUDE (Processos) não expõe assignee no create screen.
    set_assignee_on_create: bool = True


def _env_str(name: str, default: str = "") -> str:
    return str(getattr(settings, name, default) or default).strip()


def get_rest_profile(key: str) -> JiraRestProfile:
    normalized = (key or "").strip().lower()
    if normalized == "processos":
        return JiraRestProfile(
            key="processos",
            label="Processos e Riscos",
            # Key real em agile.experian.com (nome: "Processos e Riscos")
            project_key=_env_str("JIRA_PROFILE_PROCESSOS_PROJECT_KEY", "ANTIFRAUDE")
            or "ANTIFRAUDE",
            issuetype_name=_env_str("JIRA_PROFILE_PROCESSOS_ISSUETYPE", "Análise de Compliance")
            or "Análise de Compliance",
            component_name=_env_str("JIRA_PROFILE_PROCESSOS_COMPONENT", "Suporte N1 Claro")
            or "Suporte N1 Claro",
            priority_name=_env_str("JIRA_PROFILE_PROCESSOS_PRIORITY", "High") or "High",
            category_field_id=None,
            category_value=None,
            set_assignee_on_create=False,
        )

    category_field = _env_str("JIRA_CATEGORY_FIELD_ID", "customfield_37898")
    return JiraRestProfile(
        key="planejamento",
        label="Planejamento (PPLID)",
        project_key=_env_str("JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY", "PPLID") or "PPLID",
        # Instância Experian usa labels em inglês (Task, não Tarefa)
        issuetype_name=_env_str("JIRA_PROFILE_PLANEJAMENTO_ISSUETYPE", "Task") or "Task",
        component_name=_env_str("JIRA_PROFILE_PLANEJAMENTO_COMPONENT", "Suporte Claro")
        or "Suporte Claro",
        priority_name=_env_str("JIRA_PROFILE_PLANEJAMENTO_PRIORITY", "High") or "High",
        category_field_id=category_field or None,
        category_value=_env_str("JIRA_PROFILE_PLANEJAMENTO_CATEGORY", "Informação") or "Informação",
        set_assignee_on_create=True,
    )


def build_issue_fields(
    profile: JiraRestProfile,
    *,
    summary: str,
    description: str,
    assignee_name: str | None = None,
    reporter_name: str | None = None,
) -> dict:
    """Monta o dict `fields` do POST /rest/api/2/issue."""
    fields: dict = {
        "project": {"key": profile.project_key},
        "issuetype": {"name": profile.issuetype_name},
        "summary": (summary or "")[:255],
        "description": description or "",
        "priority": {"name": profile.priority_name},
    }
    if profile.component_name:
        fields["components"] = [{"name": profile.component_name}]
    if profile.category_field_id and profile.category_value:
        fields[profile.category_field_id] = {"value": profile.category_value}
    # Jira Server/DC: assignee por "name" (LAN ID).
    # reporter é opcional — vários projetos não permitem setar no create screen.
    # Processos/ANTIFRAUDE: assignee fora do create screen → omitir.
    lan_assignee = (assignee_name or "").strip()
    lan_reporter = (reporter_name or "").strip()
    if profile.set_assignee_on_create and lan_assignee:
        fields["assignee"] = {"name": lan_assignee}
    if lan_reporter:
        fields["reporter"] = {"name": lan_reporter}
    return fields
