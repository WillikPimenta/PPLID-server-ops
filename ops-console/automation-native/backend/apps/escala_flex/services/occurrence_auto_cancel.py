"""Cancelamento automático de ocorrências pendentes após expirar o prazo."""

from __future__ import annotations

from django.db import connection

from ..models import OperationalOccurrence
from .occurrence_workflow import occurrence_end_at

_AUTO_CANCEL_NOTE = (
    "Cancelada automaticamente: prazo da ocorrência expirou sem aprovação ou recusa."
)
_LOCK_KEY = 0x4F434143  # "OCAC" — lock global do job de cancelamento


def _try_acquire_lock() -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_LOCK_KEY])
        row = cursor.fetchone()
    return bool(row and row[0])


def _release_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [_LOCK_KEY])


def is_occurrence_unanswered_past_deadline(occurrence: OperationalOccurrence, reference) -> bool:
    if occurrence.cancelled or occurrence.approved is not None:
        return False
    end_at = occurrence_end_at(occurrence)
    if not end_at:
        return False
    return reference >= end_at


def run_occurrence_auto_cancels(now=None, *, dry_run: bool = False) -> dict:
    """
    Cancela ocorrências pendentes cujo horário de finalização já passou.

    Retorna {"cancelled": N, "ids": [...], "agents": [...], "skipped": bool?}.
    """
    from django.utils import timezone

    lock_acquired = False
    if not dry_run:
        lock_acquired = _try_acquire_lock()
        if not lock_acquired:
            return {"cancelled": 0, "ids": [], "agents": [], "skipped": True}

    try:
        now = now or timezone.now()
        candidates = OperationalOccurrence.objects.filter(
            cancelled=False,
            approved__isnull=True,
        ).select_related("agent")

        cancelled_ids: list[str] = []
        cancelled_agents: list[str] = []

        for occurrence in candidates:
            if not is_occurrence_unanswered_past_deadline(occurrence, now):
                continue

            if dry_run:
                cancelled_ids.append(str(occurrence.id))
                cancelled_agents.append(occurrence.agent.user_lan_id)
                continue

            existing = occurrence.approval_notes.strip()
            occurrence.approval_notes = (
                f"{existing}\n{_AUTO_CANCEL_NOTE}".strip() if existing else _AUTO_CANCEL_NOTE
            )
            occurrence.cancelled = True
            occurrence.save(update_fields=["cancelled", "approval_notes"])
            cancelled_ids.append(str(occurrence.id))
            cancelled_agents.append(occurrence.agent.user_lan_id)

        return {
            "cancelled": len(cancelled_ids),
            "ids": cancelled_ids,
            "agents": cancelled_agents,
        }
    finally:
        if lock_acquired:
            _release_lock()
