"""Inferência automática de perfis a partir de Agent / AgentHistory."""

from __future__ import annotations

import unicodedata

from apps.access.constants import (
    ROLE_OP_AGENTE,
    ROLE_OP_GERENCIA,
    ROLE_OP_LIDER,
    ROLE_PLAN_ANALISTA,
    ROLE_PLAN_ASSISTENTE,
    ROLE_PLAN_GERENCIA,
    ROLE_PROC_USUARIO,
    ROLE_QUAL_CAPACITACAO,
)
from apps.workforce.models import Agent, AgentHistory

_AGENT_BACKOFFICE_I = "agente backoffice i"


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip().lower()
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _title_matches(title: str, *patterns: str) -> bool:
    normalized = _normalize_text(title)
    return any(pattern in normalized for pattern in patterns)


def get_active_history(agent: Agent) -> AgentHistory | None:
    return (
        AgentHistory.objects.filter(agent=agent, active=True, final_date__isnull=True)
        .select_related("leader")
        .first()
    )


def is_leader_lan_id(lan_id: str) -> bool:
    if not lan_id:
        return False
    return AgentHistory.objects.filter(
        leader__user_lan_id__iexact=lan_id.strip(),
        active=True,
        final_date__isnull=True,
    ).exists()


def infer_roles_for_agent(agent: Agent, *, history: AgentHistory | None = None) -> set[str]:
    """Deriva perfis RBAC a partir do histórico ativo do colaborador."""
    history = history or get_active_history(agent)
    if history is None:
        return set()

    roles: set[str] = set()
    team = _normalize_text(history.team)
    title = history.job_title or ""
    lan_id = (agent.user_lan_id or "").lower()
    is_leader = is_leader_lan_id(lan_id) or _title_matches(
        title, "lider", "supervisor", "coordenador"
    )

    # Processos
    if team == "processos":
        roles.add(ROLE_PROC_USUARIO)

    # Planejamento
    if team == "planejamento":
        if _title_matches(title, "assistente"):
            roles.add(ROLE_PLAN_ASSISTENTE)
        elif _title_matches(title, "analista"):
            roles.add(ROLE_PLAN_ANALISTA)
        elif _title_matches(title, "coordenador", "lider"):
            roles.add(ROLE_PLAN_GERENCIA)

    # Qualidade / capacitação (por cargo) — sem mapeamento Fraud/Compliance automático.
    if _title_matches(title, "capacitacao"):
        roles.add(ROLE_QUAL_CAPACITACAO)

    # Operação
    if team.startswith("operacional") or team.startswith("gerencia"):
        if _title_matches(title, "gerente") or (
            team.startswith("gerencia") and _title_matches(title, "gerente", "supervisor")
        ):
            roles.add(ROLE_OP_GERENCIA)
        elif is_leader:
            roles.add(ROLE_OP_LIDER)
        elif _normalize_text(title) == _AGENT_BACKOFFICE_I and team.startswith("operacional"):
            roles.add(ROLE_OP_AGENTE)

    # Customer Experience / auditoria — agentes operacionais sem liderança
    if not roles and team and not is_leader:
        if _normalize_text(title) == _AGENT_BACKOFFICE_I:
            roles.add(ROLE_OP_AGENTE)

    return roles


def infer_roles_for_lan_id(lan_id: str) -> set[str]:
    agent = Agent.objects.filter(user_lan_id__iexact=lan_id.strip(), active=True).first()
    if not agent:
        return set()
    return infer_roles_for_agent(agent)
