"""Constantes e configuração padrão da geração mensal."""

from __future__ import annotations

TARGET_JOB_TITLE = "Assistente de Planejamento Operacional II"

DEFAULT_ABSENCE_CODES = ["FOLGA", "FERIAS", "AFASTADO", "BH"]

# Únicos horários que entram na regra de cobertura mínima.
# Turno noturno (ex.: 23:30 - 05:30) fica de fora — não há gente suficiente.
DEFAULT_COVERAGE_SCHEDULES = [
    "06:00 - 12:00",
    "11:00 - 17:00",
    "17:00 - 23:00",
]

# Turno Integral (jornada longa diurna, ex. 08:30-18:00) = escala 5x2:
# folga fixa em todos os sábados e domingos; não entra em cobertura de fim de semana.
SCALE_5X2_SHIFTS = {"integral"}

PRIORITY_ORDER = [
    "agent_active",
    "absences",
    "journey_restriction",
    "rest_between_shifts",
    "activity_min_coverage",
    "schedule_max_limit",
    "offs",
    "default_schedule",
    "balance",
]


def default_configuration() -> dict:
    return {
        "allow_night_shift": False,
        "replace_existing": False,
        # Fim de semana como folga por padrão (operação em Sáb/Dom é opt-in).
        "include_saturdays": False,
        "include_sundays": False,
        "include_holidays": False,
        "default_off_rule": "weekend_prefer",
        "max_consecutive_work_days": 6,
        "min_rest_hours": 11,
        # Folgas além do fim de semana / regra de consecutivos.
        "extra_offs_per_agent": 0,
        "target_job_title": TARGET_JOB_TITLE,
        "coverage_schedules": list(DEFAULT_COVERAGE_SCHEDULES),
        "schedule_limits": [],
        "activity_coverage": [],
        "absence_codes": list(DEFAULT_ABSENCE_CODES),
        "holiday_overrides": [],
        "priority_order": list(PRIORITY_ORDER),
        "excluded_agent_ids": [],
    }
