"""Zera status operacionais no início do dia (05:30) e limpeza de eventos do dia."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from ..models import AgentStatus, ScheduleToday, StatusEvent
from .status import StatusService


def _local_day_bounds(target: date) -> tuple[datetime, datetime]:
    start = timezone.make_aware(datetime.combine(target, time.min))
    end = start + timedelta(days=1)
    return start, end


def reset_daily_status(target_date: date | None = None, *, dry_run: bool = False) -> dict[str, Any]:
    """
    Limpa status operacionais do dia:
    - Remove eventos de status iniciados na data alvo
    - Encerra eventos ativos anteriores ao dia
    - Zera status/start_of_work em ScheduleToday do dia
    - Zera status/start_of_work em AgentStatus
    """
    target_date = target_date or timezone.localdate()
    day_start, day_end = _local_day_bounds(target_date)
    now = timezone.now()

    events_today = StatusEvent.objects.filter(
        start_date__gte=day_start,
        start_date__lt=day_end,
    )
    events_today_count = events_today.count()

    stale_active = StatusEvent.objects.filter(active_event=True, start_date__lt=day_start)
    stale_active_count = stale_active.count()

    schedule_qs = ScheduleToday.objects.filter(date=target_date)
    schedule_count = schedule_qs.count()

    agent_status_count = AgentStatus.objects.count()

    result = {
        "date": str(target_date),
        "events_deleted": events_today_count,
        "stale_events_closed": stale_active_count,
        "schedule_today_cleared": schedule_count,
        "agent_status_cleared": agent_status_count,
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    with transaction.atomic():
        for event in stale_active.iterator():
            StatusService._close_active_event(event, now)

        events_today.delete()

        schedule_qs.update(
            status_id=None,
            start_of_work=None,
            last_change=None,
        )

        AgentStatus.objects.update(
            status_id=None,
            start_of_work=None,
            date_of_change=None,
        )

    return result
