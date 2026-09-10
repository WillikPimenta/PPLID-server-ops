"""Permissões e perfil operacional (UserLoggedIn + fx_toolbar_itens)."""

from dataclasses import dataclass

from django.conf import settings

from apps.access import registry as R
from apps.workforce.models import Agent, AgentHistory, UserProfile

MENU_ITEMS = [
    {"key": "painel", "label": "Painel", "icon": "clock"},
    {"key": "escala", "label": "Escala", "icon": "calendar"},
    {"key": "historico", "label": "Histórico", "icon": "history"},
    {"key": "nh", "label": "Alteração de NH", "icon": "sort"},
    {"key": "intervalo", "label": "Intervalo", "icon": "intervalo"},
    {"key": "dashboards", "label": "Dashboards", "icon": "info"},
    {"key": "controle-sla", "label": "Controle de SLA", "icon": "sla"},
    {"key": "agente", "label": "Meu status", "icon": "user"},
]

OPERATIONAL_KEYS = {"painel", "escala", "historico"}
PLANNING_KEYS = {"painel", "escala", "nh", "intervalo", "dashboards", "controle-sla"}
AGENT_KEYS = {"agente"}

def open_access_enabled() -> bool:
    return getattr(settings, "ESCALA_FLEX_OPEN_ACCESS", False)


def all_menu_keys() -> list[str]:
    return [m["key"] for m in MENU_ITEMS]


@dataclass
class OperationalProfile:
    lan_id: str
    given_name: str
    full_name: str
    mail: str
    leader_name: str
    leader_lan_id: str
    location: str
    job_activity: str
    job_title: str
    team: str
    team_sector: str
    journey: str
    journey_entry_time: str
    journey_exit_time: str
    journey_shift: str
    active: bool
    is_admin: bool
    is_agent_backoffice: bool
    menu_keys: list[str]


def get_admin_lan_ids() -> set[str]:
    """LAN IDs admin via env (legado). Preferir perfil RBAC adm_portal na UI."""
    raw = getattr(settings, "ESCALA_FLEX_ADMIN_LAN_IDS", None)
    if not raw:
        return set()
    return {x.strip().lower() for x in raw if x.strip()}


def get_agent_for_user(user) -> Agent | None:
    if not user.is_authenticated:
        return None
    profile = UserProfile.objects.filter(user=user).select_related("agent").first()
    if profile:
        return profile.agent
    lan_id = user.username.lower()
    return Agent.objects.filter(user_lan_id__iexact=lan_id, active=True).first()


def get_active_history(agent: Agent) -> AgentHistory | None:
    return (
        AgentHistory.objects.filter(agent=agent, active=True, final_date__isnull=True)
        .select_related("leader")
        .first()
    )


def menu_keys_from_access(user) -> list[str]:
    """Deriva itens de menu a partir das permissões RBAC efetivas."""
    from apps.access.resolve import resolve_user_access

    access = resolve_user_access(user)
    if access.get("bypass"):
        return all_menu_keys()
    perms = set(access.get("permissions") or [])
    keys: list[str] = []
    mapping = {
        "painel": {R.OPERACAO_JORNADA_PAINEL_VIEW, R.PLANEJAMENTO_MONITORAMENTO_VIEW},
        "escala": {R.OPERACAO_JORNADA_ESCALA_VIEW, R.PLANEJAMENTO_ESCALAS_VIEW, R.PLANEJAMENTO_MONITORAMENTO_VIEW},
        "historico": {R.OPERACAO_HISTORICO_VIEW},
        "nh": {R.PLANEJAMENTO_MONITORAMENTO_VIEW},
        "intervalo": {R.PLANEJAMENTO_MONITORAMENTO_VIEW},
        "dashboards": {R.PLANEJAMENTO_MONITORAMENTO_DASHBOARDS_VIEW},
        "controle-sla": {R.PLANEJAMENTO_MONITORAMENTO_VIEW},
        "agente": {R.OPERACAO_STATUS_VIEW},
    }
    for key, required in mapping.items():
        if perms.intersection(required):
            keys.append(key)
    return keys


def resolve_menu_keys(team: str, is_admin: bool, is_agent_backoffice: bool, user=None) -> list[str]:
    if user is not None:
        rbac_keys = menu_keys_from_access(user)
        if rbac_keys:
            return rbac_keys
        if not open_access_enabled():
            return []
    if open_access_enabled():
        return all_menu_keys()
    if is_admin:
        return all_menu_keys()
    if is_agent_backoffice:
        return sorted(AGENT_KEYS)
    if team.startswith("Operacional"):
        return sorted(OPERATIONAL_KEYS)
    if team == "Planejamento":
        return sorted(PLANNING_KEYS)
    return sorted(OPERATIONAL_KEYS)


