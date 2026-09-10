"""Consulta, estatísticas e edição de intervalos (ef_break_time)."""

import re
from datetime import datetime

from django.db.models import Q
from django.utils import timezone

from apps.workforce.models import Agent, AgentHistory

from ..models import BreakTime, Escala, ScheduleToday
from .break_time_rules import (
    STANDARD_BREAK_DURATION_MINUTES,
    break_duration_minutes,
    break_exit_bounds,
    uses_extra_break_interval,
    validate_break_exit_against_schedule,
)
from .overtime import compute_overtime_flag
from .schedule_today import ScheduleTodayService
from .schedule_utils import (
    is_time_range_schedule,
    normalize_time_schedule,
    resolve_default_schedule,
)

AGENT_JOB_TITLE = "Agente Backoffice I"
HEATMAP_SLOT_MINUTES = 20
DEFAULT_BREAK_DURATION_MINUTES = STANDARD_BREAK_DURATION_MINUTES
TIME_ONLY_RE = re.compile(r"^\d{2}:\d{2}$")


def format_break_exit_time(value: str) -> str:
    """Exibe horário de saída para intervalo (HH:MM). Aceita legado hh:mm - hh:mm."""
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = normalize_time_schedule(text)
    if is_time_range_schedule(normalized):
        return normalized.split("-")[0].strip()
    if TIME_ONLY_RE.match(text):
        return text
    return text


