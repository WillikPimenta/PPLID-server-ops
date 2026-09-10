from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

from apps.workforce.models import Agent, UserProfile


SYSTEM_AGENT_LAN_ID = "c000000a"


@dataclass(frozen=True)
class AgentResolution:
    agent: Agent | None
    status: str
    matched_by: str = ""


def system_agent_lan_id() -> str:
    return str(
        getattr(settings, "QUALIDADE_SYSTEM_AGENT_LAN_ID", SYSTEM_AGENT_LAN_ID)
        or SYSTEM_AGENT_LAN_ID
    ).strip()


def is_system_agent(agent: Agent | None) -> bool:
    return bool(
        agent
        and (agent.user_lan_id or "").strip().casefold()
        == system_agent_lan_id().casefold()
    )


def get_or_create_system_agent() -> Agent:
    """Garante a opção técnica SISTEMA nos seletores de usuário auditado."""
    agent, _created = Agent.objects.get_or_create(
        user_lan_id=system_agent_lan_id(),
        defaults={"full_name": "SISTEMA", "active": True},
    )
    return agent


def _unique_match(**lookup) -> AgentResolution | None:
    matches = list(Agent.objects.filter(**lookup).order_by("id")[:2])
    if len(matches) == 1:
        return AgentResolution(matches[0], "matched")
    if len(matches) > 1:
        return AgentResolution(None, "ambiguous")
    return None


def resolve_agent_reference(value: object) -> AgentResolution:
    """Resolve texto legado por igualdade exata, incluindo agentes inativos."""
    text = str(value or "").strip()
    if not text:
        return AgentResolution(None, "empty")

    if text.casefold() == "sistema":
        result = _unique_match(user_lan_id__iexact=system_agent_lan_id())
        if result:
            return AgentResolution(result.agent, result.status, "system_lan_id")

    for matched_by, lookup in (
        ("user_lan_id", {"user_lan_id__iexact": text}),
        ("email", {"email__iexact": text}),
        ("full_name", {"full_name__iexact": text}),
    ):
        result = _unique_match(**lookup)
        if result:
            return AgentResolution(result.agent, result.status, matched_by)
    return AgentResolution(None, "not_found")


def resolve_agent_for_user(user) -> Agent | None:
    """Resolve a conta autenticada para AGENT sem considerar Agent.active."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    profile = UserProfile.objects.filter(user=user).select_related("agent").first()
    if profile:
        return profile.agent
    username = str(getattr(user, "username", "") or "").strip()
    if not username:
        return None
    return Agent.objects.filter(user_lan_id__iexact=username).first()
