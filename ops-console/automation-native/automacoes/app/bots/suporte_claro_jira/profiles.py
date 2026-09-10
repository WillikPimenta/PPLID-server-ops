# -*- coding: utf-8 -*-
"""Perfis de criação de issue Jira — Planejamento (PPLID) vs Processos."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class JiraCreateProfile:
    key: str
    label: str
    project_search: str
    project_option: str
    issue_type: str
    component_search: str
    component_option: str
    priority: str
    category: str | None


PROFILES: dict[str, JiraCreateProfile] = {
    "planejamento": JiraCreateProfile(
        key="planejamento",
        label="Planejamento (PPLID)",
        project_search="pplid",
        project_option="Planejamento IDF",
        issue_type="Tarefa",
        component_search="suporte",
        component_option="Suporte Claro",
        priority="High",
        category="Informação",
    ),
    "processos": JiraCreateProfile(
        key="processos",
        label="Processos e Riscos",
        project_search="processo",
        project_option="Processos e Riscos",
        issue_type="Análise de Compliance",
        component_search="suporte n1",
        component_option="Suporte N1 Claro",
        priority="High",
        category=None,
    ),
}


def get_profile(key: str) -> JiraCreateProfile:
    normalized = (key or "").strip().lower()
    if normalized in PROFILES:
        return PROFILES[normalized]
    return PROFILES["planejamento"]
