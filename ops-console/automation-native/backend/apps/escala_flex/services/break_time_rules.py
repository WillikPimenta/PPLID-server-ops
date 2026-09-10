"""Regras de horário de saída para intervalo."""

from datetime import datetime

from .schedule_utils import (
    is_time_range_schedule,
    normalize_time_schedule,
    parse_work_schedule,
)

WORK_BEFORE_BREAK_MINUTES = 60
WORK_AFTER_BREAK_MINUTES = 60
STANDARD_BREAK_DURATION_MINUTES = 15
EXTRA_BREAK_DURATION_MINUTES = 60


def _minutes_to_hhmm(total_minutes: int) -> str:
    total_minutes %= 24 * 60
    hour, minute = divmod(total_minutes, 60)
    return f"{hour:02d}:{minute:02d}"


def _hhmm_to_minutes(value: str) -> int:
    parsed = datetime.strptime(value, "%H:%M")
    return parsed.hour * 60 + parsed.minute


def extra_break_duration_minutes(*, overtime: bool) -> int:
    """Intervalo extra: 1h com hora extra no dia; 15 min caso contrário."""
    return EXTRA_BREAK_DURATION_MINUTES if overtime else STANDARD_BREAK_DURATION_MINUTES


def break_duration_minutes(*, extra: bool, overtime: bool = False) -> int:
    if extra:
        return extra_break_duration_minutes(overtime=overtime)
    return STANDARD_BREAK_DURATION_MINUTES


def uses_extra_break_interval(*, overtime: bool, is_weekend: bool) -> bool:
    return is_weekend or overtime


def break_exit_bounds(
    work_schedule: str,
    *,
    extra: bool = False,
    overtime: bool = False,
) -> tuple[str, str] | None:
    """
    Faixa permitida para saída ao intervalo:
    - mínimo: 1h após início do turno
    - máximo: 1h de trabalho após o intervalo + duração do intervalo
      (15 min padrão; extra: 1h com hora extra no dia, 15 min sem hora extra)
    """
    normalized = normalize_time_schedule(str(work_schedule or "").strip())
    if not is_time_range_schedule(normalized):
        return None

    start_min, end_min = parse_work_schedule(normalized)
    if start_min == 0 and end_min == 0:
        return None
    if end_min < start_min:
        end_min += 24 * 60

    break_duration = break_duration_minutes(extra=extra, overtime=overtime)
    min_exit = start_min + WORK_BEFORE_BREAK_MINUTES
    max_exit = end_min - WORK_AFTER_BREAK_MINUTES - break_duration
    if min_exit > max_exit:
        return None

    return _minutes_to_hhmm(min_exit), _minutes_to_hhmm(max_exit)


def validate_break_exit_against_schedule(
    work_schedule: str,
    exit_time: str,
    *,
    extra: bool = False,
    overtime: bool = False,
) -> None:
    if not str(exit_time or "").strip():
        return

    bounds = break_exit_bounds(work_schedule, extra=extra, overtime=overtime)
    if not bounds:
        raise ValueError(
            "Horário padrão inválido ou turno curto demais para cadastrar intervalo."
        )

    min_exit, max_exit = bounds
    exit_min = _hhmm_to_minutes(exit_time)
    min_min = _hhmm_to_minutes(min_exit)
    max_min = _hhmm_to_minutes(max_exit)

    if exit_min < min_min or exit_min > max_min:
        interval_label = "intervalo extra" if extra else "intervalo semanal"
        raise ValueError(
            f"Horário de {interval_label} inválido para a escala "
            f"{normalize_time_schedule(work_schedule)}. "
            f"Informe um horário entre {min_exit} e {max_exit}."
        )
