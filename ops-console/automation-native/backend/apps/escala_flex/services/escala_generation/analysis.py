"""Resumo analítico da prévia mensal (visão do líder)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from apps.escala_flex.services.schedule_utils import (
    is_night_shift_crossing,
    is_time_range_schedule,
    normalize_time_schedule,
)

from .rules import is_5x2_schedule, is_work_day_value, normalize_schedule_value


def _is_work(value: str) -> bool:
    return is_work_day_value(value or "")


def _is_folga(value: str) -> bool:
    return str(value or "").strip().upper() == "FOLGA"


def build_preview_analysis(
    entries: list[dict],
    *,
    conflicts: list | None = None,
    coverage_schedules: list[str] | None = None,
) -> dict:
    """Monta indicadores para o líder analisar a prévia."""
    by_agent: dict = defaultdict(list)
    for e in entries:
        by_agent[e["agent"].id].append(e)

    work_days = 0
    folga_days = 0
    weekend_work = 0
    weekend_folga = 0
    substitutions = 0
    agents_5x2 = 0
    schedule_counts: dict[str, int] = defaultdict(int)
    agent_rows: list[dict] = []

    for agent_id, agent_entries in by_agent.items():
        agent_entries.sort(key=lambda x: x["date"])
        sample = agent_entries[0]
        agent = sample["agent"]
        is_5x2 = bool(sample.get("is_5x2")) or is_5x2_schedule(
            sample.get("schedule") or "",
            sample.get("journey_shift") or "",
        )
        if is_5x2:
            agents_5x2 += 1
        is_night = is_night_shift_crossing(
            normalize_time_schedule(sample.get("schedule") or "")
        )

        a_work = 0
        a_folga = 0
        a_folga_balanced = 0  # FOLGAs geradas (exclui ausência/feriado/5x2 fixa)
        a_weekend_work = 0
        a_weekend_folga = 0
        a_subs = 0
        a_conflicts = 0

        for e in agent_entries:
            day: date = e["date"]
            value = str(e.get("day_value") or "")
            schedule = normalize_schedule_value(e.get("schedule") or "")
            effective = normalize_schedule_value(value)
            weekend = day.weekday() >= 5
            source = str(e.get("source") or "")

            if _is_work(value):
                work_days += 1
                a_work += 1
                schedule_counts[effective] += 1
                if weekend:
                    weekend_work += 1
                    a_weekend_work += 1
                if (
                    schedule
                    and effective
                    and schedule != effective
                    and is_time_range_schedule(normalize_time_schedule(schedule))
                ):
                    substitutions += 1
                    a_subs += 1
            elif _is_folga(value):
                folga_days += 1
                a_folga += 1
                if weekend:
                    weekend_folga += 1
                    a_weekend_folga += 1
                if (
                    not e.get("fixed_weekend_off")
                    and source not in {"absence", "holiday"}
                ):
                    a_folga_balanced += 1

            if e.get("has_conflict"):
                a_conflicts += 1

        agent_rows.append(
            {
                "agent_id": str(agent_id),
                "lan_id": agent.user_lan_id or "",
                "name": agent.full_name or "",
                "team": sample.get("team") or "",
                "leader": getattr(sample.get("leader"), "full_name", "") or "",
                "schedule": sample.get("schedule") or "",
                "is_5x2": is_5x2,
                "is_night": is_night,
                "work_days": a_work,
                "folga_days": a_folga,
                "folga_balanced": a_folga_balanced,
                "weekend_work": a_weekend_work,
                "weekend_folga": a_weekend_folga,
                "substitutions": a_subs,
                "conflict_days": a_conflicts,
            }
        )

    agent_rows.sort(key=lambda r: (r["is_5x2"], r["is_night"], -r["folga_balanced"], r["lan_id"]))

    # Equilíbrio exibido: diurnos operacionais (fora 5x2 e noturno).
    operational = [r for r in agent_rows if not r["is_5x2"] and not r["is_night"]]
    balanced_values = [r["folga_balanced"] for r in operational]
    folga_min = min(balanced_values) if balanced_values else 0
    folga_max = max(balanced_values) if balanced_values else 0
    folga_spread = folga_max - folga_min

    conflict_by_type: dict[str, int] = defaultdict(int)
    blocking = 0
    warning = 0
    for c in conflicts or []:
        ctype = getattr(c, "conflict_type", None) or (c.get("conflict_type") if isinstance(c, dict) else "")
        sev = getattr(c, "severity", None) or (c.get("severity") if isinstance(c, dict) else "")
        if ctype:
            conflict_by_type[str(ctype)] += 1
        if sev == "blocking":
            blocking += 1
        elif sev == "warning":
            warning += 1

    # Cobertura nos fins de semana por slot configurado
    cov_schedules = [
        normalize_schedule_value(s) for s in (coverage_schedules or [])
    ]
    weekend_coverage: list[dict] = []
    if cov_schedules:
        by_day: dict[date, list[dict]] = defaultdict(list)
        for e in entries:
            by_day[e["date"]].append(e)
        for day in sorted(d for d in by_day if d.weekday() >= 5):
            slots = []
            for slot in cov_schedules:
                count = sum(
                    1
                    for e in by_day[day]
                    if normalize_schedule_value(e.get("day_value") or "") == slot
                )
                slots.append({"schedule": slot, "count": count})
            weekend_coverage.append(
                {
                    "date": day.isoformat(),
                    "weekday": "sáb" if day.weekday() == 5 else "dom",
                    "slots": slots,
                }
            )

    return {
        "agents": len(by_agent),
        "agents_5x2": agents_5x2,
        "work_days": work_days,
        "folga_days": folga_days,
        "folga_min": folga_min,
        "folga_max": folga_max,
        "folga_spread": folga_spread,
        "weekend_work": weekend_work,
        "weekend_folga": weekend_folga,
        "substitutions": substitutions,
        "blocking_conflicts": blocking,
        "warning_conflicts": warning,
        "conflicts_by_type": dict(conflict_by_type),
        "by_schedule": dict(sorted(schedule_counts.items(), key=lambda x: (-x[1], x[0]))),
        "weekend_coverage": weekend_coverage,
        "agents_detail": agent_rows,
    }
