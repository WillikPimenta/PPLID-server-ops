"""Publicação transacional da prévia em Escala + Schedule + ScheduleToday."""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.escala_flex.models import (
    Escala,
    EscalaGenerationConflict,
    EscalaGenerationEntry,
    EscalaGenerationRun,
    Schedule,
)
from apps.escala_flex.services.schedule_today import ScheduleTodayService
from apps.escala_flex.services.schedule_utils import (
    is_absence_dia_escala,
    resolve_escala_work_schedule,
)

from .rules import merge_configuration


@dataclass
class PublishResult:
    created: int = 0
    updated: int = 0
    skipped: int = 0
    schedules_synced: int = 0
    dates_rebuilt: list[str] = field(default_factory=list)


class PublishBlockedError(Exception):
    def __init__(self, message: str, conflicts: list | None = None):
        super().__init__(message)
        self.conflicts = conflicts or []


@transaction.atomic
def publish_run(*, run: EscalaGenerationRun, user=None) -> PublishResult:
    locked = (
        EscalaGenerationRun.objects.select_for_update()
        .filter(pk=run.pk)
        .first()
    )
    if locked is None:
        raise PublishBlockedError("Execução não encontrada.")
    if locked.status == EscalaGenerationRun.STATUS_PUBLISHED:
        raise PublishBlockedError("Esta execução já foi publicada.")
    if locked.status == EscalaGenerationRun.STATUS_CANCELLED:
        raise PublishBlockedError("Execução cancelada.")
    if locked.status not in {
        EscalaGenerationRun.STATUS_READY,
        EscalaGenerationRun.STATUS_DRAFT,
    }:
        raise PublishBlockedError(
            f"Status inválido para publicação: {locked.status}."
        )

    blocking = list(
        locked.conflicts.filter(
            severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
            resolved=False,
        )
    )
    if blocking:
        raise PublishBlockedError(
            f"Existem {len(blocking)} conflitos impeditivos não resolvidos.",
            conflicts=blocking,
        )

    config = merge_configuration(locked.configuration or {})
    replace_existing = bool(config.get("replace_existing"))

    entries = list(
        locked.entries.select_related(
            "agent", "leader", "job_activity", "location"
        ).all()
    )
    if not entries:
        raise PublishBlockedError("Prévia sem itens para publicar.")

    agent_ids = {e.agent_id for e in entries}
    dates = {e.date for e in entries}
    existing_map = {
        (row.agent_id, row.data): row
        for row in Escala.objects.filter(agent_id__in=agent_ids, data__in=dates)
    }

    result = PublishResult()
    escala_upserts: list[Escala] = []
    schedule_upserts: list[Schedule] = []
    affected_dates: set = set()

    for entry in entries:
        key = (entry.agent_id, entry.date)
        existing = existing_map.get(key)
        if existing and not replace_existing:
            result.skipped += 1
            continue

        if existing:
            result.updated += 1
        else:
            result.created += 1

        escala_upserts.append(
            Escala(
                agent_id=entry.agent_id,
                leader_id=entry.leader_id,
                job_activity_id=entry.job_activity_id,
                location_id=entry.location_id,
                equipe=entry.team,
                horario=entry.schedule,
                data=entry.date,
                dia_escala=entry.day_value,
            )
        )
        work_schedule = resolve_escala_work_schedule(entry.day_value, entry.schedule)
        work_day = bool(work_schedule) and not is_absence_dia_escala(entry.day_value)
        schedule_upserts.append(
            Schedule(
                agent_id=entry.agent_id,
                date=entry.date,
                work_schedule=work_schedule,
                work_day=work_day,
            )
        )
        affected_dates.add(entry.date)

    if escala_upserts:
        Escala.objects.bulk_create(
            escala_upserts,
            update_conflicts=True,
            unique_fields=["agent", "data"],
            update_fields=[
                "leader",
                "job_activity",
                "location",
                "equipe",
                "horario",
                "dia_escala",
            ],
        )
    if schedule_upserts:
        Schedule.objects.bulk_create(
            schedule_upserts,
            update_conflicts=True,
            unique_fields=["agent", "date"],
            update_fields=["work_schedule", "work_day"],
        )
        result.schedules_synced = len(schedule_upserts)

    rebuilt: list[str] = []
    for day in sorted(affected_dates):
        ScheduleTodayService.build_for_date(day)
        rebuilt.append(day.isoformat())
    result.dates_rebuilt = rebuilt

    locked.status = EscalaGenerationRun.STATUS_PUBLISHED
    locked.published_by = user
    locked.published_at = timezone.now()
    locked.summary = {
        **(locked.summary or {}),
        "publish": {
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "schedules_synced": result.schedules_synced,
            "dates_rebuilt": result.dates_rebuilt,
        },
    }
    locked.save(
        update_fields=["status", "published_by", "published_at", "summary"]
    )
    return result


