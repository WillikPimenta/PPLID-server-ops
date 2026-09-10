"""Ajustes de volume no cenário Planejamento (Projeção SLA vs recebido histórico)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from django.db.models import Sum

from apps.dimensoes_processos.services.capacity_scenarios import DERIVATION_MEDIAN_LOOKBACK_DAYS
from apps.monitoramento_sla.models import SlaUtilConsolidado

ZERO = Decimal("0")
VOLUME_PROJECTION_MAX_RATIO = Decimal("10")
VOLUME_MEDIAN_MIN_SAMPLE_DAYS = 3

WorkflowKey = tuple[int, int]


def _load_daily_received(
    on_date: date,
    *,
    lookback_days: int = DERIVATION_MEDIAN_LOOKBACK_DAYS,
) -> dict[WorkflowKey, dict[date, Decimal]]:
    window_from = on_date - timedelta(days=lookback_days)
    totals: dict[WorkflowKey, dict[date, Decimal]] = defaultdict(dict)
    rows = (
        SlaUtilConsolidado.objects.filter(
            data_cadastro__gte=window_from,
            data_cadastro__lt=on_date,
        )
        .values("id_cliente", "id_workflow", "data_cadastro")
        .annotate(total=Sum("quantidade"))
    )
    for row in rows:
        if row["id_cliente"] is None or row["id_workflow"] is None:
            continue
        key = (int(row["id_cliente"]), int(row["id_workflow"]))
        day = row["data_cadastro"]
        amount = Decimal(str(row["total"] or 0))
        if amount <= ZERO:
            continue
        totals[key][day] = totals[key].get(day, ZERO) + amount
    return dict(totals)


def _median_daily_received(daily: dict[date, Decimal]) -> tuple[Decimal | None, int]:
    values = [amount for amount in daily.values() if amount > ZERO]
    if len(values) < VOLUME_MEDIAN_MIN_SAMPLE_DAYS:
        return None, len(values)
    return Decimal(str(median([float(value) for value in values]))), len(values)


def apply_planejamento_volume_guard(
    workflow_totals: dict[WorkflowKey, Decimal],
    workflow_meta: dict[WorkflowKey, dict],
    on_date: date,
    *,
    max_ratio: Decimal = VOLUME_PROJECTION_MAX_RATIO,
) -> tuple[dict[WorkflowKey, Decimal], list[dict]]:
    """
    Quando a Projeção SLA diverge muito do recebido histórico, usa a mediana diária
    do consolidado (60d, exclui o dia analisado) para evitar volumes contratuais
    obsoletos — ex.: Agibank com dezenas de milhares projetados vs ~2 recebidos/dia.
    """
    if not workflow_totals:
        return workflow_totals, []

    daily_received = _load_daily_received(on_date)
    adjusted_totals = dict(workflow_totals)
    adjustments: list[dict] = []

    for key, projected in workflow_totals.items():
        if projected <= ZERO:
            continue
        median_volume, sample_days = _median_daily_received(daily_received.get(key, {}))
        if median_volume is None:
            continue
        threshold = median_volume * max_ratio
        if projected <= threshold:
            continue

        info = workflow_meta.get(key, {})
        adjusted_totals[key] = median_volume
        adjustments.append(
            {
                "id_cliente": key[0],
                "id_workflow": key[1],
                "cliente_nome": info.get("cliente_nome", ""),
                "workflow_nome": info.get("workflow_nome", ""),
                "projected_volume": str(projected.quantize(Decimal("0.01"))),
                "volume_median_60d": str(median_volume.quantize(Decimal("0.01"))),
                "received_sample_days": sample_days,
                "reason": "projecao_sla_acima_mediana_recebido",
            }
        )

    return adjusted_totals, adjustments
