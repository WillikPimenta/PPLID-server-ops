"""Regras de cobertura, limites, jornada e feriados."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from apps.escala_flex.models import Holiday
from apps.escala_flex.services.schedule_utils import (
    is_night_shift_crossing,
    is_time_range_schedule,
    normalize_time_schedule,
    parse_work_schedule,
)

from .holiday_calendar import (
    CalendarHoliday,
    merge_calculated_holidays,
    normalize_holiday_location,
)


WEEKDAY_KEYS = {
    0: "mon",
    1: "tue",
    2: "wed",
    3: "thu",
    4: "fri",
    5: "sat",
    6: "sun",
}


def merge_configuration(raw: dict | None) -> dict:
    from .constants import DEFAULT_COVERAGE_SCHEDULES, default_configuration

    base = default_configuration()
    if not raw:
        return base
    merged = {**base, **raw}
    for key in (
        "schedule_limits",
        "activity_coverage",
        "absence_codes",
        "holiday_overrides",
        "priority_order",
        "coverage_schedules",
        "excluded_agent_ids",
    ):
        if key in raw and raw[key] is not None:
            merged[key] = raw[key]
    try:
        merged["extra_offs_per_agent"] = max(0, int(merged.get("extra_offs_per_agent") or 0))
    except (TypeError, ValueError):
        merged["extra_offs_per_agent"] = 0
    schedules = merged.get("coverage_schedules") or DEFAULT_COVERAGE_SCHEDULES
    merged["coverage_schedules"] = [normalize_schedule_value(s) for s in schedules]
    raw_excluded = merged.get("excluded_agent_ids")
    if raw_excluded is not None:
        merged["excluded_agent_ids"] = [
            str(item).strip()
            for item in raw_excluded
            if str(item or "").strip()
        ]
    return merged


def load_holidays_for_month(reference_month: date) -> list[Holiday | CalendarHoliday]:
    start = reference_month.replace(day=1)
    if start.month == 12:
        end = date(start.year + 1, 1, 1) - timedelta(days=1)
    else:
        end = date(start.year, start.month + 1, 1) - timedelta(days=1)
    stored = list(Holiday.objects.filter(date__gte=start, date__lte=end))
    holidays = merge_calculated_holidays(stored, start.year)
    return [holiday for holiday in holidays if start <= holiday.date <= end]


def holiday_applies(holiday: Holiday | CalendarHoliday, location_name: str) -> bool:
    loc = normalize_holiday_location(holiday.location)
    if not loc:
        return True
    return loc == normalize_holiday_location(location_name)


def is_weekend_excluded(day: date, configuration: dict) -> bool:
    if day.weekday() == 5 and not configuration.get("include_saturdays", True):
        return True
    if day.weekday() == 6 and not configuration.get("include_sundays", False):
        return True
    return False


def normalize_schedule_value(value: str) -> str:
    text = normalize_time_schedule(str(value or "").strip())
    if is_time_range_schedule(text):
        return text
    return str(value or "").strip().upper()


def is_work_day_value(value: str) -> bool:
    return is_time_range_schedule(normalize_time_schedule(value))


def is_5x2_schedule(schedule: str, journey_shift: str = "") -> bool:
    """Escala 5x2 (turno Integral): trabalha seg–sex, folga todo fim de semana."""
    from apps.workforce.services.journey_shift import resolve_journey_shift

    from .constants import SCALE_5X2_SHIFTS

    shift = (journey_shift or "").strip() or resolve_journey_shift(schedule or "")
    return shift.casefold() in SCALE_5X2_SHIFTS


def hours_covered_by_schedule(schedule: str) -> frozenset[int]:
    """
    Horas do dia (0–23) cobertas pelo horário.
    Turno noturno (ex. 23:30–05:30) cobre do início até 23 e de 0 até o fim.
    """
    text = normalize_time_schedule(schedule or "")
    if not is_time_range_schedule(text):
        return frozenset()
    start_min, end_min = parse_work_schedule(text)
    hours: set[int] = set()
    if end_min <= start_min:
        # Cruza meia-noite: start→24h e 0→end
        for minute in range(start_min, 24 * 60):
            hours.add(minute // 60)
        for minute in range(0, end_min):
            hours.add(minute // 60)
    else:
        for minute in range(start_min, end_min):
            hours.add(minute // 60)
    return frozenset(hours)


def schedule_covers_hour(schedule: str, hour: int) -> bool:
    return hour in hours_covered_by_schedule(schedule)


def is_absence_value(value: str, absence_codes: list[str]) -> bool:
    key = str(value or "").strip().casefold()
    return key in {c.casefold() for c in absence_codes}


def rest_hours_between(prev_schedule: str, next_schedule: str) -> float | None:
    if not is_work_day_value(prev_schedule) or not is_work_day_value(next_schedule):
        return None
    _, prev_end = parse_work_schedule(normalize_time_schedule(prev_schedule))
    next_start, _ = parse_work_schedule(normalize_time_schedule(next_schedule))
    # Assume consecutive calendar days: rest = (24h - prev_end) + next_start
    minutes = (24 * 60 - prev_end) + next_start
    if is_night_shift_crossing(prev_schedule):
        # Night shift ends next calendar morning; rest starts at end_min
        minutes = next_start - prev_end
        if minutes < 0:
            minutes += 24 * 60
    return minutes / 60.0


def _entry_agent_id(entry: dict):
    agent = entry.get("agent")
    return getattr(agent, "id", agent)


def effective_work_schedule(entry: dict | None) -> str:
    """Retorna o horário efetivamente trabalhado ou vazio para folga/ausência."""
    if not entry:
        return ""
    day_value = str(entry.get("day_value") or "")
    if is_work_day_value(day_value):
        return normalize_schedule_value(day_value)
    if not day_value:
        schedule = str(entry.get("schedule") or "")
        if is_work_day_value(schedule):
            return normalize_schedule_value(schedule)
    return ""


def proposed_work_breaks_rest(
    entries: list[dict],
    entry: dict,
    new_schedule: str,
    min_rest_hours: float,
) -> bool:
    """Valida interjornada anterior e seguinte ao ativar uma pessoa em folga."""
    if min_rest_hours <= 0:
        return False
    day = entry["date"]
    agent_id = _entry_agent_id(entry)
    by_date = {
        item["date"]: item
        for item in entries
        if _entry_agent_id(item) == agent_id
    }
    previous = effective_work_schedule(by_date.get(day - timedelta(days=1)))
    following = effective_work_schedule(by_date.get(day + timedelta(days=1)))
    target = normalize_schedule_value(new_schedule)
    if previous:
        rest = rest_hours_between(previous, target)
        if rest is not None and rest < min_rest_hours:
            return True
    if following:
        rest = rest_hours_between(target, following)
        if rest is not None and rest < min_rest_hours:
            return True
    return False


def proposed_work_breaks_consecutive_days(
    entries: list[dict],
    entry: dict,
    max_consecutive_work_days: int,
) -> bool:
    """Valida a sequência total resultante ao transformar uma folga em trabalho."""
    if max_consecutive_work_days <= 0:
        return False
    day = entry["date"]
    agent_id = _entry_agent_id(entry)
    worked_dates = {
        item["date"]
        for item in entries
        if _entry_agent_id(item) == agent_id and effective_work_schedule(item)
    }
    worked_dates.add(day)
    streak = 1
    cursor = day - timedelta(days=1)
    while cursor in worked_dates:
        streak += 1
        cursor -= timedelta(days=1)
    cursor = day + timedelta(days=1)
    while cursor in worked_dates:
        streak += 1
        cursor += timedelta(days=1)
    return streak > max_consecutive_work_days

def weekday_allowed(day: date, weekdays: list[str] | None) -> bool:
    if not weekdays:
        return True
    key = WEEKDAY_KEYS[day.weekday()]
    normalized = {str(w).strip().casefold() for w in weekdays}
    aliases = {
        "mon": {"mon", "seg", "segunda"},
        "tue": {"tue", "ter", "terca", "terça"},
        "wed": {"wed", "qua", "quarta"},
        "thu": {"thu", "qui", "quinta"},
        "fri": {"fri", "sex", "sexta"},
        "sat": {"sat", "sab", "sábado", "sabado"},
        "sun": {"sun", "dom", "domingo"},
    }
    allowed_keys = set()
    for wd, names in aliases.items():
        if normalized & names:
            allowed_keys.add(wd)
    if not allowed_keys:
        allowed_keys = normalized
    return key in allowed_keys or key.casefold() in normalized


@dataclass
class ScheduleLimit:
    schedule: str
    min_count: int
    max_count: int | None
    weekdays: list[str]
    activity: str = ""
    team: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduleLimit":
        return cls(
            schedule=normalize_schedule_value(data.get("schedule") or data.get("horario") or ""),
            min_count=int(data.get("min_count") or data.get("minimo") or 0),
            max_count=(
                int(data["max_count"])
                if data.get("max_count") is not None
                else (int(data["maximo"]) if data.get("maximo") is not None else None)
            ),
            weekdays=list(data.get("weekdays") or data.get("dias") or []),
            activity=str(data.get("activity") or data.get("atividade") or ""),
            team=str(data.get("team") or data.get("equipe") or ""),
        )


@dataclass
class ActivityCoverage:
    activity: str
    min_coverage: int
    max_coverage: int | None
    weekdays: list[str]
    schedules: list[str]
    priority: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> "ActivityCoverage":
        schedules = data.get("schedules") or data.get("horarios") or []
        return cls(
            activity=str(data.get("activity") or data.get("atividade") or ""),
            min_coverage=int(data.get("min_coverage") or data.get("cobertura_minima") or 0),
            max_coverage=(
                int(data["max_coverage"])
                if data.get("max_coverage") is not None
                else (
                    int(data["cobertura_maxima"])
                    if data.get("cobertura_maxima") is not None
                    else None
                )
            ),
            weekdays=list(data.get("weekdays") or data.get("dias") or []),
            schedules=[normalize_schedule_value(s) for s in schedules],
            priority=int(data.get("priority") or data.get("prioridade") or 0),
        )


def parse_schedule_limits(configuration: dict) -> list[ScheduleLimit]:
    return [ScheduleLimit.from_dict(item) for item in configuration.get("schedule_limits") or []]


def parse_activity_coverage(configuration: dict) -> list[ActivityCoverage]:
    from .constants import DEFAULT_COVERAGE_SCHEDULES

    default_schedules = [
        normalize_schedule_value(s)
        for s in (configuration.get("coverage_schedules") or DEFAULT_COVERAGE_SCHEDULES)
    ]
    items: list[ActivityCoverage] = []
    for item in configuration.get("activity_coverage") or []:
        cov = ActivityCoverage.from_dict(item)
        # Sem horários explícitos: aplica só os slots de cobertura (nunca noturno).
        if not cov.schedules:
            cov.schedules = list(default_schedules)
        items.append(cov)
    return sorted(items, key=lambda x: (-x.priority, x.activity))


def holiday_override_for(
    day: date,
    location_name: str,
    configuration: dict,
    holidays: list[Holiday | CalendarHoliday],
) -> dict[str, Any] | None:
    for override in configuration.get("holiday_overrides") or []:
        ov_date = override.get("date")
        if isinstance(ov_date, str):
            ov_date = date.fromisoformat(ov_date)
        if ov_date != day:
            continue
        ov_loc = normalize_holiday_location(override.get("location") or "")
        if ov_loc and ov_loc != normalize_holiday_location(location_name):
            continue
        return override
    matching = [
        h for h in holidays if h.date == day and holiday_applies(h, location_name)
    ]
    if not matching:
        return None
    return {
        "date": day.isoformat(),
        "name": matching[0].name,
        "location": matching[0].location,
        "operate": configuration.get("include_holidays", False),
        "treatment": "work" if configuration.get("include_holidays", False) else "off",
    }
