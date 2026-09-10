"""Lista agentes/usuários com determinada permissão portal."""

from __future__ import annotations

from django.contrib.auth import get_user_model

from apps.access.constants import role_group_name
from apps.access.inference import get_active_history, infer_roles_for_agent
from apps.access.resolve import load_manual_role_assignments
from apps.access.roles import ROLE_DEFINITIONS
from apps.escala_flex.services.permissions import get_agent_for_user
from apps.workforce.models import Agent

User = get_user_model()


def roles_with_permission(permission: str) -> set[str]:
    return {
        role
        for role, definition in ROLE_DEFINITIONS.items()
        if permission in definition.get("permissions", set())
    }


def agents_with_any_permission(*permissions: str) -> list[dict[str, str]]:
    """Retorna opções {value, label} deduplicadas para qualquer uma das permissões."""
    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for permission in permissions:
        for item in agents_with_permission(permission):
            key = item["value"].casefold()
            if key in seen:
                continue
            seen.add(key)
            results.append(item)
    results.sort(key=lambda item: item["label"].casefold())
    return results


def agents_with_permission(permission: str) -> list[dict[str, str]]:
    """
    Retorna opções {value, label} com o nome dos agentes que têm acesso
    via papéis (grupo Django, inferência de cargo ou atribuição manual).
    """
    roles = roles_with_permission(permission)
    if not roles:
        return []

    seen: set[str] = set()
    results: list[dict[str, str]] = []

    def add(name: str) -> None:
        label = (name or "").strip()
        if not label:
            return
        key = label.casefold()
        if key in seen:
            return
        seen.add(key)
        results.append({"value": label, "label": label})

    group_names = [role_group_name(role) for role in roles]
    for user in (
        User.objects.filter(is_active=True, groups__name__in=group_names)
        .distinct()
        .only("id", "username", "first_name", "last_name")
    ):
        agent = get_agent_for_user(user)
        if agent and (agent.full_name or "").strip():
            add(agent.full_name)
        else:
            full = f"{user.first_name} {user.last_name}".strip()
            add(full or user.username)

    for agent in (
        Agent.objects.filter(active=True)
        .exclude(full_name__exact="")
        .only("id", "full_name", "user_lan_id")
    ):
        history = get_active_history(agent)
        if history is None:
            continue
        if infer_roles_for_agent(agent, history=history) & roles:
            add(agent.full_name)

    for role, lan_ids in load_manual_role_assignments().items():
        if role not in roles:
            continue
        for lan in lan_ids:
            agent = (
                Agent.objects.filter(user_lan_id__iexact=lan, active=True)
                .exclude(full_name__exact="")
                .only("full_name")
                .first()
            )
            if agent:
                add(agent.full_name)

    results.sort(key=lambda item: item["label"].casefold())
    return results


def users_with_permission(permission: str) -> list[dict[str, int | str]]:
    """
    Retorna opções {id, label} com usuários ativos que têm a permissão
    via papéis (grupo Django, inferência de cargo ou atribuição manual).
    """
    roles = roles_with_permission(permission)
    if not roles:
        return []

    return _users_with_roles(roles)


def users_with_role(role: str) -> list[dict[str, int | str]]:
    """Retorna usuários ativos vinculados ao perfil RBAC exato informado."""
    cleaned = (role or "").strip()
    if not cleaned:
        return []
    return _users_with_roles({cleaned})


def _users_with_roles(roles: set[str]) -> list[dict[str, int | str]]:
    from apps.falhas_criticas.services.user_display import resolve_user_display_name

    users_by_id: dict[int, object] = {}

    def add_user(user) -> None:
        if not user or not getattr(user, "is_active", False):
            return
        users_by_id[user.pk] = user

    group_names = [role_group_name(role) for role in roles]
    for user in (
        User.objects.filter(is_active=True, groups__name__in=group_names)
        .distinct()
        .only("id", "username", "first_name", "last_name", "is_active")
    ):
        add_user(user)

    lan_ids: set[str] = set()
    for agent in (
        Agent.objects.filter(active=True)
        .exclude(user_lan_id__exact="")
        .only("id", "full_name", "user_lan_id")
    ):
        history = get_active_history(agent)
        if history is None:
            continue
        if infer_roles_for_agent(agent, history=history) & roles:
            lan_ids.add(agent.user_lan_id.strip())

    for role, assigned_lans in load_manual_role_assignments().items():
        if role not in roles:
            continue
        for lan in assigned_lans:
            cleaned = (lan or "").strip()
            if cleaned:
                lan_ids.add(cleaned)

    if lan_ids:
        from django.db.models import Q

        query = Q()
        for lan in lan_ids:
            query |= Q(username__iexact=lan)
        for user in User.objects.filter(is_active=True).filter(query).only(
            "id", "username", "first_name", "last_name", "is_active"
        ):
            add_user(user)

    results: list[dict[str, int | str]] = []
    for user in users_by_id.values():
        agent = get_agent_for_user(user)
        if agent and (agent.full_name or "").strip():
            label = agent.full_name.strip()
        else:
            display = resolve_user_display_name(user)
            if display and display != user.username:
                label = display
            else:
                full = f"{user.first_name} {user.last_name}".strip()
                label = full or user.username
        results.append({"id": str(user.pk), "label": label})

    results.sort(key=lambda item: str(item["label"]).casefold())
    return results
