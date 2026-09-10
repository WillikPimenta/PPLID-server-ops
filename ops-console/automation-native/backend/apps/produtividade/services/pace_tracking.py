# -*- coding: utf-8 -*-
"""Ritmo acumulado vs forecast horário ponderado pelo tempo logado."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time
from typing import Any

from django.db.models import QuerySet
from django.utils import timezone

from apps.produtividade.services.goal_adjustment import META_JORNADA_SECONDS
from apps.produtividade.services.hourly_forecast import (
    HOURLY_THRESHOLD,
    _dates_from_queryset,
    _matriculas_from_queryset,
    build_schedule_context_lookup,
    threshold_for_agent_hour,
)

NEAR_PP = 3.0
BEHIND_PP = 8.0
PACE_DAILY_TARGET = 95.0
PRODUCTIVE_HOURS_DEFAULT = META_JORNADA_SECONDS / 3600.0  # 5.5h


def classify_pace_delta(delta_pp: float | None) -> str:
    if delta_pp is None:
        return "unknown"
    if delta_pp >= -NEAR_PP:
        return "ok"
    if delta_pp >= -BEHIND_PP:
        return "near"
    return "behind"


def atingimento_from_pace(actual: float | None, expected: float | None) -> float | None:
    """Aproveitamento = realizado ÷ esperado × 100 (1 casa decimal)."""
    if actual is None or expected is None or expected <= 0:
        return None
    return round((actual / expected) * 100, 1)


def pace_snapshot_dict(
    *,
    actual: float | None,
    expected: float | None,
    delta_pp: float | None,
    status: str,
    days_count: int,
    pace_by_day: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "pace_actual_pct": actual,
        "pace_expected_pct": expected,
        "pace_delta_pp": delta_pp,
        "pace_status": status,
        "pace_days_count": days_count,
        "pace_daily_target": PACE_DAILY_TARGET,
        "atingimento_intraday_pct": atingimento_from_pace(actual, expected),
    }
    if pace_by_day is not None:
        payload["pace_by_day"] = pace_by_day
    return payload


def unknown_pace_snapshot() -> dict[str, Any]:
    return pace_snapshot_dict(
        actual=None,
        expected=None,
        delta_pp=None,
        status="unknown",
        days_count=0,
    )


def _monitor_tabela_for_qs(qs: QuerySet) -> list[dict[str, Any]]:
    from apps.produtividade.services.monitor_bridge import get_monitor_tabela_rows_for_qs

    return get_monitor_tabela_rows_for_qs(qs)


def build_monitor_hourly_logado_lookup(
    qs: QuerySet,
) -> dict[tuple[str, date, int], int]:
    """Mapa (matricula, data_jornada, hora) -> tempo_logado em segundos."""
    lookup: dict[tuple[str, date, int], int] = {}
    for row in _monitor_tabela_for_qs(qs):
        if row.get("hora") is None:
            continue
        matricula = str(row.get("matricula_usuario") or "").strip().lower()
        data_jornada = row.get("data_jornada")
        if not matricula or not data_jornada:
            continue
        day = date.fromisoformat(str(data_jornada)[:10])
        hour = int(row["hora"])
        logado = int(row.get("tempo_logado") or 0)
        if logado <= 0:
            continue
        key = (matricula, day, hour)
        lookup[key] = lookup.get(key, 0) + logado
    return lookup


def build_monitor_daily_logado_lookup(qs: QuerySet) -> dict[tuple[str, date], int]:
    hourly_totals: dict[tuple[str, date], int] = defaultdict(int)
    null_totals: dict[tuple[str, date], int] = {}
    for row in _monitor_tabela_for_qs(qs):
        matricula = str(row.get("matricula_usuario") or "").strip().lower()
        data_jornada = row.get("data_jornada")
        if not matricula or not data_jornada:
            continue
        day = date.fromisoformat(str(data_jornada)[:10])
        key = (matricula, day)
        if row.get("hora") is None:
            null_totals[key] = int(row.get("tempo_logado_dia") or 0)
            continue
        logado = int(row.get("tempo_logado") or 0)
        if logado > 0:
            hourly_totals[key] += logado

    result = dict(hourly_totals)
    for key, val in null_totals.items():
        if key not in result and val > 0:
            result[key] = val
    return result


def build_daily_actual_pct_map(qs: QuerySet) -> dict[tuple[str, date], float]:
    """Soma % count por (matricula, data_jornada) — alinhado ao tempo logado do monitor.

    Usa corte 05:15 (não o dia civil de recorded_at), senão madrugada
    (00:00–05:14) nunca casa realizado × esperado e o pace fica unknown.
    """
    from collections import defaultdict

    from apps.produtividade.services.analytics import (
        _iter_shift_groups,
        _shift_group_jornada_map,
        _shift_group_metrics,
    )

    key_jornada = _shift_group_jornada_map(qs)
    # Preferir count com meta ajustada (mesma base do volume); se a meta
    # virar 0 no abatimento, cai no bruto só para não perder o dia no pace.
    adjusted = _iter_shift_groups(qs, adjust_goal=True)
    raw = _iter_shift_groups(qs, adjust_goal=False)
    per_jornada: dict[tuple[str, date], float] = defaultdict(float)

    for key, totals in adjusted.items():
        matricula, civil_day, etapa = key
        jornada = key_jornada.get(key, civil_day)
        metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        pct = metrics.get("productivity_pct_count")
        if pct is None:
            raw_totals = raw.get(key)
            if raw_totals:
                metrics = _shift_group_metrics(
                    raw_totals["count"], raw_totals["seconds"], raw_totals["goal"]
                )
                pct = metrics.get("productivity_pct_count")
        if pct is not None:
            per_jornada[(matricula, jornada)] += pct

    return {key: round(total, 2) for key, total in per_jornada.items()}


def _jornada_dates_from_queryset(qs: QuerySet) -> set[date]:
    from apps.monitor_eventos.services.tabela_monitor import jornada_from_recorded_at

    dates: set[date] = set()
    for recorded_at in qs.values_list("recorded_at", flat=True):
        if not recorded_at:
            continue
        jornada = jornada_from_recorded_at(recorded_at)
        if jornada is not None:
            dates.add(jornada)
    return dates


def _parse_hour_datetime(day: date, hour: int, bucket_start: Any = None) -> datetime:
    tz = timezone.get_current_timezone()
    if bucket_start:
        if isinstance(bucket_start, str):
            parsed = datetime.fromisoformat(bucket_start.replace("Z", "+00:00"))
        elif isinstance(bucket_start, datetime):
            parsed = bucket_start
        else:
            parsed = None
        if parsed is not None:
            if timezone.is_naive(parsed):
                return timezone.make_aware(parsed, tz)
            return timezone.localtime(parsed)
    naive = datetime.combine(day, time(hour=hour))
    return timezone.make_aware(naive, tz)


def full_day_forecast_sum(
    matricula: str,
    day: date,
    schedule_lookup: dict,
) -> float | None:
    """Soma dos thresholds horários do dia (proxy da meta acumulada da jornada)."""
    ctx = schedule_lookup.get((matricula.strip().lower(), day))
    if ctx is not None and ctx.overtime:
        return None
    if ctx is None:
        return round(HOURLY_THRESHOLD * PRODUCTIVE_HOURS_DEFAULT, 2)

    total = 0.0
    for hour in range(24):
        hour_dt = _parse_hour_datetime(day, hour)
        thr = threshold_for_agent_hour(matricula, hour_dt, schedule_lookup)
        if thr is None:
            return None
        total += thr
    return round(total, 2)


def expected_pct_for_day(
    matricula: str,
    day: date,
    hourly_logado: dict[int, int],
    daily_logado: int,
    schedule_lookup: dict,
) -> float | None:
    matricula = matricula.strip().lower()
    expected = 0.0
    hourly_used = False

    for hour, logado_sec in sorted(hourly_logado.items()):
        if logado_sec <= 0:
            continue
        hourly_used = True
        hour_dt = _parse_hour_datetime(day, hour)
        thr = threshold_for_agent_hour(matricula, hour_dt, schedule_lookup)
        if thr is None:
            return None
        weight = min(int(logado_sec), 3600) / 3600.0
        expected += thr * weight

    if hourly_used:
        return round(expected, 2)

    if daily_logado <= 0:
        return None

    full_sum = full_day_forecast_sum(matricula, day, schedule_lookup)
    if full_sum is None:
        return None
    ratio = min(daily_logado, META_JORNADA_SECONDS) / META_JORNADA_SECONDS
    return round(ratio * full_sum, 2)


def _hourly_logado_for_agent_day(
    hourly_lookup: dict[tuple[str, date, int], int],
    matricula: str,
    day: date,
) -> dict[int, int]:
    matricula = matricula.strip().lower()
    result: dict[int, int] = {}
    for (mat, d, hour), seconds in hourly_lookup.items():
        if mat == matricula and d == day:
            result[hour] = seconds
    return result


def build_pace_for_agent(
    matricula: str,
    qs: QuerySet,
    *,
    daily_actual_map: dict[tuple[str, date], float] | None = None,
    hourly_logado_lookup: dict[tuple[str, date, int], int] | None = None,
    daily_logado_lookup: dict[tuple[str, date], int] | None = None,
    schedule_lookup: dict | None = None,
    include_by_day: bool = False,
) -> dict[str, Any]:
    matricula = matricula.strip().lower()
    if daily_actual_map is None:
        daily_actual_map = build_daily_actual_pct_map(qs)
    if hourly_logado_lookup is None:
        hourly_logado_lookup = build_monitor_hourly_logado_lookup(qs)
    if daily_logado_lookup is None:
        daily_logado_lookup = build_monitor_daily_logado_lookup(qs)
    if schedule_lookup is None:
        dates = _jornada_dates_from_queryset(qs) or _dates_from_queryset(qs)
        schedule_lookup = build_schedule_context_lookup({matricula}, dates)

    days = {
        day
        for (mat, day) in daily_actual_map
        if mat == matricula
    } | {
        day
        for (mat, day) in daily_logado_lookup
        if mat == matricula
    }

    actuals: list[float] = []
    expecteds: list[float] = []
    pace_by_day: list[dict[str, Any]] = []

    for day in sorted(days):
        actual_d = daily_actual_map.get((matricula, day))
        logado_d = daily_logado_lookup.get((matricula, day), 0)
        hourly = _hourly_logado_for_agent_day(hourly_logado_lookup, matricula, day)
        if not hourly and logado_d <= 0:
            continue

        expected_d = expected_pct_for_day(
            matricula,
            day,
            hourly,
            logado_d,
            schedule_lookup,
        )
        if expected_d is None or actual_d is None:
            continue

        delta_d = round(actual_d - expected_d, 2)
        actuals.append(actual_d)
        expecteds.append(expected_d)
        if include_by_day:
            pace_by_day.append(
                {
                    "date": day.isoformat(),
                    "pace_actual_pct": actual_d,
                    "pace_expected_pct": expected_d,
                    "pace_delta_pp": delta_d,
                    "pace_status": classify_pace_delta(delta_d),
                }
            )

    if not actuals:
        snap = unknown_pace_snapshot()
        if include_by_day:
            snap["pace_by_day"] = pace_by_day
        return snap

    actual_avg = round(sum(actuals) / len(actuals), 2)
    expected_avg = round(sum(expecteds) / len(expecteds), 2)
    delta_pp = round(actual_avg - expected_avg, 2)
    return pace_snapshot_dict(
        actual=actual_avg,
        expected=expected_avg,
        delta_pp=delta_pp,
        status=classify_pace_delta(delta_pp),
        days_count=len(actuals),
        pace_by_day=pace_by_day if include_by_day else None,
    )


def public_pace_fields(snapshot: dict[str, Any] | None, *, include_by_day: bool = False) -> dict[str, Any]:
    if not snapshot:
        snapshot = unknown_pace_snapshot()
    keys = (
        "pace_actual_pct",
        "pace_expected_pct",
        "pace_delta_pp",
        "pace_status",
        "pace_days_count",
        "pace_daily_target",
        "atingimento_intraday_pct",
    )
    result = {key: snapshot.get(key) for key in keys}
    if result.get("atingimento_intraday_pct") is None:
        result["atingimento_intraday_pct"] = atingimento_from_pace(
            result.get("pace_actual_pct"),
            result.get("pace_expected_pct"),
        )
    if include_by_day:
        result["pace_by_day"] = snapshot.get("pace_by_day") or []
    return result


def build_pace_map_for_qs(qs: QuerySet) -> dict[str, dict[str, Any]]:
    matriculas = _matriculas_from_queryset(qs)
    if not matriculas:
        return {}

    daily_actual_map = build_daily_actual_pct_map(qs)
    hourly_logado_lookup = build_monitor_hourly_logado_lookup(qs)
    daily_logado_lookup = build_monitor_daily_logado_lookup(qs)
    dates = _jornada_dates_from_queryset(qs) or _dates_from_queryset(qs)
    schedule_lookup = build_schedule_context_lookup(matriculas, dates)

    agents = set(matriculas)
    agents.update(mat for mat, _ in daily_actual_map)
    agents.update(mat for mat, _ in daily_logado_lookup)

    result: dict[str, dict[str, Any]] = {}
    for matricula in agents:
        result[matricula] = build_pace_for_agent(
            matricula,
            qs,
            daily_actual_map=daily_actual_map,
            hourly_logado_lookup=hourly_logado_lookup,
            daily_logado_lookup=daily_logado_lookup,
            schedule_lookup=schedule_lookup,
        )
    return result
