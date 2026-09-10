"""Montagem da listagem do Painel por headcount (Agente Backoffice I vigente)."""

from __future__ import annotations

from datetime import date

from django.db.models import Max
from django.utils import timezone

from apps.workforce.models import Agent, AgentHistory

from ..models import AgentStatus, BreakTime, CurrentActivity, Escala, Schedule, ScheduleToday, StatusType
from .overtime import compute_overtime_flag, hours_from_work_schedule
from .permissions import OperationalProfile, open_access_enabled
from .schedule_today import ScheduleTodayService, _entry_schedule_metrics
from .schedule_utils import (
    is_absence_dia_escala,
    is_night_shift_crossing,
    is_time_range_schedule,
    normalize_time_schedule,
    resolve_default_schedule,
    resolve_escala_work_schedule,
)

BACKOFFICE_JOB_TITLE = "Agente Backoffice I"


def _dedupe_headcount_histories(histories: list[AgentHistory]) -> list[AgentHistory]:
    """Um registro por agente: o mais recente por start_date."""
    by_agent: dict[int, AgentHistory] = {}
    for history in histories:
        agent_id = history.agent_id
        current = by_agent.get(agent_id)
        if current is None or history.start_date > current.start_date:
            by_agent[agent_id] = history
    return sorted(by_agent.values(), key=lambda h: (h.agent.full_name or "").lower())


def scope_panel_headcount(
    histories: list[AgentHistory],
    profile: OperationalProfile | None,
) -> list[AgentHistory]:
    """Restringe headcount conforme perfil operacional."""
    if not profile or open_access_enabled():
        return histories
    if profile.is_admin or profile.team == "Planejamento":
        return histories
    if profile.is_agent_backoffice:
        lan = profile.lan_id.lower()
        return [h for h in histories if h.agent.user_lan_id.lower() == lan]
    if profile.lan_id:
        leader = Agent.objects.filter(user_lan_id__iexact=profile.lan_id).first()
        if leader and AgentHistory.objects.filter(leader=leader, active=True).exists():
            lan = profile.lan_id.lower()
            return [
                h
                for h in histories
                if h.leader and h.leader.user_lan_id.lower() == lan
            ]
    return histories


def _eligible_headcount_histories() -> list[AgentHistory]:
    histories = list(
        AgentHistory.objects.filter(
            final_date__isnull=True,
            job_title=BACKOFFICE_JOB_TITLE,
        )
        .select_related("agent", "leader")
        .order_by("agent_id", "-start_date")
    )
    return _dedupe_headcount_histories(histories)


def _resolve_schedule_display(escala: Escala | None, work_schedule: str) -> str:
    if is_time_range_schedule(work_schedule):
        return work_schedule
    if escala:
        dia = str(escala.dia_escala or "").strip()
        if dia:
            if is_absence_dia_escala(dia):
                return dia.strip()
            return dia
        horario = str(escala.horario or "").strip()
        if horario:
            return horario
    return "Sem escala"


def _night_shift_map(target: date) -> dict[str, str]:
    processed: set[str] = set()
    result: dict[str, str] = {}
    for schedule, work_schedule in ScheduleTodayService._night_shifts_from_yesterday(
        target, processed
    ):
        lan = schedule.agent.user_lan_id.lower()
        result[lan] = work_schedule
    return result


def _default_status() -> tuple[int | None, str, str]:
    try:
        status_type = StatusType.objects.get(pk=3)
        return status_type.pk, status_type.name, status_type.color
    except StatusType.DoesNotExist:
        return 3, "Deslogado", "#bdb2b0"


def panel_max_published_date(profile: OperationalProfile | None = None) -> date | None:
    """Última data com escala publicada para o headcount do painel."""
    histories = scope_panel_headcount(_eligible_headcount_histories(), profile)
    agent_ids = [h.agent_id for h in histories]
    if not agent_ids:
        return None
    return Escala.objects.filter(agent_id__in=agent_ids).aggregate(max_data=Max("data"))[
        "max_data"
    ]


def panel_nav_max_date(profile: OperationalProfile | None = None) -> date:
    """Limite superior de navegação: hoje ou última escala publicada futura."""
    today = timezone.localdate()
    published_max = panel_max_published_date(profile)
    if published_max and published_max > today:
        return published_max
    return today


