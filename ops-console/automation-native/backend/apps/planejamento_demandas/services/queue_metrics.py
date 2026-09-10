"""Métricas operacionais de tempo na fila (idade e parada)."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone

ATTENTION_IDLE_DAYS = 3
STALE_IDLE_DAYS = 7
CRITICAL_IDLE_DAYS = 14
ATTENTION_AGE_DAYS = 7
STALE_AGE_DAYS = 14
CRITICAL_AGE_DAYS = 30


def _days_between(start: datetime | None, end: datetime | None = None) -> int | None:
    if start is None:
        return None
    end = end or timezone.now()
    if timezone.is_naive(start):
        start = timezone.make_aware(start, timezone.get_current_timezone())
    delta = end - start
    return max(0, delta.days)


def _staleness(*, age_days: int | None, idle_days: int | None, is_open: bool) -> str:
    if not is_open:
        return "closed"
    age = age_days if age_days is not None else 0
    idle = idle_days if idle_days is not None else 0
    if idle >= CRITICAL_IDLE_DAYS or age >= CRITICAL_AGE_DAYS:
        return "critical"
    if idle >= STALE_IDLE_DAYS or age >= STALE_AGE_DAYS:
        return "stale"
    if idle >= ATTENTION_IDLE_DAYS or age >= ATTENTION_AGE_DAYS:
        return "attention"
    return "fresh"


def _label_days(days: int | None, *, prefix: str) -> str:
    if days is None:
        return "—"
    if days <= 0:
        return f"{prefix} hoje"
    if days == 1:
        return f"{prefix} 1 dia"
    return f"{prefix} {days} dias"


def queue_metrics(
    *,
    created_at_jira: datetime | None,
    updated_at_jira: datetime | None,
    is_open: bool,
    status_kind: str,
) -> dict:
    open_item = is_open and status_kind == "open"
    age_days = _days_between(created_at_jira) if open_item else None
    idle_days = _days_between(updated_at_jira) if open_item else None
    staleness = _staleness(age_days=age_days, idle_days=idle_days, is_open=open_item)
    return {
        "queue_age_days": age_days,
        "idle_days": idle_days,
        "queue_age_label": _label_days(age_days, prefix="Na fila há") if open_item else "",
        "idle_label": _label_days(idle_days, prefix="Sem movimento há") if open_item else "",
        "staleness": staleness,
    }
