"""Ranking da fila online com base na Projeção SLA (Megazord)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from apps.controle_sla.services.sla_eval import (
    _shortest_sla_limit,
    _vigente_sla_by_workflow,
    business_elapsed_seconds,
    compute_breach_moment,
    find_active_sla,
    projecao_windows,
)

from ..models import OperationalSupportRequest

WAIT_PRIORITY_POINTS_PER_MINUTE = 5.0


def load_support_sla_map() -> dict:
    """Carrega uma vez o mapa vigente para cálculos em lote."""
    return _vigente_sla_by_workflow()


@dataclass(frozen=True)
class SupportSlaMetrics:
    sla_limit_seconds: int | None
    deadline_at: datetime | None
    pct_sla: float
    queue_entered_at: datetime


def _queue_entered_at(req: OperationalSupportRequest) -> datetime:
    return req.leader_decided_at or req.created_at


def compute_support_sla_metrics(
    req: OperationalSupportRequest,
    *,
    sla_map: dict | None = None,
    now=None,
) -> SupportSlaMetrics:
    now = now or timezone.now()
    entered_at = _queue_entered_at(req)
    workflow_id = req.workflow_id
    cliente_id = req.cliente_id
    if not workflow_id or not cliente_id:
        return SupportSlaMetrics(
            sla_limit_seconds=None,
            deadline_at=None,
            pct_sla=0.0,
            queue_entered_at=entered_at,
        )

    sla_map = sla_map if sla_map is not None else _vigente_sla_by_workflow()
    rules = sla_map.get((cliente_id, workflow_id), [])
    if not rules:
        return SupportSlaMetrics(
            sla_limit_seconds=None,
            deadline_at=None,
            pct_sla=0.0,
            queue_entered_at=entered_at,
        )

    windows = projecao_windows(rules)
    active = find_active_sla(
        cliente_id=cliente_id,
        workflow_id=workflow_id,
        now=now,
        sla_map_cw=sla_map,
    )
    limit = None
    if active:
        limit = active.sla_segundos
    if not limit:
        limit = _shortest_sla_limit(rules)
    if not limit:
        return SupportSlaMetrics(
            sla_limit_seconds=None,
            deadline_at=None,
            pct_sla=0.0,
            queue_entered_at=entered_at,
        )

    deadline = compute_breach_moment(entered_at, int(limit), windows)
    elapsed = business_elapsed_seconds(entered_at, now, windows)
    pct = min(999.0, (elapsed / int(limit)) * 100.0 if limit else 0.0)
    return SupportSlaMetrics(
        sla_limit_seconds=int(limit),
        deadline_at=deadline,
        pct_sla=pct,
        queue_entered_at=entered_at,
    )


def rank_online_queue_requests(
    requests: list[OperationalSupportRequest],
    *,
    sla_map: dict | None = None,
    now=None,
) -> list[OperationalSupportRequest]:
    """Prioriza marcações manuais e ordena os demais por urgência de SLA e FIFO."""
    now = now or timezone.now()
    sla_map = sla_map if sla_map is not None else _vigente_sla_by_workflow()

    scored: list[tuple[tuple[float, float, float, float], OperationalSupportRequest]] = []
    for req in requests:
        metrics = compute_support_sla_metrics(req, sla_map=sla_map, now=now)
        wait_seconds = max(0.0, (now - metrics.queue_entered_at).total_seconds())
        # Atendimento rápido: cada minuto aguardado adiciona 5 pontos de prioridade.
        wait_minutes = wait_seconds / 60.0
        urgency = metrics.pct_sla + wait_minutes * WAIT_PRIORITY_POINTS_PER_MINUTE
        priority_at = getattr(req, "queue_priority_at", None)
        scored.append(
            (
                (
                    0.0 if priority_at else 1.0,
                    -priority_at.timestamp() if priority_at else 0.0,
                    -urgency,
                    metrics.queue_entered_at.timestamp(),
                ),
                req,
            )
        )

    scored.sort(key=lambda item: item[0])
    return [req for _, req in scored]
