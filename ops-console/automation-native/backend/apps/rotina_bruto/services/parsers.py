# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd
from django.utils import timezone


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    return str(value).strip() == ""


def parse_date(value: Any) -> date | None:
    if _is_empty(value):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()

    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return parsed.date()


def parse_date_series(values: Any) -> list[date | None]:
    """Parse uma coluna de datas de uma vez (bem mais rápido que parse_date em loop)."""
    if values is None:
        return []
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    if series.empty:
        return []
    # format="mixed": ISO + DD/MM in the same column (infer alone sticks to first format).
    parsed = pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")
    out: list[date | None] = []
    for ts in parsed:
        if pd.isna(ts):
            out.append(None)
        else:
            out.append(ts.date())
    return out


def _as_aware(dt: datetime) -> datetime:
    """Datas do parquet/Excel são horário local (America/Sao_Paulo); Django USE_TZ exige aware."""
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt, timezone.get_current_timezone())


def parse_datetime(value: Any) -> datetime | None:
    if _is_empty(value):
        return None
    if isinstance(value, datetime):
        return _as_aware(value)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return _as_aware(value.to_pydatetime())

    parsed = pd.to_datetime(value, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return None
    return _as_aware(parsed.to_pydatetime())


def parse_datetime_series(values: Any) -> list[datetime | None]:
    """Parse uma coluna de datetimes de uma vez (paridade com parse_datetime)."""
    if values is None:
        return []
    series = values if isinstance(values, pd.Series) else pd.Series(values)
    if series.empty:
        return []
    parsed = pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")
    out: list[datetime | None] = []
    for ts in parsed:
        if pd.isna(ts):
            out.append(None)
        else:
            out.append(_as_aware(ts.to_pydatetime()))
    return out


def parse_protocolo(value: Any) -> int | None:
    if _is_empty(value):
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_tempo_segundos(value: Any) -> int | None:
    if _is_empty(value):
        return None

    if isinstance(value, pd.Timedelta):
        if pd.isna(value):
            return None
        return max(0, int(value.total_seconds()))

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and pd.isna(value):
            return None
        return max(0, int(value))

    text = str(value).strip()
    if not text:
        return None

    try:
        return max(0, int(float(text.replace(",", "."))))
    except ValueError:
        pass

    try:
        td = pd.to_timedelta(text, errors="coerce")
        if not pd.isna(td):
            return max(0, int(td.total_seconds()))
    except (ValueError, TypeError):
        pass

    return None
