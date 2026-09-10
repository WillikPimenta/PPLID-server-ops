"""Elegibilidade diária por cargo e vigência de AgentHistory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

from django.db.models import Q

from apps.workforce.models import Agent, AgentHistory

from .constants import TARGET_JOB_TITLE


def normalize_job_title(value: str | None) -> str:
    """Normaliza cargo: casefold, trim e colapso de espaços (sem substring)."""
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text.casefold()


def job_title_matches(value: str | None, target: str = TARGET_JOB_TITLE) -> bool:
    return normalize_job_title(value) == normalize_job_title(target)


@dataclass(frozen=True)
class EligibleAgentDay:
    agent: Agent
    day: date
    history: AgentHistory
    warning: str = ""


def _month_days(reference_month: date) -> list[date]:
    start = reference_month.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1)
    else:
        end = date(start.year, start.month + 1, 1)
    days: list[date] = []
    current = start
    while current < end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _history_covers_day(history: AgentHistory, day: date) -> bool:
    if history.start_date > day:
        return False
    if history.final_date is not None and history.final_date < day:
        return False
    return True


def _agent_active_on_day(agent: Agent, history: AgentHistory, day: date) -> bool:
    if agent.hire_date and agent.hire_date > day:
        return False
    if not history.active:
        return False
    if not _history_covers_day(history, day):
        return False
    return True


def load_histories_for_month(
    *,
    reference_month: date,
    target_job_title: str = TARGET_JOB_TITLE,
) -> list[AgentHistory]:
    days = _month_days(reference_month)
    month_start = days[0]
    month_end = days[-1]
    title_norm = normalize_job_title(target_job_title)

    qs = (
        AgentHistory.objects.select_related("agent", "leader")
        .filter(start_date__lte=month_end)
        .filter(Q(final_date__isnull=True) | Q(final_date__gte=month_start))
        .order_by("agent_id", "-start_date", "-created_at")
    )
    matched: list[AgentHistory] = []
    for history in qs:
        if job_title_matches(history.job_title, target_job_title):
            matched.append(history)
        elif not history.job_title:
            # Mantém para avisar depois se for o histórico vigente
            continue
    # Filtra também por normalização quando o DB não tem casefold
    return [
        h
        for h in matched
        if normalize_job_title(h.job_title) == title_norm
    ]


def history_for_agent_day(
    histories: Iterable[AgentHistory],
    agent_id,
    day: date,
) -> AgentHistory | None:
    candidates = [
        h
        for h in histories
        if h.agent_id == agent_id and _history_covers_day(h, day)
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda h: (h.start_date, h.created_at), reverse=True)
    return candidates[0]


def load_eligible_agent_days(
    *,
    reference_month: date,
    target_job_title: str = TARGET_JOB_TITLE,
) -> tuple[list[EligibleAgentDay], list[dict]]:
    """
    Retorna (dias elegíveis, avisos).
    Um agente só entra nos dias em que o histórico com o cargo alvo cobre a data
    e está ativo naquela data.
    """
    days = _month_days(reference_month)
    histories = load_histories_for_month(
        reference_month=reference_month,
        target_job_title=target_job_title,
    )
    by_agent: dict = {}
    for history in histories:
        by_agent.setdefault(history.agent_id, []).append(history)

    warnings: list[dict] = []
    results: list[EligibleAgentDay] = []

    for agent_id, agent_histories in by_agent.items():
        agent = agent_histories[0].agent
        for day in days:
            history = history_for_agent_day(agent_histories, agent_id, day)
            if history is None:
                continue
            if not job_title_matches(history.job_title, target_job_title):
                continue
            if not _agent_active_on_day(agent, history, day):
                continue
            warning = ""
            if not history.job_title:
                warning = "Agente sem cargo definido no histórico."
                warnings.append(
                    {
                        "agent_id": str(agent.id),
                        "lan_id": agent.user_lan_id,
                        "date": day.isoformat(),
                        "message": warning,
                    }
                )
            results.append(
                EligibleAgentDay(
                    agent=agent,
                    day=day,
                    history=history,
                    warning=warning,
                )
            )

    results.sort(key=lambda item: (item.agent.user_lan_id or "", item.day))
    return results, warnings


def load_current_histories(agent_ids: Iterable) -> dict:
    """Histórico aberto vigente por agente (active + sem final_date)."""
    ids = list(agent_ids)
    if not ids:
        return {}
    rows = AgentHistory.objects.filter(
        agent_id__in=ids,
        active=True,
        final_date__isnull=True,
    ).select_related("agent", "leader")
    return {row.agent_id: row for row in rows}


def collect_vigent_eligible_activities(
    eligible: Iterable[EligibleAgentDay],
    *,
    target_job_title: str = TARGET_JOB_TITLE,
) -> list[str]:
    """Atividades do ciclo vigente dos agentes elegíveis (ignora históricos encerrados)."""
    agent_ids = {item.agent.id for item in eligible}
    current_by_agent = load_current_histories(agent_ids)
    activities: set[str] = set()
    for agent_id in agent_ids:
        history = current_by_agent.get(agent_id)
        if history is None:
            continue
        if not job_title_matches(history.job_title, target_job_title):
            continue
        activity = str(history.job_activity or "").strip()
        if activity:
            activities.add(activity)
    return sorted(activities, key=str.casefold)


def build_eligible_agents_summary(
    eligible: Iterable[EligibleAgentDay],
    *,
    target_job_title: str = TARGET_JOB_TITLE,
) -> list[dict]:
    """Resumo por agente usando o histórico vigente quando o cargo alvo coincide."""
    seen: dict = {}
    for item in eligible:
        seen[item.agent.id] = item.agent
    current_by_agent = load_current_histories(seen.keys())

    rows: list[dict] = []
    for agent_id, agent in sorted(
        seen.items(), key=lambda pair: pair[1].user_lan_id or ""
    ):
        history = current_by_agent.get(agent_id)
        if history is None or not job_title_matches(history.job_title, target_job_title):
            continue
        rows.append(
            {
                "id": str(agent.id),
                "lan_id": agent.user_lan_id,
                "full_name": agent.full_name,
                "team": history.team,
                "leader": history.leader.full_name if history.leader_id else "",
                "activity": history.job_activity,
                "journey": history.journey,
            }
        )
    return rows
