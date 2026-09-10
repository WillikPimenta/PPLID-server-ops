from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.db import models, transaction
from django.db.models import Sum
from django.db.models.functions import ExtractHour, ExtractWeekDay
from django.utils import timezone

from apps.dimensoes_processos.models import CapacityHourlyProfileSnapshot
from apps.dimensoes_processos.services.capacity_quarterly import _select_complete_quarter
from apps.monitoramento_sla.models import SlaUtilConsolidado


def materialize_capacity_hourly_profiles(reference_date: date) -> dict:
    quarter = _select_complete_quarter(reference_date, cache={})
    if not quarter:
        return {"available": False, "created": 0, "quarter_from": None, "quarter_to": None}
    quarter_from, quarter_to = quarter
    rows = (
        SlaUtilConsolidado.objects.filter(
            data_cadastro__range=quarter,
            hora_cadastro_fonte=SlaUtilConsolidado.HORA_FONTE_REAL,
        )
        .exclude(hora_cadastro__isnull=True)
        .annotate(
            weekday_db=ExtractWeekDay("data_cadastro"),
            hour=ExtractHour("hora_cadastro"),
        )
        .values("id_cliente", "id_workflow", "weekday_db", "hour")
        .annotate(volume=Sum("quantidade"))
    )
    workflow_hours: dict[tuple[int, int, int], list[int]] = defaultdict(lambda: [0] * 24)
    client_hours: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0] * 24)
    for row in rows:
        if None in (row["id_cliente"], row["id_workflow"], row["weekday_db"], row["hour"]):
            continue
        # PostgreSQL ExtractWeekDay: domingo=1; API: segunda=0.
        weekday = (int(row["weekday_db"]) + 5) % 7
        hour = int(row["hour"])
        volume = int(row["volume"] or 0)
        workflow_hours[(int(row["id_cliente"]), int(row["id_workflow"]), weekday)][hour] += volume
        client_hours[(int(row["id_cliente"]), weekday)][hour] += volume

    generated_at = timezone.now()
    snapshots = []
    for (cliente_id, workflow_id, weekday), hours in workflow_hours.items():
        snapshots.append(
            CapacityHourlyProfileSnapshot(
                quarter_from=quarter_from,
                quarter_to=quarter_to,
                weekday=weekday,
                scope=CapacityHourlyProfileSnapshot.SCOPE_WORKFLOW,
                id_cliente=cliente_id,
                id_workflow=workflow_id,
                hourly_volumes=hours,
                trusted_volume=sum(hours),
                generated_at=generated_at,
            )
        )
    for (cliente_id, weekday), hours in client_hours.items():
        snapshots.append(
            CapacityHourlyProfileSnapshot(
                quarter_from=quarter_from,
                quarter_to=quarter_to,
                weekday=weekday,
                scope=CapacityHourlyProfileSnapshot.SCOPE_CLIENT,
                id_cliente=cliente_id,
                id_workflow=0,
                hourly_volumes=hours,
                trusted_volume=sum(hours),
                generated_at=generated_at,
            )
        )
    with transaction.atomic():
        CapacityHourlyProfileSnapshot.objects.filter(
            quarter_from=quarter_from,
            quarter_to=quarter_to,
        ).delete()
        CapacityHourlyProfileSnapshot.objects.bulk_create(snapshots, batch_size=1000)
    return {
        "available": True,
        "created": len(snapshots),
        "quarter_from": quarter_from,
        "quarter_to": quarter_to,
    }


def load_capacity_hourly_profile_snapshots(
    workflow_keys: set[tuple[int, int]],
    *,
    on_date: date,
) -> tuple[dict[tuple[int, int], dict], tuple[date, date] | None]:
    if not workflow_keys:
        return {}, None
    weekday = on_date.weekday()
    latest = (
        CapacityHourlyProfileSnapshot.objects.filter(
            weekday=weekday,
            quarter_to__lt=on_date,
        )
        .order_by("-quarter_to")
        .values_list("quarter_from", "quarter_to")
        .first()
    )
    if not latest:
        return {}, None
    quarter_from, quarter_to = latest
    client_ids = {key[0] for key in workflow_keys}
    workflow_ids = {key[1] for key in workflow_keys}
    rows = CapacityHourlyProfileSnapshot.objects.filter(
        quarter_from=quarter_from,
        quarter_to=quarter_to,
        weekday=weekday,
        id_cliente__in=client_ids,
    ).filter(
        models.Q(scope=CapacityHourlyProfileSnapshot.SCOPE_CLIENT)
        | models.Q(
            scope=CapacityHourlyProfileSnapshot.SCOPE_WORKFLOW,
            id_workflow__in=workflow_ids,
        )
    )
    exact = {}
    clients = {}
    for row in rows:
        payload = {
            "hours": [int(value or 0) for value in row.hourly_volumes],
            "trusted_volume": int(row.trusted_volume),
        }
        if row.scope == CapacityHourlyProfileSnapshot.SCOPE_WORKFLOW:
            exact[(row.id_cliente, row.id_workflow)] = payload
        else:
            clients[row.id_cliente] = payload
    result = {}
    for key in workflow_keys:
        if key in exact:
            result[key] = {**exact[key], "source": "quarterly_weekday_workflow"}
        elif key[0] in clients:
            result[key] = {**clients[key[0]], "source": "quarterly_weekday_client"}
    return result, (quarter_from, quarter_to)
