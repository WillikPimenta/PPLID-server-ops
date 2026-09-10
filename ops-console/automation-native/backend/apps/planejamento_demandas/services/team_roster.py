"""Mapeamento LAN ID Jira ↔ colaborador (headcount)."""

from __future__ import annotations

from django.utils import timezone

from apps.planejamento_demandas.models import JiraDemanda
from apps.planejamento_demandas.services.projects import jira_demandas_team_lan_ids
from apps.planejamento_demandas.services.queue_metrics import STALE_IDLE_DAYS
from apps.workforce.models import Agent


def _lan_ids() -> list[str]:
    return jira_demandas_team_lan_ids()


def agents_by_lan() -> dict[str, Agent]:
    ids = _lan_ids()
    if not ids:
        return {}
    by_lan: dict[str, Agent] = {}
    for agent in Agent.objects.filter(user_lan_id__in=ids):
        by_lan[agent.user_lan_id.upper()] = agent
    missing = [i for i in ids if i.upper() not in by_lan]
    if missing:
        for agent in Agent.objects.filter(user_lan_id__iregex=r"^(" + "|".join(missing) + ")$"):
            by_lan[agent.user_lan_id.upper()] = agent
    return by_lan


def resolve_person(lan_id: str, *, agents: dict[str, Agent] | None = None) -> dict:
    key = (lan_id or "").strip().upper()
    if not key:
        return {
            "lan_id": "",
            "display_name": "",
            "agent_id": None,
            "agent_name": "",
            "mapped": False,
            "in_team": False,
        }
    roster = agents if agents is not None else agents_by_lan()
    agent = roster.get(key)
    team = {u.upper() for u in _lan_ids()}
    return {
        "lan_id": key,
        "display_name": agent.full_name if agent else key,
        "agent_id": str(agent.id) if agent else None,
        "agent_name": agent.full_name if agent else "",
        "mapped": agent is not None,
        "in_team": key in team,
    }


def team_roster_stats(*, queryset) -> list[dict]:
    team_ids = _lan_ids()
    agents = agents_by_lan()
    stats: dict[str, dict] = {}

    for lan in team_ids:
        key = lan.upper()
        stats[key] = {
            **resolve_person(key, agents=agents),
            "assigned_open": 0,
            "reported_open": 0,
            "total_open": 0,
            "stale_assigned": 0,
            "oldest_assigned_days": None,
        }

    stale_cutoff = timezone.now() - timezone.timedelta(days=STALE_IDLE_DAYS)
    now = timezone.now()

    for key in stats:
        assigned_qs = queryset.filter(
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            assignee_username__iexact=key,
        )
        stats[key]["assigned_open"] = assigned_qs.count()
        stats[key]["reported_open"] = queryset.filter(
            status_kind=JiraDemanda.STATUS_KIND_OPEN,
            reporter_username__iexact=key,
        ).count()
        stats[key]["total_open"] = stats[key]["assigned_open"]
        stats[key]["stale_assigned"] = assigned_qs.filter(
            updated_at_jira__isnull=False,
            updated_at_jira__lte=stale_cutoff,
        ).count()
        oldest_created = (
            assigned_qs.exclude(created_at_jira__isnull=True)
            .order_by("created_at_jira")
            .values_list("created_at_jira", flat=True)
            .first()
        )
        if oldest_created is not None:
            stats[key]["oldest_assigned_days"] = max(0, (now - oldest_created).days)

    order = {lan.upper(): idx for idx, lan in enumerate(team_ids)}
    max_assigned = max((s["assigned_open"] for s in stats.values()), default=0)
    for key in stats:
        stats[key]["load_percent"] = (
            round(stats[key]["assigned_open"] / max_assigned * 100) if max_assigned else 0
        )
    return sorted(
        stats.values(),
        key=lambda x: (-x["assigned_open"], -x["stale_assigned"], order.get(x["lan_id"], 999)),
    )
