# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date

from apps.dimensoes_processos.services.meta_etapa_lookup import (
    MetaEtapaLookupCache,
    resolve_stage_goal,
)
from apps.monitor_eventos.services.tabela_monitor import jornada_from_recorded_at
from apps.produtividade.services.excel_reader import ParsedRow
from apps.workforce.models import Agent, AgentHistory


def build_agent_maps() -> tuple[dict[str, Agent], dict[str, str]]:
    agents_by_matricula: dict[str, Agent] = {}
    names_by_matricula: dict[str, str] = {}
    for agent in Agent.objects.all().only("id", "user_lan_id", "full_name"):
        key = (agent.user_lan_id or "").strip().lower()
        if not key:
            continue
        agents_by_matricula[key] = agent
        names_by_matricula[key] = agent.full_name
    return agents_by_matricula, names_by_matricula


def build_history_index() -> dict[str, list[AgentHistory]]:
    index: dict[str, list[AgentHistory]] = {}
    histories = (
        AgentHistory.objects.select_related("agent", "leader")
        .order_by("agent_id", "-start_date")
        .only(
            "agent_id",
            "start_date",
            "final_date",
            "team",
            "location",
            "journey_shift",
            "leader__full_name",
        )
    )
    for history in histories:
        agent_key = str(history.agent_id)
        index.setdefault(agent_key, []).append(history)
    return index


def _history_for_date(histories: list[AgentHistory], on_date) -> AgentHistory | None:
    for history in histories:
        if history.start_date > on_date:
            continue
        if history.final_date and history.final_date < on_date:
            continue
        return history
    return histories[0] if histories else None


def build_meta_etapa_cache_for_rows(
    rows: list[ParsedRow],
) -> MetaEtapaLookupCache:
    """Prefetch DimEtapa/MetaEtapa no range de jornadas do arquivo."""
    etapas: list[str] = []
    dates: list[date] = []
    for row in rows:
        if row.etapa:
            etapas.append(row.etapa)
        jornada = jornada_from_recorded_at(row.recorded_at)
        if jornada is None and row.recorded_at is not None:
            jornada = row.recorded_at.date()
        if jornada is not None:
            dates.append(jornada)
    date_min = min(dates) if dates else None
    date_max = max(dates) if dates else None
    return MetaEtapaLookupCache.build(
        etapa_nomes=etapas or None,
        date_min=date_min,
        date_max=date_max,
    )


def enrich_row(
    row: ParsedRow,
    agents_by_matricula: dict[str, Agent],
    names_by_matricula: dict[str, str],
    history_index: dict[str, list[AgentHistory]],
    *,
    meta_cache: MetaEtapaLookupCache | None = None,
    today: date | None = None,
) -> dict:
    agent = agents_by_matricula.get(row.matricula_norm)
    agent_name = names_by_matricula.get(row.matricula_norm, "")
    team = ""
    location = ""
    journey_shift = ""
    leader_name = ""

    on_date = jornada_from_recorded_at(row.recorded_at)
    if on_date is None:
        on_date = row.recorded_at.date()

    if agent:
        histories = history_index.get(str(agent.id), [])
        history = _history_for_date(histories, on_date)
        if history:
            team = history.team or ""
            location = history.location or ""
            journey_shift = history.journey_shift or ""
            if history.leader_id and history.leader:
                leader_name = history.leader.full_name or ""

    stage_goal = resolve_stage_goal(
        etapa_nome=row.etapa,
        on_date=on_date,
        today=today,
        cache=meta_cache,
    )

    return {
        "matricula_norm": row.matricula_norm,
        "agent": agent,
        "etapa": row.etapa,
        "analysis_seconds": row.analysis_seconds,
        "analysis_count": row.analysis_count,
        "stage_goal": stage_goal,
        "recorded_at": row.recorded_at,
        "agent_name": agent_name,
        "team": team,
        "location": location,
        "journey_shift": journey_shift,
        "leader_name": leader_name,
    }