def revalidate_run(run: EscalaGenerationRun) -> EscalaGenerationRun:
    """Revalida conflitos após ajustes manuais, sem regenerar a prévia."""
    from .analysis import build_preview_analysis
    from .conflicts import collect_conflicts

    config = merge_configuration(run.configuration or {})
    entries = []
    for entry in run.entries.select_related(
        "agent", "leader", "job_activity", "location"
    ):
        entries.append(
            {
                "agent": entry.agent,
                "date": entry.date,
                "leader": entry.leader,
                "team": entry.team,
                "sector": entry.sector,
                "activity_name": (
                    entry.job_activity.name if entry.job_activity_id else ""
                ),
                "location_name": (
                    str(entry.location) if entry.location_id else ""
                ),
                "schedule": entry.schedule,
                "day_value": entry.day_value,
                "source": entry.source,
                "job_activity": entry.job_activity,
                "location": entry.location,
                "holiday_work": False,
                "has_conflict": False,
            }
        )

    run.conflicts.all().delete()
    drafts = collect_conflicts(
        entries=entries,
        configuration=config,
        reference_month=run.reference_month,
    )
    entry_by_key = {(e.agent_id, e.date): e for e in run.entries.all()}
    conflict_objs = []
    conflicted = set()
    for draft in drafts:
        entry = entry_by_key.get(draft.entry_key) if draft.entry_key else None
        if draft.entry_key:
            conflicted.add(draft.entry_key)
        conflict_objs.append(
            EscalaGenerationConflict(
                run=run,
                entry=entry,
                agent_id=draft.agent_id,
                date=draft.date,
                activity_name=draft.activity_name,
                conflict_type=draft.conflict_type,
                severity=draft.severity,
                message=draft.message,
                details=draft.details or {},
            )
        )
    EscalaGenerationConflict.objects.bulk_create(conflict_objs, batch_size=500)
    EscalaGenerationEntry.objects.filter(run=run).update(has_conflict=False)
    if conflicted:
        to_flag = [entry_by_key[k].id for k in conflicted if k in entry_by_key]
        EscalaGenerationEntry.objects.filter(id__in=to_flag).update(has_conflict=True)

    for data in entries:
        data["has_conflict"] = (data["agent"].id, data["date"]) in conflicted

    analysis = build_preview_analysis(
        entries,
        conflicts=drafts,
        coverage_schedules=config.get("coverage_schedules") or [],
    )
    run.conflicts_count = len(conflict_objs)
    run.summary = {
        **(run.summary or {}),
        **{
            k: analysis[k]
            for k in (
                "blocking_conflicts",
                "warning_conflicts",
                "work_days",
                "weekend_work",
                "weekend_folga",
                "substitutions",
                "agents_5x2",
                "by_schedule",
                "weekend_coverage",
                "agents_detail",
                "conflicts_by_type",
                "folga_min",
                "folga_max",
                "folga_spread",
            )
        },
        "folga_count": analysis["folga_days"],
    }
    run.save(update_fields=["conflicts_count", "summary"])
    return run