def _authenticated_fallback_profile(user) -> OperationalProfile:
    """Perfil mínimo para usuário autenticado sem vínculo Agent."""
    from apps.access.resolve import resolve_user_access, user_has_permission

    lan_id = user.username.lower()
    given = (user.first_name or user.username).split()[0]
    access = resolve_user_access(user)
    keys = menu_keys_from_access(user) if access.get("permissions") or access.get("bypass") else []
    is_admin = user_has_permission(user, R.ADM_ESCALA_IMPERSONATE)
    return OperationalProfile(
        lan_id=lan_id,
        given_name=given,
        full_name=user.get_full_name() or user.username,
        mail=user.email or "",
        leader_name="",
        leader_lan_id="",
        location="",
        job_activity="",
        job_title="Colaborador",
        team="",
        team_sector="",
        journey="",
        journey_entry_time="",
        journey_exit_time="",
        journey_shift="",
        active=True,
        is_admin=is_admin,
        is_agent_backoffice=False,
        menu_keys=keys,
    )


def _staff_fallback_profile(user) -> OperationalProfile:
    """Acesso total apenas para superuser sem vínculo Agent."""
    lan_id = user.username.lower()
    given = (user.first_name or user.username).split()[0]
    return OperationalProfile(
        lan_id=lan_id,
        given_name=given,
        full_name=user.get_full_name() or user.username,
        mail=user.email or "",
        leader_name="",
        leader_lan_id="",
        location="",
        job_activity="",
        job_title="Administrador",
        team="Planejamento",
        team_sector="",
        journey="",
        journey_entry_time="",
        journey_exit_time="",
        journey_shift="",
        active=True,
        is_admin=True,
        is_agent_backoffice=False,
        menu_keys=resolve_menu_keys("Planejamento", True, False),
    )


def _build_profile_from_agent(agent: Agent, *, user=None) -> OperationalProfile:
    from apps.access.resolve import user_has_permission

    history = get_active_history(agent)
    lan_id = agent.user_lan_id.lower()
    is_admin = bool(user and user_has_permission(user, R.ADM_ESCALA_IMPERSONATE))
    team = history.team if history else ""
    job_title = history.job_title if history else ""
    is_agent_backoffice = job_title == "Agente Backoffice I"

    leader_name = ""
    leader_lan_id = ""
    if history and history.leader:
        leader_name = history.leader.full_name
        leader_lan_id = history.leader.user_lan_id

    given_name = agent.full_name.split()[0] if agent.full_name else lan_id

    return OperationalProfile(
        lan_id=lan_id,
        given_name=given_name,
        full_name=agent.full_name,
        mail=agent.email or "",
        leader_name=leader_name,
        leader_lan_id=leader_lan_id.lower() if leader_lan_id else "",
        location=history.location if history else "",
        job_activity=history.job_activity if history else "",
        job_title=job_title,
        team=team,
        team_sector=history.team_sector if history else "",
        journey=history.journey if history else "",
        journey_entry_time="",
        journey_exit_time="",
        journey_shift=history.journey_shift if history else "",
        active=agent.active,
        is_admin=is_admin,
        is_agent_backoffice=is_agent_backoffice,
        menu_keys=resolve_menu_keys(team, is_admin, is_agent_backoffice, user=user),
    )


def build_operational_profile(user) -> OperationalProfile | None:
    if not user.is_authenticated:
        return None
    if getattr(user, "is_superuser", False) and not get_agent_for_user(user):
        return _staff_fallback_profile(user)
    agent = get_agent_for_user(user)
    if not agent:
        from apps.access.resolve import resolve_user_access

        access = resolve_user_access(user)
        if access.get("bypass") or access.get("permissions"):
            return _authenticated_fallback_profile(user)
        return None
    return _build_profile_from_agent(agent, user=user)


def get_session_impersonate_lan_id(request) -> str | None:
    from apps.access.services.impersonation import (
        get_session_impersonate_lan_id as portal_get_session_impersonate_lan_id,
    )

    return portal_get_session_impersonate_lan_id(request)


def clear_impersonation_session(request) -> None:
    from apps.access.services.impersonation import (
        clear_impersonation_session as portal_clear_impersonation_session,
    )

    portal_clear_impersonation_session(request)


def set_impersonation_session(request, user_lan_id: str) -> None:
    from apps.access.services.impersonation import (
        set_impersonation_session as portal_set_impersonation_session,
    )

    portal_set_impersonation_session(request, user_lan_id)


def can_impersonate(user) -> bool:
    from apps.access.resolve import user_has_permission
    from apps.access.services.impersonation import can_impersonate_portal

    if can_impersonate_portal(user) or user_has_permission(user, R.ADM_ESCALA_IMPERSONATE):
        return True
    profile = build_operational_profile(user)
    return profile is not None and profile.is_admin


