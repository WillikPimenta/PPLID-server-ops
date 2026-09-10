"""Escopo RBAC (own / team / global) para contestação operacional."""

from __future__ import annotations

from django.db.models import Q

from apps.access import registry as R
from apps.access.constants import SCOPE_GLOBAL, SCOPE_OWN, SCOPE_TEAM
from apps.access.resolve import resolve_user_access
from apps.access.roles import default_scope_for_role, permissions_for_role
from apps.workforce.models import Agent, AgentHistory

_SCOPE_RANK = {
    SCOPE_OWN: 1,
    SCOPE_TEAM: 2,
    SCOPE_GLOBAL: 3,
}


def _user_lan(user) -> str:
    from apps.escala_flex.services.permissions import get_agent_for_user

    agent = get_agent_for_user(user)
    if agent and agent.user_lan_id:
        return agent.user_lan_id.strip().lower()
    return (getattr(user, "username", "") or "").strip().lower()


def _agent_identifiers(agent: Agent) -> set[str]:
    ids: set[str] = set()
    for value in (agent.user_lan_id, agent.time_tracking_id, agent.oracle_id):
        normalized = (value or "").strip().lower()
        if normalized:
            ids.add(normalized)
    return ids


def contestacao_scope_for_user(user) -> str:
    """Maior escopo entre perfis com ``operacao.contestacao.view``."""
    if not user or not getattr(user, "is_authenticated", False):
        return "none"

    access = resolve_user_access(user)
    if access.get("bypass"):
        return SCOPE_GLOBAL

    perms = set(access.get("permissions") or [])
    if R.OPERACAO_CONTESTACAO_VIEW not in perms:
        return "none"

    widest = SCOPE_OWN
    found = False
    for role in access.get("roles") or []:
        role_perms = permissions_for_role(role)
        if R.OPERACAO_CONTESTACAO_VIEW not in role_perms:
            continue
        found = True
        scope = default_scope_for_role(role)
        if _SCOPE_RANK.get(scope, 0) > _SCOPE_RANK.get(widest, 0):
            widest = scope

    return widest if found else SCOPE_OWN


def _team_agent_identifiers_recursive(lan: str) -> set[str]:
    """Matrículas na cadeia de liderança abaixo do usuário (históricos ativos)."""
    leader_agents = list(Agent.objects.filter(user_lan_id__iexact=lan).only("id"))
    if not leader_agents:
        return set()

    ids: set[str] = set()
    frontier = {agent.pk for agent in leader_agents}
    seen_leaders: set[int] = set()

    while frontier:
        current = frontier - seen_leaders
        if not current:
            break
        seen_leaders.update(current)

        histories = (
            AgentHistory.objects.filter(
                leader_id__in=current,
                active=True,
                final_date__isnull=True,
            )
            .select_related("agent")
            .only(
                "agent_id",
                "agent__user_lan_id",
                "agent__time_tracking_id",
                "agent__oracle_id",
            )
        )
        next_frontier: set[int] = set()
        for history in histories:
            agent = history.agent
            ids.update(_agent_identifiers(agent))
            if AgentHistory.objects.filter(
                leader_id=agent.pk,
                active=True,
                final_date__isnull=True,
            ).exists():
                next_frontier.add(agent.pk)
        frontier = next_frontier

    return ids


def team_agent_identifiers_for_user(user) -> set[str] | None:
    """Identificadores de agentes visíveis pelo escopo RBAC.

    ``None`` = escopo global (sem filtro de equipe).
    ``set()`` vazio = usuário sem agentes no escopo.
    """
    scope = contestacao_scope_for_user(user)
    if scope == SCOPE_GLOBAL:
        return None
    if scope == "none":
        return set()

    lan = _user_lan(user)
    if not lan:
        return set()

    if scope == SCOPE_OWN:
        agent = Agent.objects.filter(user_lan_id__iexact=lan).first()
        if not agent:
            return {lan}
        return _agent_identifiers(agent) or {lan}

    if scope == SCOPE_TEAM:
        return _team_agent_identifiers_recursive(lan)

    return set()


def apply_usuario_scope(qs, user):
    """Restringe ``AuditoriaFalhaCadastro`` ao escopo RBAC do usuário."""
    team = team_agent_identifiers_for_user(user)
    if team is None:
        return qs
    if not team:
        return qs.none()

    usuario_q = Q()
    for ident in team:
        usuario_q |= Q(usuario__iexact=ident)
    return qs.filter(usuario_q)
