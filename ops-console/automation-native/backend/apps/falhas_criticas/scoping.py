# -*- coding: utf-8 -*-
"""Escopo de localidade / matrícula por grupo de usuário."""
from django.http import QueryDict

from apps.access.constants import ROLE_OP_AGENTE, role_group_name
from apps.falhas_criticas.constants import (
    GROUP_GLOBAL,
    GROUP_TO_LOCALIDADE,
)
from report_falhas.io.data_loader import norm_matricula


def _empty_scope(username: str = '', **overrides):
    base = {
        'scope': 'anonymous',
        'localidade_forcada': None,
        'matricula_forcada': None,
        'can_sync': False,
        'can_choose_localidade': False,
        'username': username,
    }
    base.update(overrides)
    return base


def _resolve_own_matricula(user) -> str | None:
    """Matrícula do próprio usuário (HC Falhas ou username)."""
    from apps.falhas_criticas.services.team_hierarchy import get_user_agent

    agent = get_user_agent(user)
    if agent and agent.matricula_norm:
        return agent.matricula_norm
    mat = norm_matricula(getattr(user, 'username', '') or '')
    return mat or None


def get_user_scope(user):
    """Retorna metadados de escopo para UI e API."""
    if not user or not user.is_authenticated:
        return _empty_scope()

    group_names = set(user.groups.values_list('name', flat=True))
    username = user.username

    if user.is_superuser or GROUP_GLOBAL in group_names:
        return _empty_scope(
            username,
            scope='global',
            can_sync=True,
            can_choose_localidade=True,
        )

    for group_name, loc in GROUP_TO_LOCALIDADE.items():
        if group_name in group_names:
            return _empty_scope(
                username,
                scope=group_name,
                localidade_forcada=loc,
            )

    # Agente operacional: só a própria matrícula
    if role_group_name(ROLE_OP_AGENTE) in group_names:
        mat = _resolve_own_matricula(user)
        if not mat:
            return _empty_scope(
                username,
                scope='own',
                localidade_forcada='__BLOCKED__',
                matricula_forcada='__BLOCKED__',
            )
        return _empty_scope(
            username,
            scope='own',
            matricula_forcada=mat,
        )

    # Usuário autenticado sem grupo reconhecido: sem acesso a dados
    return _empty_scope(
        username,
        scope='none',
        localidade_forcada='__BLOCKED__',
        matricula_forcada='__BLOCKED__',
    )


def _querydict_to_dict(qd) -> dict:
    """QueryDict → dict com valores escalares (QueryDict.get, não listas)."""
    if qd is None:
        return {}
    if isinstance(qd, dict) and not hasattr(qd, 'getlist'):
        out = {}
        for k, v in qd.items():
            if isinstance(v, (list, tuple)):
                out[k] = v[0] if v else ''
            else:
                out[k] = v
        return out
    return {k: qd.get(k) for k in qd.keys()}


def scoped_query_params(user, query_params):
    """Aplica localidade/matrícula forçada do grupo sobre os parâmetros da request."""
    scope = get_user_scope(user)
    if isinstance(query_params, QueryDict):
        params = query_params.copy()
    else:
        params = QueryDict(mutable=True)
        for k, v in (query_params or {}).items():
            if isinstance(v, (list, tuple)):
                params[k] = v[0] if v else ''
            else:
                params[k] = v

    forced = scope.get('localidade_forcada')
    if forced == '__BLOCKED__':
        params['localidade'] = '__BLOCKED__'
    elif forced:
        params['localidade'] = forced

    mat_forced = scope.get('matricula_forcada')
    if mat_forced == '__BLOCKED__':
        params['matricula'] = '__BLOCKED__'
        params['meu_time'] = 'false'
    elif mat_forced:
        params['matricula'] = mat_forced
        params['meu_time'] = 'false'

    params = _querydict_to_dict(params)
    if mat_forced:
        params.pop('team_mats', None)
        params['meu_time'] = False
    else:
        params = enrich_params_with_team(user, params)
    return params, scope


def enrich_params_with_team(user, params: dict) -> dict:
    """Resolve meu_time=true → lista team_mats (subordinados HC)."""
    if str(params.get('meu_time', '')).lower() not in ('true', '1', 'yes'):
        return params
    from apps.falhas_criticas.services.team_hierarchy import get_team_matriculas

    params = dict(params)
    params['team_mats'] = get_team_matriculas(user)
    params.pop('matricula', None)
    return params


def filter_localidades_for_user(user, localidades):
    """Restringe opções de localidade exibidas no filtro."""
    scope = get_user_scope(user)
    if scope.get('scope') == 'own':
        return ['Geral']
    forced = scope.get('localidade_forcada')
    if forced and forced != '__BLOCKED__':
        return [forced]
    if scope.get('scope') == 'global':
        out = ['Geral']
        for loc in localidades:
            if loc and loc not in out:
                out.append(loc)
        return out
    return ['Geral']