def resolve_operational_profile(request) -> tuple[OperationalProfile | None, OperationalProfile | None, str | None]:
    """
    Retorna (perfil_efetivo, perfil_real, lan_id_impersonado ou None).
    """
    real_profile = build_operational_profile(request.user)
    impersonate_lan = get_session_impersonate_lan_id(request)
    if not impersonate_lan or not real_profile or not can_impersonate(request.user):
        return real_profile, real_profile, None

    agent = Agent.objects.filter(user_lan_id__iexact=impersonate_lan, active=True).first()
    if not agent:
        clear_impersonation_session(request)
        return real_profile, real_profile, None

    effective_profile = _build_profile_from_agent(agent, user=request.user)
    return effective_profile, real_profile, impersonate_lan


def get_effective_agent(request) -> Agent | None:
    _, _, impersonate_lan = resolve_operational_profile(request)
    if impersonate_lan:
        return Agent.objects.filter(user_lan_id__iexact=impersonate_lan, active=True).first()
    return get_agent_for_user(request.user)


def profile_to_dict(profile: OperationalProfile, *, extra_menu_keys: list[str] | None = None) -> dict:
    menu_by_key = {m["key"]: m for m in MENU_ITEMS}
    keys = list(profile.menu_keys)
    for key in extra_menu_keys or []:
        if key not in keys:
            keys.append(key)
    return {
        "lan_id": profile.lan_id,
        "given_name": profile.given_name,
        "full_name": profile.full_name,
        "mail": profile.mail,
        "leader": profile.leader_name,
        "leader_lan_id": profile.leader_lan_id,
        "location": profile.location,
        "job_activity": profile.job_activity,
        "job_title": profile.job_title,
        "team": profile.team,
        "team_sector": profile.team_sector,
        "journey": profile.journey,
        "journey_entry_time": profile.journey_entry_time,
        "journey_exit_time": profile.journey_exit_time,
        "journey_shift": profile.journey_shift,
        "active": profile.active,
        "is_admin": profile.is_admin,
        "is_agent_backoffice": profile.is_agent_backoffice,
        "menu_items": [
            {**menu_by_key[key], "key": key}
            for key in keys
            if key in menu_by_key
        ],
    }


def real_profile_summary(profile: OperationalProfile) -> dict:
    return {
        "lan_id": profile.lan_id,
        "full_name": profile.full_name,
        "is_admin": profile.is_admin,
    }


def can_access_escala_consulta(user) -> bool:
    from apps.access.resolve import user_has_any_permission
    from apps.access import registry as R

    if user_has_any_permission(user, R.PLANEJAMENTO_ESCALAS_VIEW, R.OPERACAO_JORNADA_ESCALA_VIEW):
        return True
    return build_operational_profile(user) is not None


def can_edit_published_schedule(profile: OperationalProfile, agent: Agent, *, user=None) -> bool:
    """Planejamento (RBAC), admin ou líder do agente podem alterar escala publicada."""
    if profile is None or agent is None:
        return False
    if user is not None:
        from apps.access.resolve import user_has_any_permission

        if user_has_any_permission(
            user,
            R.PLANEJAMENTO_ESCALAS_IMPORT,
            R.ADM_ESCALA_IMPERSONATE,
        ):
            return True
    elif profile.is_admin:
        return True
    if profile.lan_id:
        from apps.workforce.models import AgentHistory

        if AgentHistory.objects.filter(
            leader__user_lan_id__iexact=profile.lan_id,
            agent=agent,
            active=True,
            final_date__isnull=True,
        ).exists():
            return True
    return False


def scope_escala_queryset(qs, profile: OperationalProfile, *, apply_profile_scope: bool = True, user=None):
    """Restringe queryset de Escala conforme RBAC (own/team/global) e fallback legado."""
    if not apply_profile_scope:
        return qs

    if user is not None:
        from apps.escala_flex.scoping import (
            apply_jornada_escala_scope,
            jornada_escala_scope_for_user,
        )

        rbac_scope = jornada_escala_scope_for_user(user)
        if rbac_scope != "none":
            return apply_jornada_escala_scope(qs, user)

        from apps.access.resolve import user_has_any_permission

        if user_has_any_permission(
            user,
            R.PLANEJAMENTO_ESCALAS_VIEW,
            R.PLANEJAMENTO_ESCALAS_IMPORT,
            R.ADM_ESCALA_IMPERSONATE,
        ):
            return qs

    if profile is None:
        return qs.none()
    if profile.is_admin:
        return qs
    if profile.lan_id:
        agent = Agent.objects.filter(user_lan_id__iexact=profile.lan_id).first()
        if agent and AgentHistory.objects.filter(leader=agent, active=True).exists():
            return qs.filter(leader__user_lan_id__iexact=profile.lan_id)
        # Sem RBAC e sem time: apenas a própria escala (nunca headcount completo).
        return qs.filter(agent__user_lan_id__iexact=profile.lan_id)
    return qs.none()
