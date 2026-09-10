# -*- coding: utf-8 -*-
"""Escopo de dados da escala publicada (own / team / global) via RBAC."""

from __future__ import annotations

from django.db.models import Q

from apps.access import registry as R
from apps.access.constants import SCOPE_GLOBAL, SCOPE_OWN, SCOPE_TEAM
from apps.access.resolve import resolve_user_access
from apps.access.roles import default_scope_for_role, permissions_for_role

_SCOPE_RANK = {
    SCOPE_OWN: 1,
    SCOPE_TEAM: 2,
    SCOPE_GLOBAL: 3,
}

_ESCALA_VIEW_PERMS = frozenset(
    {
        R.OPERACAO_JORNADA_ESCALA_VIEW,
        R.PLANEJAMENTO_ESCALAS_VIEW,
        R.PLANEJAMENTO_ESCALAS_IMPORT,
        R.PLANEJAMENTO_ESCALAS_GENERATE,
        R.PLANEJAMENTO_ESCALAS_PUBLISH,
    }
)


def _user_lan_id(user) -> str:
    from apps.escala_flex.services.permissions import get_agent_for_user

    agent = get_agent_for_user(user)
    if agent and agent.user_lan_id:
        return agent.user_lan_id.strip().lower()
    return (getattr(user, "username", "") or "").strip().lower()


def jornada_escala_scope_for_user(user) -> str:
    """Maior escopo entre perfis com permissão de ver escala (operação ou planejamento)."""
    if not user or not getattr(user, "is_authenticated", False):
        return "none"

    access = resolve_user_access(user)
    if access.get("bypass"):
        return SCOPE_GLOBAL

    perms = set(access.get("permissions") or [])
    if not (_ESCALA_VIEW_PERMS & perms):
        return "none"

    # Planejamento / import → visão global da escala publicada
    if (
        R.PLANEJAMENTO_ESCALAS_VIEW in perms
        or R.PLANEJAMENTO_ESCALAS_IMPORT in perms
        or R.PLANEJAMENTO_ESCALAS_GENERATE in perms
        or R.PLANEJAMENTO_ESCALAS_PUBLISH in perms
    ):
        return SCOPE_GLOBAL

    roles = access.get("roles") or []
    widest = SCOPE_OWN
    found = False
    for role in roles:
        role_perms = permissions_for_role(role)
        if not (_ESCALA_VIEW_PERMS & role_perms):
            continue
        found = True
        scope = default_scope_for_role(role)
        if _SCOPE_RANK.get(scope, 0) > _SCOPE_RANK.get(widest, 0):
            widest = scope

    if not found:
        return SCOPE_OWN
    return widest


def apply_jornada_escala_scope(qs, user):
    """Restringe queryset de Escala ao escopo RBAC do usuário."""
    scope = jornada_escala_scope_for_user(user)
    if scope == SCOPE_GLOBAL:
        return qs
    if scope == "none":
        return qs.none()

    lan = _user_lan_id(user)
    if not lan:
        return qs.none()

    if scope == SCOPE_OWN:
        return qs.filter(agent__user_lan_id__iexact=lan)

    # team: própria escala + subordinados diretos
    return qs.filter(
        Q(agent__user_lan_id__iexact=lan) | Q(leader__user_lan_id__iexact=lan)
    )


def _query_params_as_dict(params) -> dict:
    """Normaliza QueryDict (valores únicos) ou mapping genérico."""
    if hasattr(params, "dict"):
        return params.dict()
    return dict(params)


def constrain_escala_query_params(user, params) -> dict:
    """Impede que query params ampliem o escopo além do permitido."""
    out = _query_params_as_dict(params)
    scope = jornada_escala_scope_for_user(user)
    if scope not in (SCOPE_OWN, "none"):
        return out

    lan = _user_lan_id(user)
    if scope == SCOPE_OWN and lan:
        out["agent_lan_id"] = lan
        out.pop("leader_lan_id", None)
        out.pop("equipe", None)
        out.pop("search", None)
    return out
