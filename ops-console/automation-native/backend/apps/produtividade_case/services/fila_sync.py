# -*- coding: utf-8 -*-
"""Persiste snapshot JSON da fila Case → CaseFilaSnapshot + CaseFilaAgg."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.produtividade_case.constants import (
    FILA_ITEMS_BULK_SIZE,
    FILA_SAMPLE_LIMIT,
    STALE_SNAPSHOT_HOURS,
)
from apps.produtividade_case.models import CaseFilaAgg, CaseFilaSampleItem, CaseFilaSnapshot


def _isoformat_local(value: datetime | None) -> str | None:
    """Serializa datetime no fuso do portal (America/Sao_Paulo)."""
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.isoformat()


def _parse_created_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw or "").strip()
        dt = parse_datetime(text) if text else None
    if dt is None:
        return None
    # Payload do bot / Mongo: instante UTC (naive = UTC).
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def _parse_captured_at(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        dt = raw
    else:
        text = str(raw or "").strip()
        dt = parse_datetime(text) if text else None
    if dt is None:
        dt = timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.utc)
    return dt


def _rows(payload: dict, *keys: str) -> list[dict]:
    for key in keys:
        val = payload.get(key)
        if isinstance(val, list):
            return val
    return []


@transaction.atomic
def sync_fila_from_json(path: Path | str) -> CaseFilaSnapshot:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo de fila Case não encontrado: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON da fila Case inválido (esperado objeto)")

    captured_at = _parse_captured_at(payload.get("captured_at"))
    total = int(payload.get("total_abertos") or 0)
    duration = payload.get("duration_seconds")
    try:
        duration_f = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_f = None

    source_file = str(path.resolve())
    # Reprocessar a mesma captura/fonte substitui o conteúdo no mesmo snapshot.
    # O lock evita duplicação em execuções concorrentes no banco transacional.
    snap = (
        CaseFilaSnapshot.objects.select_for_update()
        .filter(captured_at=captured_at, source_file=source_file)
        .order_by("id")
        .first()
    )
    if snap is None:
        snap = CaseFilaSnapshot.objects.create(
            captured_at=captured_at,
            total_abertos=total,
            success=True,
            source_file=source_file,
            duration_seconds=duration_f,
            message="Snapshot da fila Case importado.",
        )
    else:
        snap.aggs.all().delete()
        snap.sample_items.all().delete()
        snap.total_abertos = total
        snap.success = True
        snap.duration_seconds = duration_f
        snap.message = "Snapshot da fila Case reprocessado idempotentemente."
        snap.save(
            update_fields=[
                "total_abertos", "success", "duration_seconds", "message"
            ]
        )

    dim_map = (
        (CaseFilaAgg.DIM_STATUS, _rows(payload, "by_status")),
        (CaseFilaAgg.DIM_IDADE, _rows(payload, "by_idade_bucket", "by_idade")),
        (CaseFilaAgg.DIM_REQUEST_TYPE, _rows(payload, "by_request_type")),
    )
    aggs: list[CaseFilaAgg] = []
    for dimension, rows in dim_map:
        for row in rows:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or row.get("status") or row.get("bucket") or "").strip()
            if not key:
                key = "(vazio)"
            try:
                count = int(row.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            aggs.append(
                CaseFilaAgg(
                    snapshot=snap,
                    dimension=dimension,
                    key=key[:64],
                    count=max(0, count),
                )
            )
    if aggs:
        CaseFilaAgg.objects.bulk_create(aggs, batch_size=200)

    sample_raw = payload.get("sample_items") or payload.get("items")
    items: list[CaseFilaSampleItem] = []
    if isinstance(sample_raw, list) and sample_raw:
        for row in sample_raw[:FILA_SAMPLE_LIMIT]:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("protocolo_id") or "").strip()
            if not pid:
                continue
            items.append(
                CaseFilaSampleItem(
                    snapshot=snap,
                    protocolo_id=pid[:128],
                    protocolo_origem=str(row.get("protocolo_origem") or "")[:128],
                    transaction_status=str(row.get("transaction_status") or "")[:64],
                    idade_bucket=str(row.get("idade_bucket") or "")[:16],
                    created_ts=_parse_created_ts(row.get("created_ts")),
                    cadastro_origem_at=_parse_created_ts(row.get("cadastro_origem_at")),
                    workflow_origem=str(row.get("workflow_origem") or "")[:255],
                    cliente_origem=str(row.get("cliente_origem") or "")[:255],
                )
            )
        if items:
            CaseFilaSampleItem.objects.bulk_create(items, batch_size=FILA_ITEMS_BULK_SIZE)
            # Mantém itens só no snapshot atual (série horária não precisa da lista)
            CaseFilaSampleItem.objects.exclude(snapshot_id=snap.pk).delete()

    from apps.produtividade_case.services.mvp_metrics import compute_aging_stats

    aging = compute_aging_stats(
        captured_at,
        [(it.cadastro_origem_at or it.created_ts) for it in items],
    )
    CaseFilaSnapshot.objects.filter(pk=snap.pk).update(
        aging_count=aging["aging_count"],
        aging_medio_seconds=aging["aging_medio_seconds"],
        aging_mediano_seconds=aging["aging_mediano_seconds"],
        aging_p90_seconds=aging["aging_p90_seconds"],
    )
    snap.aging_count = aging["aging_count"]
    snap.aging_medio_seconds = aging["aging_medio_seconds"]
    snap.aging_mediano_seconds = aging["aging_mediano_seconds"]
    snap.aging_p90_seconds = aging["aging_p90_seconds"]
    return snap


def latest_successful_snapshot() -> CaseFilaSnapshot | None:
    return (
        CaseFilaSnapshot.objects.filter(success=True)
        .order_by("-captured_at", "-id")
        .first()
    )


def latest_snapshot_with_items() -> CaseFilaSnapshot | None:
    """Prefere o snapshot mais recente que tenha lista de protocolos."""
    from django.db.models import Count

    return (
        CaseFilaSnapshot.objects.filter(success=True)
        .annotate(_n=Count("sample_items"))
        .filter(_n__gt=0)
        .order_by("-captured_at", "-id")
        .first()
    )


def snapshot_is_stale(snap: CaseFilaSnapshot | None, *, hours: float = STALE_SNAPSHOT_HOURS) -> bool:
    if snap is None:
        return True
    cutoff = timezone.now() - timedelta(hours=hours)
    return snap.captured_at < cutoff


def serialize_status(snap: CaseFilaSnapshot | None) -> dict[str, Any]:
    if snap is None:
        return {
            "has_data": False,
            "captured_at": None,
            "total_abertos": 0,
            "stale": True,
            "source_file": "",
            "items_count": 0,
            "items_truncated": False,
            "items_limit": FILA_SAMPLE_LIMIT,
            "duration_seconds": None,
            "message": "Nenhum snapshot da fila Case disponível.",
        }
    items_count = snap.sample_items.count()
    return {
        "has_data": True,
        "captured_at": _isoformat_local(snap.captured_at),
        "total_abertos": snap.total_abertos,
        "stale": snapshot_is_stale(snap),
        "source_file": Path(snap.source_file).name if snap.source_file else "",
        "items_count": items_count,
        "items_truncated": items_count < snap.total_abertos,
        "items_limit": FILA_SAMPLE_LIMIT,
        "duration_seconds": snap.duration_seconds,
        "message": snap.message or "",
        "snapshot_id": snap.pk,
    }


def serialize_resumo(snap: CaseFilaSnapshot | None) -> dict[str, Any]:
    base = serialize_status(snap)
    if snap is None:
        base.update(
            {
                "by_status": [],
                "by_idade_bucket": [],
                "by_request_type": [],
            }
        )
        return base

    by_status: list[dict] = []
    by_idade: list[dict] = []
    by_req: list[dict] = []
    for agg in snap.aggs.all():
        item = {"key": agg.key, "count": agg.count}
        if agg.dimension == CaseFilaAgg.DIM_STATUS:
            by_status.append(item)
        elif agg.dimension == CaseFilaAgg.DIM_IDADE:
            by_idade.append(item)
        elif agg.dimension == CaseFilaAgg.DIM_REQUEST_TYPE:
            by_req.append(item)
    from apps.produtividade_case.services.mvp_metrics import serialize_fila_aging

    base.update(
        {
            "by_status": by_status,
            "by_idade_bucket": by_idade,
            "by_request_type": by_req,
            **serialize_fila_aging(snap),
        }
    )
    return base


def serialize_painel(snap: CaseFilaSnapshot | None) -> dict[str, Any]:
    """Agrupa a lista materializada da fila atual por cliente e workflow."""
    from apps.controle_sla.services.sla_eval import evaluate_sla_batch

    base = serialize_status(snap)
    if snap is None:
        base.update({
            "total_clientes": 0,
            "total_workflows": 0,
            "clientes": [],
            **{f"total_sla_{state}": 0 for state in (
                "ok", "attention", "high", "breached", "unconfigured", "unmapped", "no_date"
            )},
        })
        return base

    # Uma única leitura permite normalizar espaços/vazios antes de acumular e
    # garante que o protocolo corresponde de fato à menor data com fallback.
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in snap.sample_items.values(
        "cliente_origem", "workflow_origem", "protocolo_id",
        "protocolo_origem", "cadastro_origem_at", "created_ts",
    ).iterator(chunk_size=2000):
        cliente = " ".join((row["cliente_origem"] or "").split()) or "(sem cliente)"
        workflow = " ".join((row["workflow_origem"] or "").split()) or "(sem workflow)"
        key = (cliente, workflow)
        group = groups.setdefault(key, {
            "cliente": cliente, "workflow": workflow, "count": 0,
            "oldest_at": None, "oldest_protocol_id": "",
        })
        group["count"] += 1
        created = row["cadastro_origem_at"] or row["created_ts"]
        if created is not None and (group["oldest_at"] is None or created < group["oldest_at"]):
            group["oldest_at"] = created
            group["oldest_protocol_id"] = row["protocolo_origem"] or row["protocolo_id"]

    evaluated = evaluate_sla_batch(list(groups.values()), reference_at=snap.captured_at)
    by_client: dict[str, list[dict[str, Any]]] = {}
    status_totals = {state: 0 for state in (
        "ok", "attention", "high", "breached", "unconfigured", "unmapped", "no_date"
    )}
    for row in evaluated:
        cliente, workflow = row["cliente"], row["workflow"]
        count = int(row["count"] or 0)
        oldest = row.get("oldest_at")
        age = max(0, int((snap.captured_at - oldest).total_seconds())) if oldest else None
        state = row["sla_status"]
        status_totals[state] += 1
        by_client.setdefault(cliente, []).append({
            "key": workflow,
            "count": count,
            "oldest_protocol_id": row.get("oldest_protocol_id") or "",
            "oldest_created_at": _isoformat_local(oldest),
            "oldest_age_seconds": age,
            "sla_limit_seconds": row["sla_limit_seconds"],
            "sla_elapsed_seconds": row["sla_elapsed_seconds"],
            "sla_remaining_seconds": row["sla_remaining_seconds"],
            "sla_pct": row["sla_pct"],
            "sla_status": state,
            "sla_due_at": _isoformat_local(row.get("sla_due_at")),
        })

    clientes = []
    priority = {"breached": 0, "high": 1, "attention": 2, "unconfigured": 3,
                "unmapped": 4, "no_date": 5, "ok": 6}
    for cliente, workflows in by_client.items():
        total = sum(item["count"] for item in workflows)
        for item in workflows:
            item["pct"] = round((item["count"] / total) * 100, 1) if total else 0
        workflows.sort(key=lambda item: (
            priority.get(item["sla_status"], 99),
            -(item["sla_pct"] if item["sla_pct"] is not None else -1),
            -(item["oldest_age_seconds"] or 0), item["key"].casefold(),
        ))
        clientes.append(
            {"cliente": cliente, "total_abertos": total, "workflows": workflows}
        )
    clientes.sort(key=lambda item: (-item["total_abertos"], item["cliente"].casefold()))
    base.update(
        {
            "total_clientes": len(clientes),
            "total_workflows": len(groups),
            "clientes": clientes,
            **{f"total_sla_{state}": count for state, count in status_totals.items()},
        }
    )
    return base


def serialize_serie(*, hours: int = 72) -> dict[str, Any]:
    hours = max(1, min(int(hours or 72), 168))
    cutoff = timezone.now() - timedelta(hours=hours)
    qs = (
        CaseFilaSnapshot.objects.filter(success=True, captured_at__gte=cutoff)
        .order_by("captured_at")
        .values("captured_at", "total_abertos", "id")
    )
    points = [
        {
            "captured_at": _isoformat_local(row["captured_at"]),
            "total_abertos": row["total_abertos"],
            "snapshot_id": row["id"],
        }
        for row in qs
    ]
    return {"hours": hours, "points": points}
