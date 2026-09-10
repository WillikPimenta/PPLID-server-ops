"""Calendário calculado usado quando a dimensão de feriados está incompleta."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import re
import unicodedata


@dataclass(frozen=True)
class CalendarHoliday:
    date: date
    name: str
    location: str = ""
    holiday_type: str = "national"


def normalize_holiday_location(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    if not text or text in {"nacional", "brasil", "todas", "todos"}:
        return ""
    if "sao carlos" in text:
        return "sao carlos"
    if "brasilia" in text or "distrito federal" in text or text == "df":
        return "brasilia"
    return text


def easter_sunday(year: int) -> date:
    """Algoritmo gregoriano de Meeus/Jones/Butcher."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def calculated_holidays(year: int) -> list[CalendarHoliday]:
    easter = easter_sunday(year)
    national = [
        (1, 1, "Confraternização Universal"),
        (4, 21, "Tiradentes"),
        (5, 1, "Dia Mundial do Trabalho"),
        (9, 7, "Independência do Brasil"),
        (10, 12, "Nossa Senhora Aparecida"),
        (11, 2, "Finados"),
        (11, 15, "Proclamação da República"),
        (11, 20, "Dia Nacional de Zumbi e da Consciência Negra"),
        (12, 25, "Natal"),
    ]
    holidays = [
        CalendarHoliday(date(year, month, day), name)
        for month, day, name in national
    ]

    good_friday = easter - timedelta(days=2)
    corpus_christi = easter + timedelta(days=60)

    holidays.extend(
        [
            CalendarHoliday(good_friday, "Paixão de Cristo", "São Carlos", "municipal"),
            CalendarHoliday(corpus_christi, "Corpus Christi", "São Carlos", "municipal"),
            CalendarHoliday(date(year, 7, 9), "Revolução Constitucionalista", "São Carlos", "estadual"),
            CalendarHoliday(date(year, 8, 15), "Nossa Senhora da Babilônia", "São Carlos", "municipal"),
            CalendarHoliday(date(year, 11, 4), "Aniversário de São Carlos", "São Carlos", "municipal"),
            CalendarHoliday(good_friday, "Paixão de Cristo", "Brasília", "distrital"),
            CalendarHoliday(date(year, 4, 21), "Aniversário de Brasília", "Brasília", "distrital"),
            CalendarHoliday(corpus_christi, "Corpus Christi", "Brasília", "distrital"),
            CalendarHoliday(date(year, 11, 30), "Dia do Evangélico", "Brasília", "distrital"),
        ]
    )
    return sorted(holidays, key=lambda item: (item.date, item.location, item.name))


def merge_calculated_holidays(stored: list, year: int) -> list:
    """Preserva a dimensão cadastrada e completa somente eventos ausentes."""
    merged = list(stored)
    known = {
        (
            item.date,
            normalize_holiday_location(getattr(item, "location", "")),
            str(getattr(item, "name", "")).strip().casefold(),
        )
        for item in stored
    }
    for holiday in calculated_holidays(year):
        key = (
            holiday.date,
            normalize_holiday_location(holiday.location),
            holiday.name.strip().casefold(),
        )
        if key not in known:
            merged.append(holiday)
            known.add(key)
    return sorted(merged, key=lambda item: (item.date, item.location, item.name))