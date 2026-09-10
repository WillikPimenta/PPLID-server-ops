"""Snapshot do Painel filtrado por atividade (job_activity) para apoio à aprovação de ocorrências."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from django.utils import timezone

from .permissions import OperationalProfile

from .kpis import AVAILABLE_STATUS_ID, LOGGED_OUT_STATUS_ID
from .panel_schedule import build_panel_rows
from .schedule_utils import is_shift_active_at, is_time_range_schedule


def _logged_in_today(start_of_work, target_date: date) -> bool:
    if not start_of_work:
        return False
    return timezone.localtime(start_of_work).date() == target_date


def _enrich_panel_row(row: dict[str, Any], target_date: date, reference_time) -> dict[str, Any]:
    status_id = row.get("status")
    work_schedule = row.get("work_schedule") or ""
    has_time = bool(row.get("has_time_schedule")) or is_time_range_schedule(work_schedule)
    start_of_work = row.get("start_of_work")
    is_night = bool(row.get("is_previous_night_shift"))

    disponivel = status_id == AVAILABLE_STATUS_ID
    deslogado = status_id == LOGGED_OUT_STATUS_ID
    em_pausa = status_id is not None and status_id not in (
        AVAILABLE_STATUS_ID,
        LOGGED_OUT_STATUS_ID,
    )
    logged_in = _logged_in_today(start_of_work, target_date)
    ausente_dia = has_time and not logged_in
    em_turno = is_shift_active_at(
        reference_time,
        target_date,
        work_schedule,
        is_previous_night_shift=is_night,
    )
    ausente_agora = False
    if em_turno and has_time:
        if start_of_work is None:
            ausente_agora = True
        else:
            ausente_agora = timezone.localtime(start_of_work) > timezone.localtime(reference_time)

    capacidade_operacional = em_turno and disponivel
    online_agora = capacidade_operacional
    pausa_agora = em_turno and em_pausa

    if ausente_agora:
        estado = "Ausente"
    elif deslogado:
        estado = "Deslogado"
    elif disponivel:
        estado = "Disponível"
    elif em_pausa:
        estado = (row.get("status_name") or "Em pausa").strip() or "Em pausa"
    else:
        estado = (row.get("status_name") or "—").strip() or "—"

    nh = (row.get("hierarchical_level_name") or row.get("current_activity") or "—").strip() or "—"

    return {
        **row,
        "nh": nh,
        "disponivel": disponivel,
        "deslogado": deslogado,
        "em_pausa": em_pausa,
        "ausente_dia": ausente_dia,
        "ausente_agora": ausente_agora,
        "em_turno": em_turno,
        "capacidade_operacional": capacidade_operacional,
        "online_agora": online_agora,
        "pausa_agora": pausa_agora,
        "estado": estado,
    }


def _agent_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_lan_id": row.get("user_lan_id") or "",
        "full_name": row.get("full_name") or "",
        "nh": row.get("nh") or "—",
        "status": row.get("status"),
        "status_name": row.get("status_name") or "",
        "status_color": row.get("status_color") or "",
        "estado": row.get("estado") or "—",
        "leader_name": row.get("leader_name") or "",
    }


def build_activity_panel_snapshot(
    target_date: date,
    job_activity: str,
    profile: OperationalProfile | None = None,
    reference_time=None,
) -> dict[str, Any]:
    reference_time = reference_time or timezone.now()
    activity = (job_activity or "").strip()
    base_rows = build_panel_rows(target_date, profile)
    rows = [
        _enrich_panel_row(row, target_date, reference_time)
        for row in base_rows
        if (row.get("job_activity") or "").strip() == activity
    ]

    by_nh: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "nh": "",
            "escalados": 0,
            "online": 0,
            "em_pausa": 0,
            "ausentes": 0,
            "agents": [],
        }
    )

    online_agents: list[dict[str, Any]] = []
    absent_agents: list[dict[str, Any]] = []

    for row in rows:
        nh_key = row["nh"]
        bucket = by_nh[nh_key]
        bucket["nh"] = nh_key
        bucket["escalados"] += 1
        agent = _agent_payload(row)
        bucket["agents"].append(agent)

        if row["online_agora"]:
            bucket["online"] += 1
            online_agents.append(agent)
        if row["pausa_agora"]:
            bucket["em_pausa"] += 1
        if row["ausente_agora"]:
            bucket["ausentes"] += 1
            absent_agents.append(agent)

    nh_groups = []
    for nh_key in sorted(by_nh.keys(), key=lambda value: value.lower()):
        bucket = by_nh[nh_key]
        bucket["agents"] = sorted(
            bucket["agents"],
            key=lambda agent: (agent.get("full_name") or "").lower(),
        )
        nh_groups.append(bucket)

    online_agents.sort(key=lambda agent: (agent.get("full_name") or "").lower())
    absent_agents.sort(key=lambda agent: (agent.get("full_name") or "").lower())

    return {
        "date": str(target_date),
        "job_activity": activity,
        "reference_time": timezone.localtime(reference_time).isoformat(),
        "summary": {
            "escalados": len(rows),
            "online": sum(1 for row in rows if row["online_agora"]),
            "em_pausa": sum(1 for row in rows if row["pausa_agora"]),
            "ausentes": sum(1 for row in rows if row["ausente_agora"]),
            "deslogados": sum(1 for row in rows if row["deslogado"]),
        },
        "by_nh": nh_groups,
        "online_agents": online_agents,
        "absent_agents": absent_agents,
    }