def normalize_break_exit_time(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if is_time_range_schedule(normalize_time_schedule(text)):
        return format_break_exit_time(text)
    try:
        parsed = datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise ValueError(
            "Horário de saída inválido. Informe apenas HH:MM (ex.: 13:00)."
        ) from exc
    return parsed.strftime("%H:%M")


def format_headcount_journey(journey: str) -> str:
    """Compat: normaliza journey do headcount quando for faixa horária."""
    return resolve_default_schedule(journey=journey)


def _latest_escala_map(agent_ids: list) -> dict[str, Escala]:
    """Última escala publicada por agente (mesma fonte da Consulta de Escala)."""
    if not agent_ids:
        return {}
    result: dict[str, Escala] = {}
    escalas = (
        Escala.objects.filter(agent_id__in=agent_ids)
        .select_related("agent", "leader", "job_activity", "location")
        .order_by("agent_id", "-data")
    )
    for escala in escalas:
        lan = escala.agent.user_lan_id.lower()
        if lan not in result:
            result[lan] = escala
    return result


def resolve_agent_default_schedule(
    agent: Agent,
    *,
    escala: Escala | None = None,
    journey: str = "",
) -> str:
    horario = escala.horario if escala else ""
    if not horario and escala is None:
        horario = (
            Escala.objects.filter(agent=agent)
            .exclude(horario="")
            .order_by("-data")
            .values_list("horario", flat=True)
            .first()
            or ""
        )
    if not journey:
        history = (
            AgentHistory.objects.filter(
                agent=agent, active=True, final_date__isnull=True
            )
            .only("journey")
            .first()
        )
        journey = history.journey if history else ""
    return resolve_default_schedule(horario=horario, journey=journey)


def _active_backoffice_histories(
    *,
    search: str | None = None,
    location: str | None = None,
    job_activity: str | None = None,
):
    qs = (
        AgentHistory.objects.filter(
            active=True,
            final_date__isnull=True,
            job_title=AGENT_JOB_TITLE,
            agent__active=True,
        )
        .select_related("agent", "leader")
        .order_by("agent__full_name")
    )
    if location:
        qs = qs.filter(location=location)
    if job_activity:
        qs = qs.filter(job_activity=job_activity)
    if search:
        qs = qs.filter(
            Q(agent__full_name__icontains=search)
            | Q(agent__user_lan_id__icontains=search)
        )
    return qs


def _break_time_map() -> dict[str, BreakTime]:
    return {
        bt.agent_lan_id.lower(): bt
        for bt in BreakTime.objects.filter(active=True)
    }


def _break_label(bt: BreakTime | None, *, weekend: bool = False) -> str:
    if not bt:
        return ""
    raw = (bt.weekend if weekend else bt.week) or ""
    return format_break_exit_time(raw)


def _heatmap_slot_labels() -> list[str]:
    labels: list[str] = []
    minute = 0
    end_minute = 24 * 60
    while minute < end_minute:
        hour, mins = divmod(minute, 60)
        labels.append(f"{hour:02d}:{mins:02d}")
        minute += HEATMAP_SLOT_MINUTES
    return labels


def _heatmap_hour_labels() -> list[str]:
    return [f"{hour:02d}:00" for hour in range(24)]


def _resolve_agent_overtime(entry: ScheduleToday | None, work_schedule: str = "") -> bool:
    if not entry:
        schedule = work_schedule or ""
        return compute_overtime_flag(schedule) if schedule else False
    if entry.overtime:
        return True
    return compute_overtime_flag(entry.work_schedule or "")


def _schedule_today_map(agent_ids: list, target_date) -> dict[str, ScheduleToday]:
    if not agent_ids:
        return {}
    entries = ScheduleToday.objects.filter(
        agent_id__in=agent_ids,
        date=target_date,
    ).select_related("agent")
    return {entry.agent.user_lan_id.lower(): entry for entry in entries}


def resolve_agent_break_interval(
    row: dict,
    *,
    target_date=None,
) -> tuple[int, int] | None:
    """
    Intervalo efetivo do agente no dia (alinhado ao Painel):
    - sem HE / dia útil: intervalo semanal (15 min)
    - com HE ou fim de semana: intervalo extra (1h com HE, 15 min sem HE)
    """
    if target_date is None:
        target_date = timezone.localdate()

    overtime = bool(row.get("overtime"))
    is_weekend = target_date.weekday() >= 5
    use_extra = uses_extra_break_interval(overtime=overtime, is_weekend=is_weekend)
    raw_label = row["weekend"] if use_extra else row["week"]
    exit_time = format_break_exit_time(raw_label)
    if not exit_time or not TIME_ONLY_RE.match(exit_time):
        return None

    hour, minute = map(int, exit_time.split(":"))
    start_min = hour * 60 + minute
    duration = break_duration_minutes(extra=use_extra, overtime=overtime)
    return start_min, start_min + duration


def _slot_range_minutes(slot_label: str) -> tuple[int, int]:
    hour, minute = map(int, slot_label.split(":"))
    start = hour * 60 + minute
    return start, start + HEATMAP_SLOT_MINUTES


def _interval_overlaps_slot(
    start_min: int, end_min: int, slot_start: int, slot_end: int
) -> bool:
    return start_min < slot_end and end_min > slot_start


def compute_break_heatmap(rows: list[dict], *, target_date=None) -> dict:
    if target_date is None:
        target_date = timezone.localdate()

    slots = _heatmap_slot_labels()
    slot_count = len(slots)
    total_counts = [0] * slot_count
    activities: list[dict[str, int]] = [{} for _ in range(slot_count)]

    for row in rows:
        activity = (row.get("job_activity") or "").strip() or "Sem atividade"
        interval = resolve_agent_break_interval(row, target_date=target_date)
        if not interval:
            continue
        start_min, end_min = interval
        for index, slot_label in enumerate(slots):
            slot_start, slot_end = _slot_range_minutes(slot_label)
            if _interval_overlaps_slot(start_min, end_min, slot_start, slot_end):
                total_counts[index] += 1
                activities[index][activity] = activities[index].get(activity, 0) + 1

    return {
        "slots": slots,
        "hours": _heatmap_hour_labels(),
        "slot_minutes": HEATMAP_SLOT_MINUTES,
        "rows": [
            {"label": "Total", "counts": total_counts},
        ],
        "activity_by_slot": activities,
        "max_count": max(total_counts) if total_counts else 0,
    }


def list_break_time_rows(
    *,
    search: str | None = None,
    location: str | None = None,
    job_activity: str | None = None,
) -> list[dict]:
    histories = list(
        _active_backoffice_histories(
            search=search,
            location=location,
            job_activity=job_activity,
        )
    )
    break_map = _break_time_map()
    escala_map = _latest_escala_map([h.agent_id for h in histories])
    target_date = timezone.localdate()
    schedule_today_map = _schedule_today_map(
        [h.agent_id for h in histories],
        target_date,
    )
    rows: list[dict] = []
    for history in histories:
        agent = history.agent
        lan = agent.user_lan_id.lower()
        bt = break_map.get(lan)
        escala = escala_map.get(lan)
        schedule_today = schedule_today_map.get(lan)
        default_schedule = resolve_agent_default_schedule(
            agent,
            escala=escala,
            journey=history.journey,
        )
        overtime = _resolve_agent_overtime(schedule_today, default_schedule)
        location = (
            escala.location.display_name
            if escala and escala.location
            else history.location or ""
        )
        job_activity = (
            escala.job_activity.name
            if escala and escala.job_activity
            else history.job_activity or ""
        )
        if escala and escala.leader:
            leader_name = escala.leader.full_name
            leader_lan_id = escala.leader.user_lan_id
        else:
            leader_name = history.leader.full_name if history.leader else ""
            leader_lan_id = history.leader.user_lan_id if history.leader else ""
        week_bounds = break_exit_bounds(default_schedule, extra=False)
        weekend_bounds = break_exit_bounds(default_schedule, extra=True, overtime=overtime)
        rows.append(
            {
                "break_time_id": str(bt.id) if bt else None,
                "user_lan_id": agent.user_lan_id,
                "full_name": agent.full_name,
                "location": location,
                "job_activity": job_activity,
                "leader_name": leader_name,
                "leader_lan_id": leader_lan_id,
                "default_schedule": default_schedule,
                "overtime": overtime,
                "break_exit_min": week_bounds[0] if week_bounds else "",
                "break_exit_max": week_bounds[1] if week_bounds else "",
                "weekend_break_exit_min": weekend_bounds[0] if weekend_bounds else "",
                "weekend_break_exit_max": weekend_bounds[1] if weekend_bounds else "",
                "week": _break_label(bt),
                "weekend": _break_label(bt, weekend=True),
            }
        )
    return rows


def filter_options() -> dict:
    histories = _active_backoffice_histories()
    locations = sorted(
        {h.location for h in histories if h.location},
        key=str.casefold,
    )
    activities = sorted(
        {h.job_activity for h in histories if h.job_activity},
        key=str.casefold,
    )
    return {"locations": locations, "job_activities": activities}


def compute_break_time_stats(
    *,
    search: str | None = None,
    location: str | None = None,
    job_activity: str | None = None,
) -> dict:
    rows = list_break_time_rows(
        search=search,
        location=location,
        job_activity=job_activity,
    )
    without_config = sum(
        1 for row in rows if not row["week"].strip() and not row["weekend"].strip()
    )

    return {
        "total_agents": len(rows),
        "without_config": without_config,
        "heatmap": compute_break_heatmap(rows, target_date=timezone.localdate()),
    }


def upsert_break_time(
    agent: Agent,
    *,
    week: str | None = None,
    weekend: str | None = None,
    work_schedule: str | None = None,
) -> BreakTime:
    lan = agent.user_lan_id.lower()
    bt = BreakTime.objects.filter(agent_lan_id__iexact=lan, active=True).first()
    if not bt:
        bt = BreakTime(agent_lan_id=lan, active=True)

    schedule = str(work_schedule or "").strip()
    if not schedule:
        schedule = resolve_agent_default_schedule(agent)

    if week is not None:
        if week.strip():
            if schedule:
                validate_break_exit_against_schedule(schedule, week, extra=False)
            bt.week = normalize_break_exit_time(week)
        else:
            bt.week = ""
    if weekend is not None:
        if weekend.strip():
            if schedule:
                overtime = _resolve_agent_overtime(
                    ScheduleToday.objects.filter(
                        agent=agent,
                        date=timezone.localdate(),
                    ).first(),
                    schedule,
                )
                validate_break_exit_against_schedule(
                    schedule,
                    weekend,
                    extra=True,
                    overtime=overtime,
                )
            bt.weekend = normalize_break_exit_time(weekend)
        else:
            bt.weekend = ""

    bt.active = True
    bt.save()
    ScheduleTodayService.sync_agent_break_times(agent)
    return bt


def apply_break_time_updates(items: list[dict]) -> tuple[int, list[dict]]:
    updated = 0
    errors: list[dict] = []
    for item in items:
        lan = str(item.get("user_lan_id") or "").strip()
        agent = Agent.objects.filter(user_lan_id__iexact=lan, active=True).first()
        if not agent:
            errors.append({"user_lan_id": lan, "detail": "Agente não encontrado."})
            continue
        try:
            upsert_break_time(
                agent,
                week=item.get("week"),
                weekend=item.get("weekend"),
            )
            updated += 1
        except ValueError as exc:
            errors.append({"user_lan_id": lan, "detail": str(exc)})
    return updated, errors
