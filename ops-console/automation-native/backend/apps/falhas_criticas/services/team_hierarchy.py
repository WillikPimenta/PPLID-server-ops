# -*- coding: utf-8 -*-
"""Hierarquia de time via HC (colunas Leader + JobTitle)."""
from collections import defaultdict

from report_falhas.io.data_loader import norm_matricula, normalize_text, safe_str

from apps.falhas_criticas.models import FalhasAgent
from apps.falhas_criticas.services.user_display import resolve_user_display_name

MANAGER_KEYWORDS = (
    'gerente executivo',
    'gerente',
    'coordenador',
    'supervisor',
    'lider',
    'líder',
    'leader',
)


def _norm_key(val) -> str:
    return normalize_text(safe_str(val))


def _is_people_manager(job_title: str) -> bool:
    jt = _norm_key(job_title)
    if not jt:
        return False
    return any(kw in jt for kw in MANAGER_KEYWORDS)


def get_user_agent(user):
    if not user or not user.is_authenticated:
        return None
    mat = norm_matricula(user.username)
    if mat:
        agent = FalhasAgent.objects.filter(pk=mat).first()
        if agent:
            return agent
    display = resolve_user_display_name(user)
    if display:
        for agent in FalhasAgent.objects.filter(name__iexact=display.strip()):
            return agent
    return None


def _build_children_index(agents):
    """leader_key → [matricula_norm] (subordinados diretos)."""
    children: dict[str, list[str]] = defaultdict(list)
    mat_to_keys: dict[str, set[str]] = defaultdict(set)

    for agent in agents:
        mat = agent.matricula_norm
        if not mat:
            continue
        keys = {_norm_key(agent.name), mat, _norm_key(mat)}
        keys.discard('')
        mat_to_keys[mat] = keys
        leader = _norm_key(agent.leader_nome)
        if leader:
            children[leader].append(mat)

    return children, mat_to_keys


def get_team_matriculas(user, include_self=False) -> list[str]:
    """Retorna matrículas de todos os subordinados na cadeia Leader (HC)."""
    user_agent = get_user_agent(user)
    if not user_agent:
        return []

    agents = list(FalhasAgent.objects.exclude(leader_nome='').only(
        'matricula_norm', 'name', 'leader_nome', 'job_title',
    ))
    if not agents:
        return []

    children, mat_to_keys = _build_children_index(agents)

    anchor_keys = set(mat_to_keys.get(user_agent.matricula_norm, set()))
    anchor_keys.update({
        _norm_key(user_agent.name),
        user_agent.matricula_norm,
        _norm_key(user.username),
        _norm_key(resolve_user_display_name(user)),
    })
    anchor_keys.discard('')

    team: set[str] = set()
    pending = list(anchor_keys)
    seen_keys: set[str] = set()

    while pending:
        key = pending.pop()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        for mat in children.get(key, []):
            if mat in team:
                continue
            team.add(mat)
            pending.extend(mat_to_keys.get(mat, set()))

    if include_self:
        team.add(user_agent.matricula_norm)

    return sorted(team)


def get_team_context(user) -> dict:
    """Metadados de time para UI (/filters e /me)."""
    agent = get_user_agent(user)
    mats = get_team_matriculas(user)
    members = []
    if mats:
        for a in FalhasAgent.objects.filter(matricula_norm__in=mats).order_by('name'):
            members.append({
                'matricula': a.matricula_norm,
                'nome': a.name or a.matricula_norm,
                'job_title': a.job_title or '',
            })

    return {
        'is_manager': bool(agent and _is_people_manager(agent.job_title)),
        'job_title': (agent.job_title if agent else '') or '',
        'leader_nome': (agent.leader_nome if agent else '') or '',
        'team_count': len(mats),
        'team_matriculas': mats,
        'team_members': members,
        'can_filter_team': len(mats) > 0,
    }
