# -*- coding: utf-8 -*-
"""Meta horária dinâmica (intervalo + hora extra) para comparação de produtividade."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

from django.db.models import QuerySet
from django.utils import timezone

from apps.escala_flex.models import BreakTime, Schedule
from apps.escala_flex.services.break_time_rules import (
    break_duration_minutes,
    uses_extra_break_interval,
)
from apps.produtividade.models import ProductivityRecord
from apps.workforce.models import Agent

HOURLY_THRESHOLD = 17.27  # ~ meta diária 95% / 5h30


@dataclass(frozen=True)
class DayScheduleContext:
    overtime: bool
    break_start_min: int | None
    break_end_min: int | None


def _parse_exit_minutes(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    range_match = re.search(r"(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})", text)
    if range_match:
        return int(range_match.group(1)) * 60 + int(range_match.group(2))
    match = re.match(r"^(\d{1,2}):(\d{2})$", text)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def resolve_break_window_minutes(
    overtime: bool,
    ref_date: date,
    break_time: BreakTime | None,
) -> tuple[int, int] | None:
    if break_time is None:
        return None
    extra = uses_extra_break_interval(
        overtime=overtime,
        is_weekend=ref_date.weekday() >= 5,
    )
    exit_raw = (break_time.weekend if extra else break_time.week).strip()
    exit_min = _parse_exit_minutes(exit_raw)
    if exit_min is None:
        return None
    duration = break_duration_minutes(extra=extra, overtime=overtime)
    return exit_min, exit_min + duration


def break_overlap_minutes(
    hour_start: datetime,
    break_start_min: int,
    break_end_min: int,
) -> int:
    local = (
        timezone.localtime(hour_start)
        if timezone.is_aware(hour_start)
        else hour_start
    )
    hour_begin = local.hour * 60 + local.minute
    hour_end = hour_begin + 60
    overlap_start = max(hour_begin, break_start_min)
    overlap_end = min(hour_end, break_end_min)
    return max(0, overlap_end - overlap_start)


def hourly_forecast_threshold(overtime: bool, overlap_minutes: int) -> float | None:
    if overtime:
        return None
    productive = max(0, 60 - max(0, int(overlap_minutes)))
    return round((productive / 60.0) * HOURLY_THRESHOLD, 2)


def build_schedule_context_lookup(
    matriculas: set[str],
    dates: set[date],
) -> dict[tuple[str, date], DayScheduleContext]:
    if not matriculas or not dates:
        return {}

    mats_lower = {m.strip().lower() for m in matriculas if m}
    agents = Agent.objects.filter(user_lan_id__in=mats_lower).only("id", "user_lan_id")
    agent_by_mat = {a.user_lan_id.strip().lower(): a for a in agents if a.user_lan_id}
    if not agent_by_mat:
        return {}

    break_map = {
        bt.agent_lan_id.strip().lower(): bt
        for bt in BreakTime.objects.filter(active=True, agent_lan_id__in=mats_lower)
    }

    schedules = Schedule.objects.filter(
        agent_id__in=[a.id for a in agent_by_mat.values()],
        date__in=dates,
    ).select_related("agent")

    lookup: dict[tuple[str, date], DayScheduleContext] = {}
    for schedule in schedules:
        matricula = schedule.agent.user_lan_id.strip().lower()
        window = resolve_break_window_minutes(
            schedule.overtime,
            schedule.date,
            break_map.get(matricula),
        )
        lookup[(matricula, schedule.date)] = DayScheduleContext(
            overtime=bool(schedule.overtime),
            break_start_min=window[0] if window else None,
            break_end_min=window[1] if window else None,
        )
    return lookup


def _dates_from_queryset(qs: QuerySet) -> set[date]:
    dates: set[date] = set()
    for recorded_at in qs.values_list("recorded_at", flat=True):
        if not recorded_at:
            continue
        local = (
            timezone.localtime(recorded_at)
            if timezone.is_aware(recorded_at)
            else recorded_at
        )
        dates.add(local.date())
    return dates


def _matriculas_from_queryset(qs: QuerySet) -> set[str]:
    return {
        str(m).strip().lower()
        for m in qs.values_list("matricula_norm", flat=True).distinct()
        if m
    }


def build_hourly_threshold_map(
    hourly_keys: set[tuple[str, object]],
    schedule_lookup: dict[tuple[str, date], DayScheduleContext],
) -> dict[tuple[str, object], float | None]:
    result: dict[tuple[str, object], float | None] = {}
    for matricula, hour in hourly_keys:
        if not isinstance(hour, datetime):
            result[(matricula, hour)] = HOURLY_THRESHOLD
            continue
        local = timezone.localtime(hour) if timezone.is_aware(hour) else hour
        ctx = schedule_lookup.get((matricula, local.date()))
        if ctx is None:
            result[(matricula, hour)] = HOURLY_THRESHOLD
            continue
        if ctx.overtime:
            result[(matricula, hour)] = None
            continue
        overlap = 0
        if ctx.break_start_min is not None and ctx.break_end_min is not None:
            overlap = break_overlap_minutes(hour, ctx.break_start_min, ctx.break_end_min)
        result[(matricula, hour)] = hourly_forecast_threshold(False, overlap)
    return result


def build_hourly_threshold_map_for_qs(
    qs: QuerySet,
    hourly_sums: dict[tuple[str, object], float],
) -> dict[tuple[str, object], float | None]:
    if not hourly_sums:
        return {}
    matriculas = _matriculas_from_queryset(qs)
    dates = _dates_from_queryset(qs)
    schedule_lookup = build_schedule_context_lookup(matriculas, dates)
    return build_hourly_threshold_map(set(hourly_sums.keys()), schedule_lookup)


def threshold_for_agent_hour(
    matricula: str,
    hour: datetime,
    schedule_lookup: dict[tuple[str, date], DayScheduleContext],
) -> float | None:
    matricula = matricula.strip().lower()
    local = timezone.localtime(hour) if timezone.is_aware(hour) else hour
    ctx = schedule_lookup.get((matricula, local.date()))
    if ctx is None:
        return HOURLY_THRESHOLD
    if ctx.overtime:
        return None
    overlap = 0
    if ctx.break_start_min is not None and ctx.break_end_min is not None:
        overlap = break_overlap_minutes(hour, ctx.break_start_min, ctx.break_end_min)
    return hourly_forecast_threshold(False, overlap)
