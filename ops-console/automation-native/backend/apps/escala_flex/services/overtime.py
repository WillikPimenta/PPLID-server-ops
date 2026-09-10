"""Regras de hora extra (HE) a partir da faixa horária da escala."""

from decimal import Decimal

STANDARD_WORK_HOURS = Decimal("6")
LUNCH_HOURS = Decimal("1")
MIN_OVERTIME_HOURS = Decimal("1")
MAX_OVERTIME_HOURS = Decimal("2")


def hours_from_work_schedule(work_schedule: str) -> Decimal | None:
    from .schedule_utils import parse_work_schedule

    start_min, end_min = parse_work_schedule(work_schedule)
    if start_min == 0 and end_min == 0:
        return None
    if end_min < start_min:
        duration_min = (24 * 60 - start_min) + end_min
    else:
        duration_min = end_min - start_min
    return Decimal(str(round(duration_min / 60, 1)))


def overtime_hours_from_span(span_hours: Decimal) -> Decimal:
    """Horas extras efetivas: desconta 1h de almoço e jornada padrão de 6h."""
    if span_hours <= STANDARD_WORK_HOURS:
        return Decimal("0")
    net_work = span_hours - LUNCH_HOURS
    return max(Decimal("0"), net_work - STANDARD_WORK_HOURS)


def compute_overtime_flag(work_schedule: str) -> bool:
    span_hours = hours_from_work_schedule(work_schedule)
    if span_hours is None:
        return False
    ot_hours = overtime_hours_from_span(span_hours)
    return MIN_OVERTIME_HOURS <= ot_hours <= MAX_OVERTIME_HOURS


def validate_schedule_overtime(work_schedule: str) -> None:
    span_hours = hours_from_work_schedule(work_schedule)
    if span_hours is None:
        return
    ot_hours = overtime_hours_from_span(span_hours)
    if ot_hours > MAX_OVERTIME_HOURS:
        raise ValueError(
            "Hora extra inválida: o máximo permitido é 2 horas além da jornada "
            "de 6 horas (desconsiderando 1 hora de almoço)."
        )


def is_standard_workload(work_schedule: str) -> bool:
    span_hours = hours_from_work_schedule(work_schedule)
    return span_hours is not None and span_hours == STANDARD_WORK_HOURS


def validate_standard_workload(work_schedule: str) -> None:
    if not is_standard_workload(work_schedule):
        raise ValueError("A carga horária deve ser igual a 6 horas.")
