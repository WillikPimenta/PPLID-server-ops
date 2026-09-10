# -*- coding: utf-8 -*-
"""Escopo de dados da Produtividade Operacional (own / team / global)."""

from __future__ import annotations

from apps.access import registry as R
from apps.access.constants import SCOPE_GLOBAL, SCOPE_OWN, SCOPE_TEAM
from apps.access.resolve import resolve_user_access
from apps.access.roles import default_scope_for_role, permissions_for_role

_SCOPE_RANK = {
    SCOPE_OWN: 1,
    SCOPE_TEAM: 2,
    SCOPE_GLOBAL: 3,
}


def _user_lan_id(user) -> str:
    return (getattr(user, "username", "") or "").strip().lower()


def produtividade_scope_for_user(user) -> str:
    """Maior escopo entre perfis que têm indicadores.produtividade.view."""
    if not user or not getattr(user, "is_authenticated", False):
        return "none"

    access = resolve_user_access(user)
    if access.get("bypass"):
        return SCOPE_GLOBAL

    perms = set(access.get("permissions") or [])
    if R.INDICADORES_PRODUTIVIDADE_VIEW not in perms:
        return "none"

    roles = access.get("roles") or []
    widest = SCOPE_OWN
    found = False
    for role in roles:
        role_perms = permissions_for_role(role)
        if R.INDICADORES_PRODUTIVIDADE_VIEW not in role_perms:
            continue
        found = True
        scope = default_scope_for_role(role)
        if _SCOPE_RANK.get(scope, 0) > _SCOPE_RANK.get(widest, 0):
            widest = scope

    if not found:
        # Permissão veio de bypass parcial / combinação — default safe
        return SCOPE_OWN
    return widest


def _truthy(value) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes", "on")


def direct_report_matriculas(user) -> set[str]:
    """Matrículas dos subordinados diretos (AgentHistory.active sem final_date)."""
    from apps.escala_flex.services.permissions import get_agent_for_user
    from apps.workforce.models import AgentHistory

    lan = _user_lan_id(user)
    agent = get_agent_for_user(user)
    leader_lan = (agent.user_lan_id if agent else lan) or ""
    leader_lan = leader_lan.strip().lower()
    if not leader_lan:
        return set()

    mats = set(
        AgentHistory.objects.filter(
            leader__user_lan_id__iexact=leader_lan,
            active=True,
            final_date__isnull=True,
        ).values_list("agent__user_lan_id", flat=True)
    )
    return {m.strip().lower() for m in mats if m and str(m).strip()}


def matriculas_for_scope(user) -> set[str] | None:
    """None = sem filtro automático. Set vazio = nenhum agente no escopo.

    Op. Líder (team) não restringe sozinho: o time só entra com filtro
    ``meu_time`` (``_meu_time_mats``). Op. Agente (own) continua preso à própria matrícula.
    """
    from apps.escala_flex.services.permissions import get_agent_for_user

    scope = produtividade_scope_for_user(user)
    if scope in (SCOPE_GLOBAL, SCOPE_TEAM):
        return None
    if scope == "none":
        return set()

    lan = _user_lan_id(user)
    agent = get_agent_for_user(user)

    if scope == SCOPE_OWN:
        if agent and agent.user_lan_id:
            return {agent.user_lan_id.strip().lower()}
        return {lan} if lan else set()

    return None


def apply_produtividade_scope(qs, user):
    """Restringe queryset de ProductivityRecord ao escopo do usuário."""
    mats = matriculas_for_scope(user)
    if mats is None:
        return qs
    if not mats:
        return qs.none()
    return qs.filter(matricula_norm__in=mats)


def constrain_filter_params(user, params: dict) -> dict:
    """Remove/força params que ampliariam o escopo além do permitido."""
    out = dict(params)
    scope = produtividade_scope_for_user(user)
    mats = matriculas_for_scope(user)

    # "Meu time": global/team estreita aos subordinados diretos (só se marcado).
    if _truthy(out.get("meu_time")):
        if scope == SCOPE_OWN:
            out["meu_time"] = False
            out.pop("_meu_time_mats", None)
        else:
            team_mats = direct_report_matriculas(user)
            out["_meu_time_mats"] = sorted(team_mats)
    else:
        out.pop("_meu_time_mats", None)

    if mats is None:
        return out

    if scope == SCOPE_OWN:
        own = next(iter(mats), None)
        if own:
            out["matricula"] = own
        out["team"] = None
        out["meu_time"] = False
        out.pop("_meu_time_mats", None)
        return out

    return out


def matricula_in_scope(user, matricula: str) -> bool:
    """Detalhe do agente: own restringe; global/team liberam (time é filtro opcional)."""
    mats = matriculas_for_scope(user)
    if mats is None:
        return True
    return (matricula or "").strip().lower() in mats