def build_panel_rows(
    target: date,
    profile: OperationalProfile | None = None,
) -> list[dict]:
    """Monta linhas do Painel para a data alvo a partir do headcount elegível."""
    today = timezone.localdate()
    is_future = target > today
    histories = scope_panel_headcount(_eligible_headcount_histories(), profile)
    if not histories:
        return []

    agent_ids = [h.agent_id for h in histories]
    escalas = {
        e.agent_id: e
        for e in Escala.objects.filter(data=target, agent_id__in=agent_ids).select_related(
            "agent", "leader", "job_activity", "location"
        )
    }
    schedule_today_map = {
        st.agent_id: st
        for st in ScheduleToday.objects.filter(date=target, agent_id__in=agent_ids).select_related(
            "agent", "status", "leader", "hierarchical_level"
        )
    }
    status_map = ScheduleTodayService._status_map()
    activity_map = ScheduleTodayService._current_activity_map()
    break_map = ScheduleTodayService._break_time_map()
    night_map = _night_shift_map(target) if not is_future else {}
    default_status_id, default_status_name, default_status_color = _default_status()

    rows: list[dict] = []
    for history in histories:
        agent = history.agent
        lan = agent.user_lan_id.lower()
        escala = escalas.get(agent.id)
        entry = None if is_future else schedule_today_map.get(agent.id)

        work_schedule = ""
        is_night = False
        schedule: Schedule | None = None

        if entry and entry.work_schedule and is_time_range_schedule(entry.work_schedule):
            work_schedule = normalize_time_schedule(entry.work_schedule)
            is_night = entry.is_previous_night_shift
        elif entry and entry.is_previous_night_shift and entry.work_schedule:
            work_schedule = normalize_time_schedule(entry.work_schedule)
            is_night = True
        elif escala:
            work_schedule = resolve_escala_work_schedule(escala.dia_escala, escala.horario)
            work_schedule = normalize_time_schedule(work_schedule)
        elif lan in night_map:
            work_schedule = night_map[lan]
            is_night = True

        schedule_display = _resolve_schedule_display(escala, work_schedule)
        has_time_schedule = is_time_range_schedule(work_schedule)

        if entry:
            status = entry.status
            status_id = status.pk if status else default_status_id
            status_name = status.name if status else default_status_name
            status_color = status.color if status else default_status_color
            current_activity = entry.current_activity or ""
            hierarchical_level = entry.hierarchical_level_id
            hierarchical_level_name = (
                entry.hierarchical_level.name if entry.hierarchical_level else ""
            )
            start_of_work = entry.start_of_work
            week_break = entry.week_break or ""
            weekend_break = entry.weekend_break or ""
            blocked = entry.blocked
            blocked_by = entry.blocked_by or ""
            blocked_description = entry.blocked_description or ""
            blocked_at = entry.blocked_at
            last_change = entry.last_change
            row_id = str(entry.id)
            leader = entry.leader
            leader_name = entry.leader.full_name if entry.leader else history.leader.full_name if history.leader else ""
            leader_lan_id = entry.leader_lan_id or (
                history.leader.user_lan_id.lower() if history.leader else ""
            )
            location = entry.location or history.location or ""
            job_activity = entry.job_activity or history.job_activity or ""
            job_title = entry.job_title or history.job_title or ""
            journey = entry.journey or resolve_default_schedule(
                horario=escala.horario if escala else "",
                journey=history.journey or "",
            )
            sector = entry.sector or history.team_sector or ""
            full_name = entry.full_name or agent.full_name
            working_hour = entry.working_hour
            overtime = entry.overtime or compute_overtime_flag(work_schedule)
        else:
            agent_status = status_map.get(lan)
            activity_record = activity_map.get(lan)
            bt = break_map.get(lan)
            status_type = agent_status.status if agent_status else None
            status_id = status_type.pk if status_type else default_status_id
            status_name = status_type.name if status_type else default_status_name
            status_color = status_type.color if status_type else default_status_color
            current_activity = agent_status.current_activity if agent_status else ""
            hierarchical_level = (
                activity_record.hierarchical_level_id if activity_record else None
            )
            hierarchical_level_name = (
                activity_record.hierarchical_level.name
                if activity_record and activity_record.hierarchical_level
                else ""
            )
            start_of_work = agent_status.start_of_work if agent_status else None
            week_break = bt.week if bt else ""
            weekend_break = bt.weekend if bt else ""
            blocked = False
            blocked_by = ""
            blocked_description = ""
            blocked_at = None
            last_change = agent_status.date_of_change if agent_status else None
            row_id = f"panel-{agent.id}"

            if escala and escala.leader:
                leader = escala.leader
                leader_name = escala.leader.full_name
                leader_lan_id = escala.leader.user_lan_id.lower()
            elif history.leader:
                leader = history.leader
                leader_name = history.leader.full_name
                leader_lan_id = history.leader.user_lan_id.lower()
            else:
                leader = None
                leader_name = ""
                leader_lan_id = ""

            if escala:
                location = escala.location.display_name if escala.location else history.location or ""
                job_activity = (
                    escala.job_activity.name if escala.job_activity else history.job_activity or ""
                )
            else:
                location = history.location or ""
                job_activity = history.job_activity or ""

            job_title = history.job_title or ""
            journey = resolve_default_schedule(
                horario=escala.horario if escala else "",
                journey=history.journey or "",
            )
            sector = escala.equipe if escala else history.team_sector or ""
            full_name = agent.full_name
            working_hour, overtime = _entry_schedule_metrics(work_schedule, schedule)

        rows.append(
            {
                "id": row_id,
                "agent_id": agent.id,
                "user_lan_id": agent.user_lan_id,
                "full_name": full_name,
                "date": str(target),
                "work_schedule": work_schedule,
                "working_hour": working_hour,
                "overtime": bool(overtime),
                "status": status_id,
                "status_name": status_name,
                "status_color": status_color,
                "current_activity": current_activity,
                "hierarchical_level": hierarchical_level,
                "hierarchical_level_name": hierarchical_level_name,
                "start_of_work": start_of_work,
                "week_break": week_break,
                "weekend_break": weekend_break,
                "is_previous_night_shift": is_night,
                "leader_name": leader_name,
                "leader_lan_id": leader_lan_id,
                "location": location,
                "job_activity": job_activity,
                "job_title": job_title,
                "journey": journey,
                "sector": sector,
                "blocked": blocked,
                "blocked_by": blocked_by,
                "blocked_description": blocked_description,
                "blocked_at": blocked_at,
                "last_change": last_change,
                "has_time_schedule": has_time_schedule,
                "schedule_display": schedule_display,
            }
        )

    return rows


