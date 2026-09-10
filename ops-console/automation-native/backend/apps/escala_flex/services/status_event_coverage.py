"""Cobertura de tempo de status por ocorrências operacionais aprovadas.

O tempo previsto em ocorrências (Planejamento) cobre parte da duração real do
status. O excedente vai para aprovação do líder no Histórico.

Somente status vinculados a um tipo de ocorrência entram nesse fluxo.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable
from uuid import UUID

from django.utils import timezone

from ..models import (
    OccurrenceType,
    OperationalOccurrence,
    OperationalOccurrenceExtension,
    StatusEvent,
)

PAUSE_STATUS_IDS = {2, 4, 5, 6, 7, 8, 9, 10, 11, 13}


def _event_day(event: StatusEvent) -> date:
    start = event.start_date
    if timezone.is_aware(start):
        return timezone.localtime(start).date()
    return start.date()


def _pool_key(agent_id: int, day: date, status_id: int) -> tuple[int, date, int]:
    return (agent_id, day, status_id)


def load_linked_status_ids(status_ids: Iterable[int] | None = None) -> set[int]:
    """Status IDs que possuem vínculo com algum tipo de ocorrência ativo."""
    qs = OccurrenceType.objects.filter(active=True, status_types__isnull=False)
    if status_ids is not None:
        status_ids = {int(s) for s in status_ids if s is not None}
        if not status_ids:
            return set()
        qs = qs.filter(status_types__id__in=status_ids)
    return set(qs.values_list("status_types__id", flat=True).distinct())


def load_occurrence_coverage_pools(
    events: Iterable[StatusEvent],
    linked_status_ids: set[int] | None = None,
) -> dict[tuple[int, date, int], int]:
    """Soma forecast_seconds de ocorrências aprovadas por (agente, dia, status)."""
    events = [
        e
        for e in events
        if e.status_id in PAUSE_STATUS_IDS and e.total_duration is not None
    ]
    if not events:
        return {}

    if linked_status_ids is None:
        linked_status_ids = load_linked_status_ids({e.status_id for e in events})

    agent_ids = {e.agent_id for e in events}
    days = {_event_day(e) for e in events}
    status_ids = {e.status_id for e in events if e.status_id in linked_status_ids}
    if not status_ids:
        return {}

    pools: dict[tuple[int, date, int], int] = defaultdict(int)
    occurrences = (
        OperationalOccurrence.objects.filter(
            agent_id__in=agent_ids,
            date__in=days,
            approved=True,
            cancelled=False,
            occurrence_type__status_types__id__in=status_ids,
        )
        .prefetch_related("occurrence_type__status_types")
        .distinct()
    )

    for occ in occurrences:
        linked = {st.id for st in occ.occurrence_type.status_types.all()}
        for status_id in linked & status_ids:
            pools[_pool_key(occ.agent_id, occ.date, status_id)] += int(occ.forecast_seconds or 0)

    return dict(pools)


def allocate_coverage_for_events(
    events: Iterable[StatusEvent],
) -> dict[UUID, dict[str, int | bool]]:
    """Aloca cobertura cronologicamente; retorna covered/excess/status_linked por event.id."""
    pause_events = [
        e
        for e in events
        if e.status_id in PAUSE_STATUS_IDS and not e.active_event and e.total_duration is not None
    ]
    if not pause_events:
        return {}

    linked_status_ids = load_linked_status_ids({e.status_id for e in pause_events})
    pools = load_occurrence_coverage_pools(pause_events, linked_status_ids)
    remaining = dict(pools)

    ordered = sorted(pause_events, key=lambda e: (e.start_date, str(e.id)))
    result: dict[UUID, dict[str, int | bool]] = {}

    for event in ordered:
        total = max(0, int(event.total_duration or 0))
        linked = event.status_id in linked_status_ids
        if not linked:
            result[event.id] = {
                "covered_duration": 0,
                "excess_duration": 0,
                "status_linked": False,
            }
            continue

        key = _pool_key(event.agent_id, _event_day(event), event.status_id)
        available = max(0, int(remaining.get(key, 0)))
        covered = min(total, available)
        remaining[key] = available - covered
        excess = max(0, total - covered)
        result[event.id] = {
            "covered_duration": covered,
            "excess_duration": excess,
            "status_linked": True,
        }

    return result


def build_coverage_map(events: Iterable[StatusEvent]) -> dict[UUID, dict[str, int | bool]]:
    """Mapa covered/excess incluindo irmãos do mesmo dia/status (fora da página)."""
    from django.db.models import Q

    pause_events = [
        e
        for e in events
        if e.status_id in PAUSE_STATUS_IDS and not e.active_event and e.total_duration is not None
    ]
    if not pause_events:
        return {}

    q = Q()
    for event in pause_events:
        q |= Q(
            agent_id=event.agent_id,
            status_id=event.status_id,
            start_date__date=_event_day(event),
        )

    siblings = list(
        StatusEvent.objects.filter(
            q,
            active_event=False,
            total_duration__isnull=False,
            status_id__in=PAUSE_STATUS_IDS,
        )
    )
    return allocate_coverage_for_events(siblings)


def coverage_for_event(event: StatusEvent) -> dict[str, int | bool]:
    """Cobertura de um único evento (considera irmãos do mesmo dia/status)."""
    if event.status_id not in PAUSE_STATUS_IDS or event.active_event:
        return {"covered_duration": 0, "excess_duration": 0, "status_linked": False}

    allocated = build_coverage_map([event])
    return allocated.get(
        event.id,
        {
            "covered_duration": 0,
            "excess_duration": 0,
            "status_linked": event.status_id in load_linked_status_ids({event.status_id}),
        },
    )


def related_occurrences_for_event(event: StatusEvent) -> list[OperationalOccurrence]:
    """Ocorrências do agente no dia vinculadas ao status do evento (consulta)."""
    if not event.status_id:
        return []

    day = _event_day(event)
    pending_ext_qs = OperationalOccurrenceExtension.objects.filter(
        approved__isnull=True,
    ).select_related("created_by", "approved_by")

    from django.db.models import Prefetch

    return list(
        OperationalOccurrence.objects.filter(
            agent_id=event.agent_id,
            date=day,
            cancelled=False,
            occurrence_type__status_types__id=event.status_id,
        )
        .select_related(
            "agent",
            "occurrence_type",
            "leader",
            "created_by",
            "approved_by",
            "schedule_today",
            "schedule_today__hierarchical_level",
            "agent__current_activity_record__hierarchical_level",
        )
        .prefetch_related(
            Prefetch("extensions", queryset=pending_ext_qs, to_attr="_pending_extensions"),
        )
        .distinct()
        .order_by("scheduled_time", "created_at")
    )
