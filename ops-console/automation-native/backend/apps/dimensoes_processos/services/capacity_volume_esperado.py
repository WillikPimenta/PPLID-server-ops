"""
Volume esperado por hora — equivalente a ``tabela_volume_esperado`` do DAX.

Constrói ``Volume Hora Contrato`` = ``VolumeDia`` × ``CurvaIntraDay_Pct``, onde a curva
intraday vem do histórico de **3 meses** do monitoramento SLA consolidado
(``SlaUtilConsolidado``), no mesmo dia da semana.

Referência DAX: projeção diária + curva semanal (ajuste dias úteis) + curva intraday.
Para Capacity usa-se apenas **Volume Hora Contrato** (contrato original × curva horária),
não o volume semanalmente ajustado.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import ExtractHour

from apps.dimensoes_processos.models import ProjecaoSla
from apps.monitoramento_sla.models import SlaUtilConsolidado
from apps.monitoramento_sla.services.projecao_lookup import _covers, load_projecao_rows_for_range

ZERO = Decimal("0")
HOURS = range(24)
HISTORICAL_MONTHS = 3
UNIFORM_HOURLY = Decimal("1") / Decimal("24")

WorkflowKey = tuple[int, int]
NhKey = tuple[int, int, int]  # cliente, workflow, nh
HourlyVolumeKey = tuple[int, int, int]  # cliente, workflow, hour
HourlyNhVolumeKey = tuple[int, int, int, int]  # cliente, workflow, nh, hour


def mes_index(value: date) -> int:
    return value.year * 12 + value.month


def _mes_index_to_year_month(value: int) -> tuple[int, int]:
    year = (value - 1) // 12
    month = (value - 1) % 12 + 1
    return year, month


def first_day_of_mes_index(value: int) -> date:
    year, month = _mes_index_to_year_month(value)
    return date(year, month, 1)


def last_day_of_mes_index(value: int) -> date:
    year, month = _mes_index_to_year_month(value)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, last_day)


def weekday_num(value: date) -> int:
    """Segunda=0 … Domingo=6 (igual DAX WEEKDAY(..., 2) - 1)."""
    return value.weekday()


def week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def curva_semanal_pct(numerador: Decimal, denominador: Decimal) -> Decimal | None:
    if denominador <= ZERO:
        return None
    return numerador / denominador


def curva_intraday_pct(recebidos_hora: Decimal, recebidos_dia: Decimal) -> Decimal:
    """
    Regra DAX (CurvaIntradayMensal):
    - sem histórico diário (den=0) → uniforme 1/24
    - com histórico diário → num/den (0 se hora sem volume)
    """
    if recebidos_dia <= ZERO:
        return UNIFORM_HOURLY
    if recebidos_hora <= ZERO:
        return ZERO
    return recebidos_hora / recebidos_dia


def _history_bounds(mes_ref: int) -> tuple[date, date]:
    start = first_day_of_mes_index(mes_ref - HISTORICAL_MONTHS)
    end = last_day_of_mes_index(mes_ref - 1)
    return start, end


def _load_historico_diario(
    hist_start: date,
    hist_end: date,
) -> dict[tuple[int, int, int | None, int, int], Decimal]:
    """
    Agrega recebidos diários por C+WF+NH+MesIndex+Weekday.

    Chave: (cliente, workflow, nh, mes_index_hist, weekday_num)
    """
    totals: dict[tuple[int, int, int | None, int, int], Decimal] = defaultdict(lambda: ZERO)
    rows = (
        SlaUtilConsolidado.objects.filter(
            data_cadastro__gte=hist_start,
            data_cadastro__lte=hist_end,
        )
        .values("id_cliente", "id_workflow", "id_nh", "data_cadastro")
        .annotate(recebidos=Sum("quantidade"))
    )
    for row in rows:
        if row["id_cliente"] is None or row["id_workflow"] is None:
            continue
        data_hist = row["data_cadastro"]
        key = (
            int(row["id_cliente"]),
            int(row["id_workflow"]),
            int(row["id_nh"]) if row["id_nh"] is not None else None,
            mes_index(data_hist),
            weekday_num(data_hist),
        )
        totals[key] += Decimal(str(row["recebidos"] or 0))
    return dict(totals)


def _load_historico_horario(
    hist_start: date,
    hist_end: date,
) -> dict[tuple[int, int, int | None, int, int, int], Decimal]:
    """Agrega recebidos horários por C+WF+NH+MesIndex+Weekday+Hora."""
    totals: dict[tuple[int, int, int | None, int, int, int], Decimal] = defaultdict(lambda: ZERO)
    rows = (
        SlaUtilConsolidado.objects.filter(
            data_cadastro__gte=hist_start,
            data_cadastro__lte=hist_end,
        )
        .exclude(hora_cadastro__isnull=True)
        .annotate(hour=ExtractHour("hora_cadastro"))
        .values("id_cliente", "id_workflow", "id_nh", "data_cadastro", "hour")
        .annotate(recebidos=Sum("quantidade"))
    )
    for row in rows:
        if row["id_cliente"] is None or row["id_workflow"] is None or row["hour"] is None:
            continue
        data_hist = row["data_cadastro"]
        key = (
            int(row["id_cliente"]),
            int(row["id_workflow"]),
            int(row["id_nh"]) if row["id_nh"] is not None else None,
            mes_index(data_hist),
            weekday_num(data_hist),
            int(row["hour"]),
        )
        totals[key] += Decimal(str(row["recebidos"] or 0))
    return dict(totals)


def _sum_historico_diario(
    historico: dict[tuple[int, int, int | None, int, int], Decimal],
    *,
    cliente_id: int,
    workflow_id: int,
    nh_id: int | None,
    mes_ref: int,
    weekday: int | None = None,
    weekdays: range | None = None,
) -> Decimal:
    total = ZERO
    mes_start = mes_ref - HISTORICAL_MONTHS
    mes_end = mes_ref - 1
    for (cliente, workflow, nh, mes_hist, wd), amount in historico.items():
        if cliente != cliente_id or workflow != workflow_id:
            continue
        if nh_id is not None and nh != nh_id:
            continue
        if mes_hist < mes_start or mes_hist > mes_end:
            continue
        if weekday is not None and wd != weekday:
            continue
        if weekdays is not None and wd not in weekdays:
            continue
        total += amount
    return total


def _sum_historico_horario(
    historico: dict[tuple[int, int, int | None, int, int, int], Decimal],
    *,
    cliente_id: int,
    workflow_id: int,
    nh_id: int | None,
    mes_ref: int,
    weekday: int,
    hour: int,
) -> Decimal:
    total = ZERO
    mes_start = mes_ref - HISTORICAL_MONTHS
    mes_end = mes_ref - 1
    for (cliente, workflow, nh, mes_hist, wd, h), amount in historico.items():
        if cliente != cliente_id or workflow != workflow_id:
            continue
        if nh_id is not None and nh != nh_id:
            continue
        if mes_hist < mes_start or mes_hist > mes_end:
            continue
        if wd != weekday or h != hour:
            continue
        total += amount
    return total


def _projection_volume_for_date(
    on_date: date,
    *,
    projection_rows: list[ProjecaoSla] | None = None,
) -> dict[NhKey, Decimal]:
    """VolumeDia por C+WF+NH na data (dimProjecao)."""
    if projection_rows is None:
        projection_rows = load_projecao_rows_for_range(date_min=on_date, date_max=on_date)

    volumes: dict[NhKey, Decimal] = defaultdict(lambda: ZERO)
    for row in projection_rows:
        if not row.workflow.ind_considerar or not row.cliente.operations:
            continue
        if not _covers(row, on_date):
            continue
        if row.volume is None or row.volume <= 0:
            continue
        key = (row.cliente_id, row.workflow_id, row.nivel_hierarquico_id)
        volumes[key] += Decimal(str(row.volume))
    return dict(volumes)


def _weekday_projection_totals(
    week_dates: list[date],
    *,
    projection_rows: list[ProjecaoSla],
) -> dict[tuple[int, int], Decimal]:
    """VolumeSemanaUtil_Proj: soma seg–sex por Cliente+Workflow (todos NHs)."""
    totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    for day in week_dates:
        if weekday_num(day) > 4:
            continue
        daily = _projection_volume_for_date(day, projection_rows=projection_rows)
        for (cliente_id, workflow_id, _nh_id), amount in daily.items():
            totals[(cliente_id, workflow_id)] += amount
    return dict(totals)


def build_volume_hora_contrato(
    on_date: date,
    *,
    workflow_keys: set[WorkflowKey] | None = None,
) -> dict[HourlyVolumeKey, Decimal]:
    """
    Retorna ``Volume Hora Contrato`` agregado por (cliente, workflow, hora).

    Soma NHs — Capacity consulta C+WF+Hora (equivalente operacional ao SUM).
    """
    detailed = build_volume_hora_contrato_detailed(on_date, workflow_keys=workflow_keys)
    totals: dict[HourlyVolumeKey, Decimal] = defaultdict(lambda: ZERO)
    for (cliente_id, workflow_id, _nh_id, hour), amount in detailed.items():
        totals[(cliente_id, workflow_id, hour)] += amount
    return dict(totals)


def build_volume_hora_contrato_detailed(
    on_date: date,
    *,
    workflow_keys: set[WorkflowKey] | None = None,
) -> dict[HourlyNhVolumeKey, Decimal]:
    """Volume Hora Contrato no grão C+WF+NH+Hora."""
    mes_ref = mes_index(on_date)
    weekday = weekday_num(on_date)
    hist_start, hist_end = _history_bounds(mes_ref)

    hist_diario = _load_historico_diario(hist_start, hist_end)
    hist_horario = _load_historico_horario(hist_start, hist_end)

    week_dates = [week_start(on_date) + timedelta(days=offset) for offset in range(5)]
    projection_rows = load_projecao_rows_for_range(
        date_min=min(week_dates[0], on_date),
        date_max=max(week_dates[-1], on_date),
    )
    volume_dia_map = _projection_volume_for_date(on_date, projection_rows=projection_rows)

    out: dict[HourlyNhVolumeKey, Decimal] = defaultdict(lambda: ZERO)

    for (cliente_id, workflow_id, nh_id), volume_dia in volume_dia_map.items():
        wf_key = (cliente_id, workflow_id)
        if workflow_keys is not None and wf_key not in workflow_keys:
            continue

        recebidos_mesmo_dia = _sum_historico_diario(
            hist_diario,
            cliente_id=cliente_id,
            workflow_id=workflow_id,
            nh_id=nh_id,
            mes_ref=mes_ref,
            weekday=weekday,
        )

        for hour in HOURS:
            recebidos_hora = _sum_historico_horario(
                hist_horario,
                cliente_id=cliente_id,
                workflow_id=workflow_id,
                nh_id=nh_id,
                mes_ref=mes_ref,
                weekday=weekday,
                hour=hour,
            )
            pct = curva_intraday_pct(recebidos_hora, recebidos_mesmo_dia)
            amount = volume_dia * pct
            if amount > ZERO:
                out[(cliente_id, workflow_id, nh_id, hour)] += amount

    return dict(out)


def build_volume_hora_ajustado(
    on_date: date,
    *,
    workflow_keys: set[WorkflowKey] | None = None,
) -> dict[HourlyVolumeKey, Decimal]:
    """
    ``Volume Hora`` do DAX = VolumeDiaAjustado × CurvaIntraDay_Pct.

    Útil para auditoria; Capacity oficial usa ``build_volume_hora_contrato``.
    """
    mes_ref = mes_index(on_date)
    weekday = weekday_num(on_date)
    hist_start, hist_end = _history_bounds(mes_ref)
    hist_diario = _load_historico_diario(hist_start, hist_end)
    hist_horario = _load_historico_horario(hist_start, hist_end)

    week_dates = [week_start(on_date) + timedelta(days=offset) for offset in range(5)]
    projection_rows = load_projecao_rows_for_range(
        date_min=min(week_dates[0], on_date),
        date_max=max(week_dates[-1], on_date),
    )
    volume_dia_map = _projection_volume_for_date(on_date, projection_rows=projection_rows)
    week_totals = _weekday_projection_totals(week_dates, projection_rows=projection_rows)

    totals: dict[HourlyVolumeKey, Decimal] = defaultdict(lambda: ZERO)

    for (cliente_id, workflow_id, nh_id), volume_dia in volume_dia_map.items():
        wf_key = (cliente_id, workflow_id)
        if workflow_keys is not None and wf_key not in workflow_keys:
            continue

        if 0 <= weekday <= 4:
            num = _sum_historico_diario(
                hist_diario,
                cliente_id=cliente_id,
                workflow_id=workflow_id,
                nh_id=nh_id,
                mes_ref=mes_ref,
                weekday=weekday,
            )
            den = _sum_historico_diario(
                hist_diario,
                cliente_id=cliente_id,
                workflow_id=workflow_id,
                nh_id=nh_id,
                mes_ref=mes_ref,
                weekdays=range(0, 5),
            )
            pct_semana = curva_semanal_pct(num, den)
            volume_dia_ajustado = (
                week_totals.get(wf_key, volume_dia) * pct_semana
                if pct_semana is not None
                else volume_dia
            )
        else:
            volume_dia_ajustado = volume_dia

        recebidos_mesmo_dia = _sum_historico_diario(
            hist_diario,
            cliente_id=cliente_id,
            workflow_id=workflow_id,
            nh_id=nh_id,
            mes_ref=mes_ref,
            weekday=weekday,
        )

        for hour in HOURS:
            recebidos_hora = _sum_historico_horario(
                hist_horario,
                cliente_id=cliente_id,
                workflow_id=workflow_id,
                nh_id=nh_id,
                mes_ref=mes_ref,
                weekday=weekday,
                hour=hour,
            )
            pct_hora = curva_intraday_pct(recebidos_hora, recebidos_mesmo_dia)
            amount = volume_dia_ajustado * pct_hora
            if amount > ZERO:
                totals[(cliente_id, workflow_id, hour)] += amount

    return dict(totals)
