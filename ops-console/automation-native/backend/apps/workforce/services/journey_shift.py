"""Deriva turno (journey_shift) a partir da jornada HH:MM - HH:MM."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_JOURNEY_RE = re.compile(
    r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$",
)

INSS_TYPE_CHOICES = (
    "Licença maternidade",
    "Afastamento INSS",
)

EXTERNAL_MOVEMENT_CHOICES = (
    "Promoção externa",
    "Desligamento Voluntário",
    "Desligamento Involuntário",
)

TERMINATION_MOVEMENT_CHOICES = (
    "Desligamento Voluntário",
    "Desligamento Involuntário",
)


def resolve_journey_shift(journey: str) -> str:
    """Classifica turno pelo horário de início (e Integral se duração ≥ 8h diurna)."""
    match = _JOURNEY_RE.match(journey or "")
    if not match:
        return ""
    h1, m1, h2, m2 = (int(match.group(i)) for i in range(1, 5))
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return ""
    start = h1 * 60 + m1
    end = h2 * 60 + m2
    if end <= start:
        end += 24 * 60
    duration = end - start

    # Jornada longa começando de manhã → Integral
    if duration >= 8 * 60 and 6 * 60 <= start < 12 * 60:
        return "Integral"

    if start < 6 * 60:
        return "Madrugada"
    if start < 12 * 60:
        return "Matutino"
    if start < 15 * 60:
        return "Intermediário"
    if start < 18 * 60:
        return "Vespertino"
    return "Noturno"


def parse_hms_to_hours(value: str | None) -> Decimal | None:
    """Converte 'HH:MM:SS' (ou parcial) em horas decimais. Vazio → None."""
    text = (value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError("Formato inválido. Use HH:MM:SS.")
    try:
        h = int(parts[0] or 0)
        m = int(parts[1]) if len(parts) > 1 and parts[1] != "" else 0
        s = int(parts[2]) if len(parts) > 2 and parts[2] != "" else 0
    except ValueError as exc:
        raise ValueError("Formato inválido. Use HH:MM:SS.") from exc
    if h < 0 or m < 0 or s < 0 or m > 59 or s > 59:
        raise ValueError("Horário inválido.")
    total_seconds = h * 3600 + m * 60 + s
    if total_seconds == 0:
        return None
    hours = (Decimal(total_seconds) / Decimal(3600)).quantize(Decimal("0.01"))
    if hours > Decimal("999.99"):
        raise ValueError("Desconto excede o máximo permitido.")
    return hours


def hours_to_hms(value: Decimal | float | str | None) -> str:
    """Converte horas decimais em HH:MM:SS."""
    if value is None or value == "":
        return ""
    try:
        hours = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ""
    if hours <= 0:
        return ""
    total_seconds = int(round(float(hours) * 3600))
    h, rem = divmod(total_seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
