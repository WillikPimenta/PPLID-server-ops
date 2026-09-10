"""Timeout automático de status após horário de saída da escala."""

from django.db import connection
from django.utils import timezone

from ..models import ScheduleToday
from .schedule_utils import is_time_range_schedule, normalize_time_schedule, schedule_end_datetime
from .status import StatusService

_TIMEOUT_LOCK_KEY = 0x4E535404  # "NST\x04" — lock global do job de timeout


def _try_acquire_timeout_lock() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_TIMEOUT_LOCK_KEY])
        row = cursor.fetchone()
    return bool(row and row[0])


def _release_timeout_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [_TIMEOUT_LOCK_KEY])


def run_status_timeouts(now=None, *, dry_run: bool = False) -> dict:
    """
    Verifica agentes com status != Deslogado após o fim da escala e encerra o turno.

    Retorna {"closed": N, "agents": [...], "skipped": bool?}.
    """
    lock_acquired = False
    if not dry_run:
        lock_acquired = _try_acquire_timeout_lock()
        if not lock_acquired:
            return {"closed": 0, "agents": [], "skipped": True}

    try:
        now = now or timezone.now()
        today = timezone.localdate()

        candidates = (
            ScheduleToday.objects.filter(date=today)
            .exclude(status_id=StatusService.LOGGED_OUT_ID)
            .select_related("agent", "status")
        )

        closed_agents: list[str] = []

        for row in candidates:
            work_schedule = normalize_time_schedule(row.work_schedule or "")
            if not is_time_range_schedule(work_schedule):
                continue

            end_dt = schedule_end_datetime(
                row.date,
                work_schedule,
                is_previous_night_shift=row.is_previous_night_shift,
            )
            if not end_dt or now <= end_dt:
                continue

            if dry_run:
                closed_agents.append(row.agent.user_lan_id)
                continue

            StatusService.auto_end_shift(row.agent, row, end_dt)
            closed_agents.append(row.agent.user_lan_id)

        return {"closed": len(closed_agents), "agents": closed_agents}
    finally:
        if lock_acquired:
            _release_timeout_lock()
