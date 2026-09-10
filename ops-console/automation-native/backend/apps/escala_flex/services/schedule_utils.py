"""Utilitários de escala (horários, turno noturno)."""

import re
from datetime import datetime, timedelta

from django.utils import timezone

TIME_SCHEDULE_RE = re.compile(r"^\d{2}:\d{2}\s*-\s*\d{2}:\d{2}$")


def is_absence_dia_escala(value: str) -> bool:
    from .absence_types import is_absence_code

    return is_absence_code(value)


def work_schedule_from_dia_escala(dia_escala: str) -> str:
    """Retorna hh:mm - hh:mm se dia_escala for horário; vazio para ausências/outros."""
    if is_absence_dia_escala(dia_escala):
        return ""
    ws = normalize_time_schedule(str(dia_escala or ""))
    return ws if is_time_range_schedule(ws) else ""


def resolve_escala_work_schedule(dia_escala: str, horario: str = "") -> str:
    """
    Resolve a escala efetiva do dia (mesma regra da Consulta de Escala):
    - dia_escala preenchido: usa dia_escala (exclui FERIAS/FOLGA/BH/AFASTADO)
    - dia_escala vazio: usa horario padrão
    """
    dia = str(dia_escala or "").strip()
    if dia:
        return work_schedule_from_dia_escala(dia)
    hor = normalize_time_schedule(str(horario or ""))
    return hor if is_time_range_schedule(hor) else ""


def normalize_time_schedule(value: str) -> str:
    """Normaliza texto de horário para hh:mm - hh:mm quando possível."""
    if not value:
        return ""
    text = str(value).strip()
    parts = re.split(r"\s*-\s*", text)
    if len(parts) == 2:
        try:
            start = datetime.strptime(parts[0].strip(), "%H:%M").strftime("%H:%M")
            end = datetime.strptime(parts[1].strip(), "%H:%M").strftime("%H:%M")
            return f"{start} - {end}"
        except ValueError:
            pass
    return text


def is_time_range_schedule(work_schedule: str) -> bool:
    """True se a escala está no formato hh:mm - hh:mm."""
    normalized = normalize_time_schedule(work_schedule or "")
    return bool(TIME_SCHEDULE_RE.match(normalized))


def resolve_default_schedule(*, horario: str = "", journey: str = "") -> str:
    """
    Horário padrão alinhado à Consulta de Escala:
    prioriza Escala.horario; se vazio ou inválido, usa journey do headcount quando for faixa.
    """
    hor = normalize_time_schedule(str(horario or "").strip())
    if is_time_range_schedule(hor):
        return hor

    journey_text = str(journey or "").strip()
    if not journey_text:
        return hor

    journey_normalized = normalize_time_schedule(journey_text)
    if is_time_range_schedule(journey_normalized):
        return journey_normalized

    return hor or journey_text


def parse_work_schedule(work_schedule: str) -> tuple[int, int]:
    """Retorna (inicio_min, fim_min) em minutos desde meia-noite."""
    if not work_schedule or len(work_schedule) < 11:
        return 0, 0
    start_str = work_schedule[:5]
    end_str = work_schedule[-5:]
    try:
        start = datetime.strptime(start_str, "%H:%M").time()
        end = datetime.strptime(end_str, "%H:%M").time()
    except ValueError:
        return 0, 0
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    return start_min, end_min


def is_night_shift_crossing(work_schedule: str) -> bool:
    start_min, end_min = parse_work_schedule(work_schedule)
    return end_min < start_min


def schedule_start_datetime(date, work_schedule: str):
    """DateTime de início da escala no dia."""
    if not work_schedule:
        return None
    start_str = work_schedule[:5]
    try:
        t = datetime.strptime(start_str, "%H:%M").time()
    except ValueError:
        return None
    return datetime.combine(date, t)


def schedule_end_datetime(
    schedule_date,
    work_schedule: str,
    *,
    is_previous_night_shift: bool = False,
):
    """DateTime de fim da escala (timezone-aware quando USE_TZ)."""
    normalized = normalize_time_schedule(work_schedule or "")
    if not is_time_range_schedule(normalized):
        return None

    start_min, end_min = parse_work_schedule(normalized)
    if start_min == 0 and end_min == 0:
        return None

    end_str = normalized[-5:]
    try:
        end_time = datetime.strptime(end_str, "%H:%M").time()
    except ValueError:
        return None

    if is_previous_night_shift:
        end_date = schedule_date
    elif end_min < start_min:
        end_date = schedule_date + timedelta(days=1)
    else:
        end_date = schedule_date

    naive = datetime.combine(end_date, end_time)
    if timezone.is_naive(naive):
        return timezone.make_aware(naive)
    return naive


def is_shift_active_at(
    reference_time,
    schedule_date,
    work_schedule: str,
    *,
    is_previous_night_shift: bool = False,
) -> bool:
    """True se reference_time está dentro do intervalo da escala no dia."""
    normalized = normalize_time_schedule(work_schedule or "")
    if not is_time_range_schedule(normalized):
        return False

    if is_previous_night_shift:
        end_dt = schedule_end_datetime(
            schedule_date,
            normalized,
            is_previous_night_shift=True,
        )
        if not end_dt:
            return False
        day_start = timezone.make_aware(
            datetime.combine(schedule_date, datetime.min.time())
        )
        return day_start <= reference_time < end_dt

    start_dt = schedule_start_datetime(schedule_date, normalized)
    end_dt = schedule_end_datetime(
        schedule_date,
        normalized,
        is_previous_night_shift=False,
    )
    if not start_dt or not end_dt:
        return False

    start_aware = (
        timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt
    )
    end_aware = end_dt if not timezone.is_naive(end_dt) else timezone.make_aware(end_dt)
    return start_aware <= reference_time < end_aware
