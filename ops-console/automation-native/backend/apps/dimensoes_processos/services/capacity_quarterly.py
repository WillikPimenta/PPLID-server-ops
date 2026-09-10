"""Cenario trimestral: redistribui a semana projetada pelos sete weekdays."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum

from apps.dimensoes_processos.services.capacity_volume_esperado import (
    load_projecao_rows_for_range,
    week_start,
)
from apps.monitoramento_sla.models import SlaUtilConsolidado
from apps.monitoramento_sla.services.projecao_lookup import _covers

ZERO = Decimal("0")


def _canonical_projection_by_nh(on_date: date, projection_rows: list) -> dict:
    """Mesmo contrato diario: duplicidade/null nao viram volume valido."""
    candidates: dict[tuple[int, int, int], list] = defaultdict(list)
    for row in projection_rows:
        if not row.workflow.ind_considerar or not row.cliente.operations:
            continue
        if _covers(row, on_date):
            candidates[(row.cliente_id, row.workflow_id, row.nivel_hierarquico_id)].append(row)
    return {
        grain: Decimal(str(rows[0].volume))
        for grain, rows in candidates.items()
        if len(rows) == 1 and rows[0].volume is not None and rows[0].volume > 0
    }


def _previous_complete_quarter(on_date: date) -> tuple[date, date]:
    current_quarter_month = ((on_date.month - 1) // 3) * 3 + 1
    current_quarter_start = date(on_date.year, current_quarter_month, 1)
    previous_end = current_quarter_start - timedelta(days=1)
    previous_start = date(
        previous_end.year,
        ((previous_end.month - 1) // 3) * 3 + 1,
        1,
    )
    return previous_start, previous_end


def _select_complete_quarter(
    reference_date: date,
    *,
    cache: dict | None,
    lookback_quarters: int = 8,
) -> tuple[date, date] | None:
    anchor_start, anchor_end = _previous_complete_quarter(reference_date)
    selection_key = ("complete-quarter", anchor_start, anchor_end, lookback_quarters)
    cached = cache.get(selection_key) if cache is not None else None
    if cached is not None:
        return cached

    earliest = anchor_start
    for _ in range(lookback_quarters - 1):
        earliest, _unused = _previous_complete_quarter(earliest)
    observed_dates = set(
        SlaUtilConsolidado.objects.filter(
            data_cadastro__range=(earliest, anchor_end)
        ).values_list("data_cadastro", flat=True).distinct()
    )
    candidate_start, candidate_end = anchor_start, anchor_end
    selected = None
    for _ in range(lookback_quarters):
        expected_count = (candidate_end - candidate_start).days + 1
        observed_count = sum(
            1 for day in observed_dates if candidate_start <= day <= candidate_end
        )
        if observed_count == expected_count:
            selected = (candidate_start, candidate_end)
            break
        candidate_start, candidate_end = _previous_complete_quarter(candidate_start)
    if cache is not None:
        # Sentinel explicito para diferenciar "nao consultado" de indisponivel.
        cache[selection_key] = selected if selected is not None else False
    return selected


def build_quarterly_weekday_reference(
    on_date: date,
    *,
    cache: dict | None = None,
) -> dict:
    """Aplica shares Mon-Sun historicos ao total SLA projetado da semana.

    A magnitude continua vindo da Projecao SLA. O consolidado dos tres meses-
    calendario completos anteriores define apenas a distribuicao entre os sete
    dias. Sem historico no grao C+WF+NH, conserva-se a projecao do dia e o
    fallback fica explicito no retorno.
    """
    start_of_week = week_start(on_date)
    # Uma semana que cruza trimestre usa uma unica referencia, fixada pela
    # segunda-feira, evitando mudar a curva no meio da escala.
    complete_quarter = _select_complete_quarter(start_of_week, cache=cache)
    if not complete_quarter:
        return {
            "available": False,
            "reason": "no_complete_calendar_quarter",
            "workflow_totals": {},
            "workflow_meta": {},
        }
    hist_from, hist_to = complete_quarter
    cache_key = (start_of_week, hist_from, hist_to)
    cached = cache.get(cache_key) if cache is not None else None
    if cached is None:
        week_dates = [start_of_week + timedelta(days=offset) for offset in range(7)]
        projection_rows = load_projecao_rows_for_range(
            date_min=week_dates[0], date_max=week_dates[-1]
        )
        projection_by_day = {
            day: _canonical_projection_by_nh(day, projection_rows)
            for day in week_dates
        }
        projection_workflow_meta: dict[tuple[int, int], dict] = {}
        for row in projection_rows:
            projection_workflow_meta.setdefault(
                (row.cliente_id, row.workflow_id),
                {
                    "id_cliente": row.cliente_id,
                    "cliente_nome": row.cliente.nome,
                    "id_workflow": row.workflow_id,
                    "workflow_nome": row.workflow.nome,
                    "nh_count": 0,
                },
            )
        weekly_projection: dict[tuple[int, int, int], Decimal] = defaultdict(lambda: ZERO)
        for daily in projection_by_day.values():
            for grain, volume in daily.items():
                weekly_projection[grain] += volume
        workflow_keys = {grain[:2] for grain in weekly_projection}

        history_key = ("history", hist_from, hist_to, frozenset(workflow_keys))
        history_cached = cache.get(history_key) if cache is not None else None
        if history_cached is None:
            history_by_grain_weekday: dict[
                tuple[int, int, int | None, int], Decimal
            ] = defaultdict(lambda: ZERO)
            observed_dates_by_grain_weekday: dict[
                tuple[int, int, int | None, int], set[date]
            ] = defaultdict(set)
            cliente_ids = {key[0] for key in workflow_keys}
            workflow_ids = {key[1] for key in workflow_keys}
            rows = (
                SlaUtilConsolidado.objects.filter(
                    data_cadastro__range=(hist_from, hist_to),
                    id_cliente__in=cliente_ids,
                    id_workflow__in=workflow_ids,
                )
                .values("data_cadastro", "id_cliente", "id_workflow", "id_nh")
                .annotate(volume=Sum("quantidade"))
            )
            for row in rows:
                workflow_key = (int(row["id_cliente"]), int(row["id_workflow"]))
                if workflow_key not in workflow_keys:
                    continue
                grain = (
                    *workflow_key,
                    int(row["id_nh"]) if row["id_nh"] is not None else None,
                    row["data_cadastro"].weekday(),
                )
                history_by_grain_weekday[grain] += Decimal(str(row["volume"] or 0))
                observed_dates_by_grain_weekday[grain].add(row["data_cadastro"])

            eligible_dates_by_weekday = {
                weekday: sum(
                    1
                    for offset in range((hist_to - hist_from).days + 1)
                    if (hist_from + timedelta(days=offset)).weekday() == weekday
                )
                for weekday in range(7)
            }
            history_cached = (
                history_by_grain_weekday,
                observed_dates_by_grain_weekday,
                eligible_dates_by_weekday,
            )
            if cache is not None:
                cache[history_key] = history_cached
        (
            history_by_grain_weekday,
            observed_dates_by_grain_weekday,
            eligible_dates_by_weekday,
        ) = history_cached
        history_by_workflow_weekday: dict[tuple[int, int, int], Decimal] = defaultdict(
            lambda: ZERO
        )
        history_by_workflow: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
        for (cliente_id, workflow_id, _nh_id, weekday), amount in history_by_grain_weekday.items():
            history_by_workflow_weekday[(cliente_id, workflow_id, weekday)] += amount
            history_by_workflow[(cliente_id, workflow_id)] += amount
        by_day: dict[date, dict] = {}
        for day in week_dates:
            weekday = day.weekday()
            totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
            historical_grains = 0
            workflow_fallback_grains = 0
            fallback_grains = 0
            exact_volume = ZERO
            workflow_fallback_volume = ZERO
            projection_fallback_volume = ZERO
            weighted_coverage_num = ZERO
            weighted_coverage_den = ZERO
            for grain, week_volume in weekly_projection.items():
                cliente_id, workflow_id, nh_id = grain
                historical_week_total = sum(
                    (
                        history_by_grain_weekday.get(
                            (cliente_id, workflow_id, nh_id, wd), ZERO
                        )
                        for wd in range(7)
                    ),
                    ZERO,
                )
                if historical_week_total > ZERO:
                    weekday_volume = history_by_grain_weekday.get(
                        (cliente_id, workflow_id, nh_id, weekday), ZERO
                    )
                    value = week_volume * weekday_volume / historical_week_total
                    historical_grains += 1
                    exact_volume += week_volume
                else:
                    workflow_week_total = history_by_workflow.get(
                        (cliente_id, workflow_id), ZERO
                    )
                    if workflow_week_total > ZERO:
                        workflow_weekday_volume = history_by_workflow_weekday.get(
                            (cliente_id, workflow_id, weekday), ZERO
                        )
                        value = week_volume * workflow_weekday_volume / workflow_week_total
                        workflow_fallback_grains += 1
                        workflow_fallback_volume += week_volume
                    else:
                        value = projection_by_day[day].get(grain, ZERO)
                        fallback_grains += 1
                        projection_fallback_volume += week_volume
                totals[(cliente_id, workflow_id)] += value
                observed = len(
                    observed_dates_by_grain_weekday.get(
                        (cliente_id, workflow_id, nh_id, weekday), set()
                    )
                )
                eligible = eligible_dates_by_weekday[weekday]
                coverage = Decimal(observed) / Decimal(eligible) * 100 if eligible else ZERO
                weighted_coverage_num += coverage * week_volume
                weighted_coverage_den += week_volume

            by_day[day] = {
                "available": True,
                "workflow_totals": dict(totals),
                "workflow_meta": projection_workflow_meta,
                "weekly_projection_volume": sum(weekly_projection.values(), ZERO),
                "historical_grain_count": historical_grains,
                "workflow_fallback_grain_count": workflow_fallback_grains,
                "fallback_grain_count": fallback_grains,
                "exact_nh_coverage_pct": (
                    exact_volume / weighted_coverage_den * 100
                    if weighted_coverage_den > ZERO
                    else ZERO
                ),
                "workflow_fallback_coverage_pct": (
                    workflow_fallback_volume / weighted_coverage_den * 100
                    if weighted_coverage_den > ZERO
                    else ZERO
                ),
                "projection_fallback_pct": (
                    projection_fallback_volume / weighted_coverage_den * 100
                    if weighted_coverage_den > ZERO
                    else ZERO
                ),
                "historical_coverage_pct": (
                    (exact_volume + workflow_fallback_volume)
                    / weighted_coverage_den
                    * 100
                    if weighted_coverage_den > ZERO
                    else ZERO
                ),
                "coverage_pct": (
                    weighted_coverage_num / weighted_coverage_den
                    if weighted_coverage_den > ZERO
                    else ZERO
                ),
                "eligible_dates": eligible_dates_by_weekday[weekday],
                "window_from": hist_from,
                "window_to": hist_to,
            }
        cached = by_day
        if cache is not None:
            cache[cache_key] = cached
    return cached[on_date]