def filter_panel_rows(rows: list[dict], params) -> list[dict]:
    """Aplica os mesmos filtros de schedule_today_list sobre linhas do painel."""
    qs = rows
    if params.get("logged_only") in ("true", "1", "yes"):
        qs = [r for r in qs if r.get("status") != 3]
    if names := params.getlist("full_name"):
        qs = [r for r in qs if r.get("full_name") in names]
    if nh := params.getlist("current_activity"):
        qs = [r for r in qs if r.get("hierarchical_level_name") in nh]
    if activities := params.getlist("job_activity"):
        qs = [r for r in qs if (r.get("job_activity") or "").strip() in activities]
    if schedules := params.getlist("work_schedule"):
        qs = [r for r in qs if r.get("work_schedule") in schedules]
    if locations := params.getlist("location"):
        qs = [r for r in qs if r.get("location") in locations]
    if statuses := params.getlist("status"):
        status_ids = {int(s) for s in statuses}
        qs = [r for r in qs if r.get("status") in status_ids]
    if params.get("overtime") == "true":
        qs = [r for r in qs if r.get("overtime")]
    if leader_lan := params.get("leader_lan_id"):
        leader_lan = leader_lan.lower()
        qs = [r for r in qs if (r.get("leader_lan_id") or "").lower() == leader_lan]
    if search := params.get("search"):
        search_lower = search.lower()
        qs = [
            r
            for r in qs
            if search_lower in (r.get("full_name") or "").lower()
            or search_lower in (r.get("user_lan_id") or "").lower()
        ]
    if params.get("time_schedule_only") in ("true", "1", "yes"):
        qs = [r for r in qs if r.get("has_time_schedule")]
    return sorted(qs, key=lambda r: (r.get("full_name") or "").lower())


def panel_filter_options(rows: list[dict]) -> dict:
    statuses_map: dict[int, str] = {}
    for row in rows:
        status_id = row.get("status")
        if status_id is not None:
            statuses_map[status_id] = row.get("status_name") or ""
    statuses = [
        {"id": sid, "name": name}
        for sid, name in sorted(statuses_map.items(), key=lambda x: (x[1] or "").lower())
    ]
    return {
        "full_names": sorted(
            {r["full_name"] for r in rows if r.get("full_name")},
            key=str.lower,
        ),
        "current_activities": sorted(
            {r["hierarchical_level_name"] for r in rows if r.get("hierarchical_level_name")},
            key=str.lower,
        ),
        "work_schedules": sorted(
            {r["work_schedule"] for r in rows if r.get("work_schedule")},
            key=str.lower,
        ),
        "locations": sorted(
            {r["location"] for r in rows if r.get("location")},
            key=str.lower,
        ),
        "statuses": statuses,
    }


def panel_leader_options(rows: list[dict]) -> list[dict]:
    leaders_map: dict[str, str] = {}
    for row in rows:
        lan = (row.get("leader_lan_id") or "").lower()
        name = row.get("leader_name") or ""
        if lan and name:
            leaders_map[lan] = name
    return [
        {"lan_id": lan, "name": name}
        for lan, name in sorted(leaders_map.items(), key=lambda x: x[1].lower())
    ]


def panel_has_overnight_schedule(rows: list[dict]) -> bool:
    for row in rows:
        if row.get("is_previous_night_shift"):
            return True
        if is_night_shift_crossing(row.get("work_schedule") or ""):
            return True
    return False
