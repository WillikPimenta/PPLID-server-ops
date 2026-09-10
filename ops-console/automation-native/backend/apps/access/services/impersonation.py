"""Impersonação portal (sessão) com impacto no RBAC efetivo."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from apps.access.registry import ADM_ESCALA_IMPERSONATE
from apps.access.resolve import resolve_user_access, user_has_permission
from apps.workforce.models import Agent, UserProfile

User = get_user_model()

SESSION_LAN_KEYS = ("portal_impersonate_lan_id", "ef_impersonate_lan_id")
SESSION_BY_KEYS = ("portal_impersonate_by", "ef_impersonate_by")


def can_impersonate_portal(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return user_has_permission(user, ADM_ESCALA_IMPERSONATE)


def get_session_impersonate_lan_id(request) -> str | None:
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return None
    real_pk = str(request.user.pk)
    for lan_key, by_key in zip(SESSION_LAN_KEYS, SESSION_BY_KEYS):
        lan = request.session.get(lan_key)
        by = request.session.get(by_key)
        if not lan or by is None:
            continue
        if str(by) != real_pk:
            continue
        return str(lan).strip().lower()
    return None


def set_impersonation_session(request, user_lan_id: str) -> None:
    lan = user_lan_id.strip().lower()
    by = str(request.user.pk)
    for lan_key, by_key in zip(SESSION_LAN_KEYS, SESSION_BY_KEYS):
        request.session[lan_key] = lan
        request.session[by_key] = by
    request.session.modified = True


def clear_impersonation_session(request) -> None:
    for lan_key, by_key in zip(SESSION_LAN_KEYS, SESSION_BY_KEYS):
        request.session.pop(lan_key, None)
        request.session.pop(by_key, None)
    request.session.modified = True


def resolve_target_user(user_lan_id: str):
    lan = (user_lan_id or "").strip().lower()
    if not lan:
        return None, "Informe o usuário."

    agent = Agent.objects.filter(user_lan_id__iexact=lan, active=True).first()
    if agent:
        profile = UserProfile.objects.filter(agent=agent).select_related("user").first()
        if profile and profile.user.is_active:
            if profile.user.is_superuser:
                return None, "Não é permitido impersonar um superusuário."
            return profile.user, None

    user = User.objects.filter(username__iexact=lan, is_active=True).first()
    if user:
        if user.is_superuser:
            return None, "Não é permitido impersonar um superusuário."
        return user, None

    if agent:
        return None, "Colaborador ativo sem conta na Intranet."
    return None, "Usuário não encontrado ou inativo."


def resolve_effective_user(request):
    """Usuário cujas permissões RBAC devem valer na requisição."""
    real = getattr(request, "user", None)
    if not real or not real.is_authenticated:
        return real

    lan = get_session_impersonate_lan_id(request)
    if not lan:
        return real

    target, _error = resolve_target_user(lan)
    if not target:
        # Agente sem conta Intranet: Escala Flex continua impersonando; RBAC permanece do real.
        if Agent.objects.filter(user_lan_id__iexact=lan, active=True).exists():
            return real
        clear_impersonation_session(request)
        return real

    if target.pk == real.pk:
        clear_impersonation_session(request)
        return real

    return target


def build_access_payload(user, *, inject_impersonate: bool = False) -> dict:
    access = resolve_user_access(user)
    if inject_impersonate:
        perms = set(access.get("permissions") or [])
        perms.add(ADM_ESCALA_IMPERSONATE)
        access["permissions"] = sorted(perms)
    return access


def list_impersonation_targets() -> list[dict[str, str]]:
    """Usuários ativos da Intranet (com preferência de nome do agente)."""
    seen: set[str] = set()
    results: list[dict[str, str]] = []

    profiles = (
        UserProfile.objects.filter(user__is_active=True, agent__active=True)
        .select_related("user", "agent")
        .order_by("agent__full_name", "user__username")
    )
    for profile in profiles:
        user = profile.user
        if user.is_superuser:
            continue
        lan = (profile.agent.user_lan_id or user.username or "").strip().lower()
        if not lan or lan in seen:
            continue
        seen.add(lan)
        name = (profile.agent.full_name or "").strip()
        label = name if name else user.get_full_name() or user.username
        results.append({"value": lan, "label": label, "username": user.username})

    orphan_users = (
        User.objects.filter(is_active=True, is_superuser=False)
        .filter(profile__isnull=True)
        .order_by("first_name", "last_name", "username")
    )
    for user in orphan_users:
        lan = (user.username or "").strip().lower()
        if not lan or lan in seen:
            continue
        seen.add(lan)
        label = user.get_full_name().strip() or user.username
        results.append({"value": lan, "label": label, "username": user.username})

    results.sort(key=lambda item: item["label"].casefold())
    return results


def serialize_user_brief(user) -> dict | None:
    if not user:
        return None
    full = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return {
        "id": str(user.pk),
        "username": user.username,
        "display_name": full or user.username,
    }


def build_impersonation_preview(user_lan_id: str) -> tuple[dict | None, str | None]:
    """Retorna headcount + RBAC do alvo para o card de prévia."""
    target, error = resolve_target_user(user_lan_id)
    if error or not target:
        return None, error or "Usuário não encontrado."

    lan = (user_lan_id or "").strip().lower()
    agent = Agent.objects.filter(user_lan_id__iexact=lan, active=True).first()
    history = None
    if agent:
        history = (
            agent.history.filter(active=True, final_date__isnull=True)
            .select_related("leader", "facilitator")
            .order_by("-start_date")
            .first()
        )

    access = resolve_user_access(target)
    headcount = {
        "full_name": (agent.full_name if agent else "") or target.get_full_name() or target.username,
        "user_lan_id": (agent.user_lan_id if agent else target.username) or "",
        "email": (agent.email if agent else "") or target.email or "",
        "hire_date": agent.hire_date.isoformat() if agent and agent.hire_date else None,
        "oracle_id": agent.oracle_id if agent else "",
        "time_tracking_id": agent.time_tracking_id if agent else "",
        "active": bool(agent.active) if agent else bool(target.is_active),
        "team": history.team if history else "",
        "job_title": history.job_title if history else "",
        "job_activity": history.job_activity if history else "",
        "location": history.location if history else "",
        "journey": history.journey if history else "",
        "band": history.band if history else "",
        "leader_name": history.leader.full_name if history and history.leader else "",
        "facilitator_name": history.facilitator.full_name if history and history.facilitator else "",
    }

    return {
        "user": {
            "id": str(target.pk),
            "username": target.username,
            "display_name": target.get_full_name().strip() or target.username,
            "email": target.email or "",
        },
        "headcount": headcount,
        "access": {
            "roles": access.get("roles") or [],
            "permissions": access.get("permissions") or [],
            "scopes": access.get("scopes") or {},
            "bypass": bool(access.get("bypass")),
        },
    }, None
