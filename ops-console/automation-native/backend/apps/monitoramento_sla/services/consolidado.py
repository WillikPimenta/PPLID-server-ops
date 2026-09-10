# -*- coding: utf-8 -*-
"""Agregação da fato consolidada a partir do detalhe (§17)."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from django.db import transaction
from django.db.models import Count

from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilDetalhe, SlaUtilSyncRun


GROUP_FIELDS = (
    "data_cadastro",
    "hora_cadastro",
    "hora_cadastro_fonte",
    "data_conclusao",
    "hora_conclusao",
    "id_cliente",
    "id_workflow",
    "id_nh",
    "cliente_nome",
    "workflow_nome",
    "nh_nome",
    "tipo_conclusao",
    "resultado",
    "sla_descricao_natural",
    "sla_descricao_ajustado",
    "faixa",
    "avaliacao",
    "date_key_cadastro",
)


@transaction.atomic
def rebuild_consolidado(
    *,
    sync_run: SlaUtilSyncRun | None = None,
    affected_dates: Iterable[date] | None = None,
) -> int:
    """Reagrega somente as datas alteradas, usando GROUP BY no PostgreSQL."""
    if affected_dates is None:
        dates = list(
            SlaUtilDetalhe.objects.order_by()
            .values_list("data_cadastro", flat=True)
            .distinct()
        )
    else:
        dates = sorted(set(affected_dates))
    if not dates:
        return 0

    SlaUtilConsolidado.objects.filter(data_cadastro__in=dates).delete()
    grouped = (
        SlaUtilDetalhe.objects.filter(data_cadastro__in=dates)
        .order_by()
        .values(*GROUP_FIELDS)
        .annotate(quantidade=Count("id"))
    )
    batch: list[SlaUtilConsolidado] = []
    total = 0
    for payload in grouped.iterator(chunk_size=2000):
        batch.append(SlaUtilConsolidado(sync_run=sync_run, **payload))
        if len(batch) >= 1000:
            SlaUtilConsolidado.objects.bulk_create(batch, batch_size=1000)
            total += len(batch)
            batch = []
    if batch:
        SlaUtilConsolidado.objects.bulk_create(batch, batch_size=1000)
        total += len(batch)
    return total
