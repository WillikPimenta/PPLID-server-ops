"""Detecção e classificação de conflitos da prévia."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from apps.escala_flex.models import Escala, EscalaGenerationConflict, EscalaGenerationEntry
from apps.escala_flex.services.schedule_utils import is_night_shift_crossing

from .rules import (
    is_absence_value,
    is_5x2_schedule,
    is_work_day_value,
    normalize_schedule_value,
    parse_activity_coverage,
    parse_schedule_limits,
    proposed_work_breaks_consecutive_days,
    proposed_work_breaks_rest,
    rest_hours_between,
    weekday_allowed,
)


@dataclass
class ConflictDraft:
    conflict_type: str
    severity: str
    message: str
    agent_id: object | None = None
    date: date | None = None
    entry_key: tuple | None = None
    activity_name: str = ""
    details: dict = field(default_factory=dict)


def collect_conflicts(
    *,
    entries: list[dict],
    configuration: dict,
    reference_month: date,
) -> list[ConflictDraft]:
    conflicts: list[ConflictDraft] = []
    absence_codes = configuration.get("absence_codes") or []
    allow_night = bool(configuration.get("allow_night_shift"))
    min_rest = float(configuration.get("min_rest_hours") or 11)
    replace_existing = bool(configuration.get("replace_existing"))
    max_consecutive = int(configuration.get("max_consecutive_work_days") or 0)

    by_agent: dict = defaultdict(list)
    for entry in entries:
        by_agent[entry["agent"].id].append(entry)

    for agent_id, agent_entries in by_agent.items():
        agent_entries.sort(key=lambda e: e["date"])
        prev_work = None
        consecutive = 0
        for entry in agent_entries:
            day = entry["date"]
            day_value = entry.get("day_value") or ""
            schedule = entry.get("schedule") or ""
            key = (agent_id, day)

            if not entry.get("leader"):
                conflicts.append(
                    ConflictDraft(
                        conflict_type=EscalaGenerationConflict.TYPE_MISSING_LEADER,
                        severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
                        message="Agente sem liderança cadastrada.",
                        agent_id=agent_id,
                        date=day,
                        entry_key=key,
                    )
                )
            if not entry.get("team"):
                conflicts.append(
                    ConflictDraft(
                        conflict_type=EscalaGenerationConflict.TYPE_MISSING_TEAM,
                        severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
                        message="Agente sem equipe cadastrada.",
                        agent_id=agent_id,
                        date=day,
                        entry_key=key,
                    )
                )
            if not entry.get("job_activity") and not entry.get("activity_name"):
                conflicts.append(
                    ConflictDraft(
                        conflict_type=EscalaGenerationConflict.TYPE_MISSING_ACTIVITY,
                        severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
                        message="Agente sem atividade cadastrada.",
                        agent_id=agent_id,
                        date=day,
                        entry_key=key,
                    )
                )
            if not entry.get("location") and not entry.get("location_name"):
                conflicts.append(
                    ConflictDraft(
                        conflict_type=EscalaGenerationConflict.TYPE_MISSING_LOCATION,
                        severity=EscalaGenerationConflict.SEVERITY_WARNING,
                        message="Agente sem localidade cadastrada.",
                        agent_id=agent_id,
                        date=day,
                        entry_key=key,
                    )
                )
            if is_work_day_value(day_value) or (not day_value and is_work_day_value(schedule)):
                effective = day_value if is_work_day_value(day_value) else schedule
                if not effective:
                    conflicts.append(
                        ConflictDraft(
                            conflict_type=EscalaGenerationConflict.TYPE_MISSING_SCHEDULE,
                            severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
                            message="Agente sem horário válido.",
                            agent_id=agent_id,
                            date=day,
                            entry_key=key,
                        )
                    )
                elif (
                    is_night_shift_crossing(effective)
                    and not allow_night
                    and not is_night_shift_crossing(schedule)
                ):
                    conflicts.append(
                        ConflictDraft(
                            conflict_type=EscalaGenerationConflict.TYPE_NIGHT_SHIFT_DENIED,
                            severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
                            message="Turno noturno não permitido na configuração.",
                            agent_id=agent_id,
                            date=day,
                            entry_key=key,
                            details={"schedule": effective},
                        )
                    )
                if prev_work is not None:
                    rest = rest_hours_between(prev_work, effective)
                    if rest is not None and rest < min_rest:
                        conflicts.append(
                            ConflictDraft(
                                conflict_type=EscalaGenerationConflict.TYPE_INSUFFICIENT_REST,
                                severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
                                message=(
                                    f"Descanso insuficiente entre jornadas ({rest:.1f}h < {min_rest}h)."
                                ),
                                agent_id=agent_id,
                                date=day,
                                entry_key=key,
                                details={"rest_hours": rest, "min_rest_hours": min_rest},
                            )
                        )
                consecutive += 1
                if max_consecutive and consecutive == max_consecutive + 1:
                    # Um aviso por sequência (não um por dia após o limite).
                    conflicts.append(
                        ConflictDraft(
                            conflict_type=EscalaGenerationConflict.TYPE_INVALID_JOURNEY,
                            severity=EscalaGenerationConflict.SEVERITY_WARNING,
                            message=(
                                f"Mais de {max_consecutive} dias consecutivos de trabalho."
                            ),
                            agent_id=agent_id,
                            date=day,
                            entry_key=key,
                        )
                    )
                prev_work = effective
            else:
                consecutive = 0
                prev_work = None
                if day_value and not is_absence_value(day_value, absence_codes) and not is_work_day_value(day_value):
                    conflicts.append(
                        ConflictDraft(
                            conflict_type=EscalaGenerationConflict.TYPE_INVALID_JOURNEY,
                            severity=EscalaGenerationConflict.SEVERITY_BLOCKING,
                            message=f"Valor de dia inválido: {day_value}.",
                            agent_id=agent_id,
                            date=day,
                            entry_key=key,
                        )
                    )

            if entry.get("holiday_work"):
                conflicts.append(
                    ConflictDraft(
                        conflict_type=EscalaGenerationConflict.TYPE_HOLIDAY_WORK,
                        severity=EscalaGenerationConflict.SEVERITY_WARNING,
                        message="Trabalho em feriado.",
                        agent_id=agent_id,
                        date=day,
                        entry_key=key,
                    )
                )

    # Cobertura por atividade / limites por horário
    by_day: dict[date, list[dict]] = defaultdict(list)
    for entry in entries:
        by_day[entry["date"]].append(entry)

    for day, day_entries in by_day.items():
        working = [
            e
            for e in day_entries
            if is_work_day_value(e.get("day_value") or "")
            or (
                not (e.get("day_value") or "")
                and is_work_day_value(e.get("schedule") or "")
            )
        ]
        for limit in parse_schedule_limits(configuration):
            if not weekday_allowed(day, limit.weekdays):
                continue
            matched = []
            for e in working:
                effective = (
                    e["day_value"]
                    if is_work_day_value(e.get("day_value") or "")
                    else e.get("schedule") or ""
                )
                if limit.schedule and effective != limit.schedule:
                    continue
                activity = (e.get("activity_name") or "").casefold()
                if limit.activity and activity != limit.activity.casefold():
                    continue
                team = (e.get("team") or "").casefold()
                if limit.team and team != limit.team.casefold():
                    continue
                matched.append(e)
            count = len(matched)
            if limit.min_count and count < limit.min_count:
                conflicts.append(
                    ConflictDraft(
                        conflict_type=EscalaGenerationConflict.TYPE_COVERAGE_BELOW_MIN,
                        severity=EscalaGenerationConflict.SEVERITY_WARNING,
                        message=(
                            f"Cobertura do horário {limit.schedule} abaixo do mínimo "
                            f"({count}/{limit.min_count}) em {day.isoformat()}."
                        ),
                        date=day,
                        details={"schedule": limit.schedule, "count": count, "min": limit.min_count},
                    )
                )
            if limit.max_count is not None and count > limit.max_count:
                conflicts.append(
                    ConflictDraft(
                        conflict_type=EscalaGenerationConflict.TYPE_SCHEDULE_OVER_MAX,
                        severity=EscalaGenerationConflict.SEVERITY_WARNING,
                        message=(
                            f"Limite do horário {limit.schedule} excedido "
                            f"({count}/{limit.max_count}) em {day.isoformat()}."
                        ),
                        date=day,
                        details={"schedule": limit.schedule, "count": count, "max": limit.max_count},
                    )
                )

        for cov in parse_activity_coverage(configuration):
            if not weekday_allowed(day, cov.weekdays):
                continue
            if not cov.min_coverage:
                continue
            schedules = cov.schedules or []
            if not schedules:
                continue
            allowed = {normalize_schedule_value(s) for s in schedules}
            present: set[str] = set()
            for e in day_entries:
                base = normalize_schedule_value(e.get("schedule") or "")
                if base in allowed:
                    present.add(base)
            if not present:
                continue
            weak_slots: list[dict] = []
            for schedule in sorted(present):
                target = normalize_schedule_value(schedule)
                count = sum(
                    1
                    for e in working
                    if normalize_schedule_value(
                        e["day_value"]
                        if is_work_day_value(e.get("day_value") or "")
                        else e.get("schedule") or ""
                    )
                    == target
                )
                if count < cov.min_coverage:
                    weak_slots.append(
                        {"schedule": target, "count": count, "min": cov.min_coverage}
                    )

            if weak_slots:
                worst = min(slot["count"] for slot in weak_slots)
                labels = ", ".join(slot["schedule"] for slot in weak_slots[:3])
                folga_candidates = []
                rejected = {"interjornada": 0, "sequencia_dias": 0, "indisponivel": 0}
                for e in day_entries:
                    if str(e.get("day_value") or "").upper() != "FOLGA":
                        continue
                    if e.get("source") in {
                        EscalaGenerationEntry.SOURCE_ABSENCE,
                        EscalaGenerationEntry.SOURCE_HOLIDAY,
                    } or e.get("fixed_weekend_off") or (
                        day.weekday() >= 5
                        and is_5x2_schedule(e.get("schedule") or "", e.get("journey_shift") or "")
                    ):
                        rejected["indisponivel"] += 1
                        continue
                    sched = normalize_schedule_value(e.get("schedule") or "")
                    if not is_work_day_value(sched) or is_night_shift_crossing(sched):
                        rejected["indisponivel"] += 1
                        continue
                    if proposed_work_breaks_consecutive_days(
                        entries, e, max_consecutive
                    ):
                        rejected["sequencia_dias"] += 1
                        continue
                    eligible_schedules = []
                    rest_rejected = False
                    for slot in weak_slots:
                        target = normalize_schedule_value(slot["schedule"])
                        if is_night_shift_crossing(target):
                            continue
                        if proposed_work_breaks_rest(entries, e, target, min_rest):
                            rest_rejected = True
                            continue
                        eligible_schedules.append(target)
                    if not eligible_schedules:
                        rejected["interjornada" if rest_rejected else "indisponivel"] += 1
                        continue
                    same_slot = sched in set(eligible_schedules)
                    folga_candidates.append(
                        (0 if same_slot else 1, e, sched, eligible_schedules)
                    )
                folga_candidates.sort(
                    key=lambda item: (
                        item[0],
                        getattr(item[1]["agent"], "user_lan_id", "") or "",
                    )
                )
                flagged = 0
                for same_rank, e, sched, eligible_schedules in folga_candidates:
                    lan = getattr(e["agent"], "user_lan_id", "") or ""
                    name = getattr(e["agent"], "full_name", "") or ""
                    same_slot = same_rank == 0
                    if flagged < 4:
                        conflicts.append(
                            ConflictDraft(
                                conflict_type=EscalaGenerationConflict.TYPE_COVERAGE_BELOW_MIN,
                                severity=EscalaGenerationConflict.SEVERITY_WARNING,
                                message=(
                                    f"Cobertura insuficiente em {day.isoformat()} "
                                    f"({labels}; pior {worst}/{cov.min_coverage}). "
                                    "Substituto elegível para a cobertura"
                                    + (
                                        f" — candidato em FOLGA: {lan or name}"
                                        if same_slot or lan or name
                                        else "."
                                    )
                                    + (
                                        " (mesmo horário)."
                                        if same_slot
                                        else " (outro horário — ajuste a célula)."
                                    )
                                ),
                                agent_id=e["agent"].id,
                                date=day,
                                entry_key=(e["agent"].id, day),
                                activity_name=cov.activity,
                                details={
                                    "min": cov.min_coverage,
                                    "worst_count": worst,
                                    "weak_schedules": weak_slots,
                                    "needs_leader_substitute": True,
                                    "suggested_lan": lan,
                                    "same_slot": same_slot,
                                    "eligible_schedules": eligible_schedules,
                                    "eligibility_checks": {
                                        "interjornada": True,
                                        "sequencia_dias": True,
                                    },
                                },
                            )
                        )
                        flagged += 1
                if flagged == 0:
                    others = [
                        {
                            "lan": getattr(e["agent"], "user_lan_id", "") or "",
                            "name": getattr(e["agent"], "full_name", "") or "",
                            "day_value": e.get("day_value") or "",
                            "schedule": normalize_schedule_value(e.get("schedule") or ""),
                        }
                        for e in day_entries
                        if is_work_day_value(e.get("day_value") or "")
                    ][:8]
                    conflicts.append(
                        ConflictDraft(
                            conflict_type=EscalaGenerationConflict.TYPE_COVERAGE_BELOW_MIN,
                            severity=EscalaGenerationConflict.SEVERITY_WARNING,
                            message=(
                                f"Cobertura insuficiente em {day.isoformat()} "
                                f"({labels}; pior {worst}/{cov.min_coverage}). "
                                "Nenhum agente em FOLGA pode assumir a cobertura sem "
                                "violar interjornada ou a sequÃªncia mÃ¡xima de trabalho."
                            ),
                            date=day,
                            activity_name=cov.activity,
                            details={
                                "min": cov.min_coverage,
                                "worst_count": worst,
                                "weak_schedules": weak_slots,
                                "needs_leader_substitute": True,
                                "working_others": others,
                                "eligible_candidates": [],
                                "rejected_candidates": rejected,
                            },
                        )
                    )

    # Escalas existentes — um conflito resumido (evita pintar a grade inteira).
    if entries:
        agent_ids = {e["agent"].id for e in entries}
        dates = {e["date"] for e in entries}
        existing_count = Escala.objects.filter(
            agent_id__in=agent_ids, data__in=dates
        ).count()
        if existing_count:
            conflicts.append(
                ConflictDraft(
                    conflict_type=EscalaGenerationConflict.TYPE_EXISTING_ESCALA,
                    severity=(
                        EscalaGenerationConflict.SEVERITY_WARNING
                        if replace_existing
                        else EscalaGenerationConflict.SEVERITY_BLOCKING
                    ),
                    message=(
                        f"Já existem {existing_count} registro(s) de escala no período. "
                        + (
                            "Ative 'Substituir escala existente' para publicar por cima."
                            if not replace_existing
                            else "Serão substituídos na publicação."
                        )
                    ),
                    details={"existing_count": existing_count},
                )
            )

    return conflicts
