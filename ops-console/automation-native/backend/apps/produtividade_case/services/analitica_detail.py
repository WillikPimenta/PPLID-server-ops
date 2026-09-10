# -*- coding: utf-8 -*-
"""Queries de detalhe Case Manager: protocolos, agente, ranking, amostra fila."""

from __future__ import annotations

from datetime import date
from typing import Any

from django.db.models import Count, Q, Sum

from apps.produtividade_case.constants import (
    DIM_MATRICULA_DESTINO,
    RANKING_DIMENSIONS,
)
from apps.produtividade_case.models import CaseConsolidadoFact, CaseFilaSampleItem
from apps.produtividade_case.services.consolidado_agg import (
    _agg_qs,
    _enrich_agentes,
    _rollup_dimension,
    latest_consolidado_snapshot,
    selected_consolidado_snapshots,
    serialize_analitica_status,
)
from apps.produtividade_case.services.fila_sync import (
    _isoformat_local,
    latest_snapshot_with_items,
    latest_successful_snapshot,
    serialize_status,
)

# Listas Case Manager: FE carrega o conjunto e pagina após filtro/ordenação no cliente.
LIST_FETCH_PAGE_SIZE_MAX = 100
PROTOCOL_ORDERING = frozenset(
    {
        "conclusao_destino_at",
        "-conclusao_destino_at",
        "tempo_analise_segundos",
        "-tempo_analise_segundos",
        "protocolo_destino",
        "-protocolo_destino",
    }
)
FILA_LIST_ORDERING = frozenset(
    {
        "protocolo_id",
        "-protocolo_id",
        "protocolo_origem",
        "-protocolo_origem",
        "transaction_status",
        "-transaction_status",
        "idade_bucket",
        "-idade_bucket",
        "created_ts",
        "-created_ts",
        "cadastro_origem_at",
        "-cadastro_origem_at",
        "workflow_origem",
        "-workflow_origem",
        "cliente_origem",
        "-cliente_origem",
    }
)


def serialize_ranking(
    *,
    dimension: str,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    top: int = 50,
) -> dict[str, Any]:
    dim = (dimension or "").strip()
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    if dim not in RANKING_DIMENSIONS:
        base.update(
            {
                "dimension": dim,
                "items": [],
                "total": 0,
                "error": f"Dimensão inválida. Use: {', '.join(sorted(RANKING_DIMENSIONS))}",
            }
        )
        return base

    snap, qs = _agg_qs(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
        dimension=dim,
    )
    rows = _rollup_dimension(qs) if snap else []
    top_n = max(1, min(int(top or 50), 200))
    items = rows[:top_n]
    total = sum(r["count"] for r in rows) or 0
    for r in items:
        r["pct"] = round(100.0 * r["count"] / total, 2) if total else 0.0
    base.update({"dimension": dim, "items": items, "total": total})
    return base


def _facts_qs(
    *,
    periodo_mes: str | None,
    workflow_origem: str | None = None,
    matricula_destino: str | None = None,
    resultado_destino: str | None = None,
    resultado_origem: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
):
    snaps = selected_consolidado_snapshots(
        periodo_mes=periodo_mes, date_from=date_from, date_to=date_to
    )
    if not snaps:
        return None, CaseConsolidadoFact.objects.none()

    snap = snaps[-1]
    qs = CaseConsolidadoFact.objects.filter(snapshot__in=snaps)
    if workflow_origem:
        qs = qs.filter(workflow_origem=workflow_origem.strip())
    if matricula_destino:
        qs = qs.filter(matricula_destino=matricula_destino.strip().lower())
    if resultado_destino:
        qs = qs.filter(resultado_destino=resultado_destino.strip())
    if resultado_origem:
        qs = qs.filter(resultado_origem=resultado_origem.strip())
    if q:
        term = q.strip()
        qs = qs.filter(
            Q(protocolo_destino__icontains=term)
            | Q(protocolo_origem__icontains=term)
            | Q(matricula_destino__icontains=term)
            | Q(matricula_origem__icontains=term)
            | Q(workflow_origem__icontains=term)
            | Q(resultado_origem__icontains=term)
            | Q(resultado_destino__icontains=term)
            | Q(cliente_origem__icontains=term)
            | Q(nh_origem__icontains=term)
            | Q(status_destino__icontains=term)
            | Q(tipo_conclusao_origem__icontains=term)
        )
    if date_from:
        qs = qs.filter(conclusao_destino_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(conclusao_destino_at__date__lte=date_to)
    return snap, qs


def serialize_protocolos(
    *,
    periodo_mes: str | None = None,
    workflow_origem: str | None = None,
    matricula_destino: str | None = None,
    resultado_destino: str | None = None,
    resultado_origem: str | None = None,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    ordering: str = "-conclusao_destino_at",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    snap, qs = _facts_qs(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        workflow_origem=workflow_origem,
        matricula_destino=matricula_destino,
        resultado_destino=resultado_destino,
        resultado_origem=resultado_origem,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    order = ordering if ordering in PROTOCOL_ORDERING else "-conclusao_destino_at"
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 25), LIST_FETCH_PAGE_SIZE_MAX))

    if snap is None:
        base.update(
            {
                "count": 0,
                "page": page,
                "page_size": page_size,
                "num_pages": 0,
                "total_workflows": 0,
                "total_clientes_origem": 0,
                "total_resultados_destino": 0,
                "count_com_tempo": 0,
                "cobertura_tempo_pct": 0.0,
                "tma_seconds": None,
                "results": [],
            }
        )
        return base

    total = qs.count()
    summary = qs.aggregate(
        total_workflows=Count(
            "workflow_origem", distinct=True, filter=~Q(workflow_origem="")
        ),
        total_clientes_origem=Count(
            "cliente_origem", distinct=True, filter=~Q(cliente_origem="")
        ),
        total_resultados_destino=Count(
            "resultado_destino", distinct=True, filter=~Q(resultado_destino="")
        ),
        count_com_tempo=Count("tempo_analise_segundos"),
        analysis_seconds_sum=Sum("tempo_analise_segundos"),
    )
    timed_count = int(summary["count_com_tempo"] or 0)
    seconds_sum = int(summary["analysis_seconds_sum"] or 0)
    num_pages = (total + page_size - 1) // page_size if total else 0
    offset = (page - 1) * page_size
    rows = list(qs.order_by(order)[offset : offset + page_size])
    results = [
        {
            "protocolo_destino": f.protocolo_destino,
            "protocolo_origem": f.protocolo_origem,
            "workflow_origem": f.workflow_origem,
            "nh_origem": f.nh_origem,
            "cliente_origem": f.cliente_origem,
            "matricula_origem": f.matricula_origem,
            "matricula_destino": f.matricula_destino,
            "resultado_destino": f.resultado_destino,
            "resultado_origem": f.resultado_origem,
            "status_destino": f.status_destino,
            "tipo_conclusao_origem": f.tipo_conclusao_origem,
            "alertas_destino": f.alertas_destino,
            "conclusao_destino_at": _isoformat_local(f.conclusao_destino_at),
            "tempo_analise_segundos": f.tempo_analise_segundos,
        }
        for f in rows
    ]
    base.update(
        {
            "periodo_mes": snap.periodo_mes,
            "count": total,
            "page": page,
            "page_size": page_size,
            "num_pages": num_pages,
            "ordering": order,
            "total_workflows": int(summary["total_workflows"] or 0),
            "total_clientes_origem": int(summary["total_clientes_origem"] or 0),
            "total_resultados_destino": int(
                summary["total_resultados_destino"] or 0
            ),
            "count_com_tempo": timed_count,
            "cobertura_tempo_pct": (
                round(100.0 * timed_count / total, 2) if total else 0.0
            ),
            "tma_seconds": (
                round(seconds_sum / timed_count, 1) if timed_count else None
            ),
            "results": results,
        }
    )
    return base


def serialize_agente_detalhe(
    matricula: str,
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    mat = (matricula or "").strip().lower()
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    enrich = _enrich_agentes([mat], reference_date=date_to) if mat else {}
    meta = enrich.get(mat, {})
    base.update(
        {
            "matricula": mat,
            "agent_name": meta.get("agent_name") or mat,
            "team": meta.get("team") or "",
            "leader_name": meta.get("leader_name") or "",
        }
    )
    if not mat:
        base.update(
            {
                "count": 0,
                "tma_seconds": 0.0,
                "analysis_seconds_sum": 0,
                "serie_diaria": [],
                "top_workflows": [],
                "top_resultados": [],
            }
        )
        return base

    snap, facts = _facts_qs(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        matricula_destino=mat,
        date_from=date_from,
        date_to=date_to,
    )
    if snap is None:
        base.update(
            {
                "count": 0,
                "tma_seconds": 0.0,
                "analysis_seconds_sum": 0,
                "serie_diaria": [],
                "top_workflows": [],
                "top_resultados": [],
            }
        )
        return base

    count = facts.count()
    timed_facts = facts.exclude(tempo_analise_segundos__isnull=True)
    count_com_tempo = timed_facts.count()
    secs_agg = timed_facts.aggregate(s=Sum("tempo_analise_segundos"))
    secs = int(secs_agg["s"] or 0)
    tma = round(secs / count_com_tempo, 1) if count_com_tempo else None

    serie_diaria = []
    for row in (
        facts.exclude(conclusao_destino_at__isnull=True)
        .values("conclusao_destino_at__date")
        .annotate(
            count=Count("id"),
            analysis_seconds_sum=Sum("tempo_analise_segundos"),
        )
        .order_by("conclusao_destino_at__date")
    ):
        day = row["conclusao_destino_at__date"]
        serie_diaria.append(
            {
                "day": day.isoformat() if day else "",
                "count": int(row["count"] or 0),
                "analysis_seconds_sum": int(row["analysis_seconds_sum"] or 0),
            }
        )

    def _top_from_facts(field: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = (
            facts.values(field)
            .annotate(
                count=Count("id"),
                count_com_tempo=Count("tempo_analise_segundos"),
                analysis_seconds_sum=Sum("tempo_analise_segundos"),
            )
            .order_by("-count")[:limit]
        )
        out = []
        for r in rows:
            c = int(r["count"] or 0)
            timed = int(r["count_com_tempo"] or 0)
            s = int(r["analysis_seconds_sum"] or 0)
            out.append(
                {
                    "key": r[field] or "(vazio)",
                    "count": c,
                    "analysis_seconds_sum": s,
                    "count_com_tempo": timed,
                    "tma_seconds": round(s / timed, 1) if timed else None,
                }
            )
        return out

    if not serie_diaria:
        _, mat_qs = _agg_qs(
            periodo_mes=snap.periodo_mes,
            date_from=date_from,
            date_to=date_to,
            dimension=DIM_MATRICULA_DESTINO,
        )
        mat_qs = mat_qs.filter(key=mat)
        for row in (
            mat_qs.values("day")
            .annotate(count=Sum("count"), analysis_seconds_sum=Sum("analysis_seconds_sum"))
            .order_by("day")
        ):
            serie_diaria.append(
                {
                    "day": row["day"].isoformat(),
                    "count": int(row["count"] or 0),
                    "analysis_seconds_sum": int(row["analysis_seconds_sum"] or 0),
                }
            )

    base.update(
        {
            "periodo_mes": snap.periodo_mes,
            "count": count,
            "count_com_tempo": count_com_tempo,
            "cobertura_tempo_pct": round(100.0 * count_com_tempo / count, 2) if count else 0.0,
            "tma_seconds": tma,
            "analysis_seconds_sum": secs,
            "serie_diaria": serie_diaria,
            "top_workflows": _top_from_facts("workflow_origem"),
            "top_resultados": _top_from_facts("resultado_destino"),
        }
    )
    return base


def serialize_fila_amostra(
    *,
    idade_bucket: str | None = None,
    transaction_status: str | None = None,
    workflow_origem: str | None = None,
    q: str | None = None,
    ordering: str = "created_ts",
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    # Lista vem do snapshot com itens; status/total do latest (pode ser o mesmo)
    snap_items = latest_snapshot_with_items()
    snap = snap_items or latest_successful_snapshot()
    base = serialize_status(latest_successful_snapshot() or snap)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), LIST_FETCH_PAGE_SIZE_MAX))
    order = ordering if ordering in FILA_LIST_ORDERING else "created_ts"
    if snap is None:
        base.update(
            {
                "count": 0,
                "page": page,
                "page_size": page_size,
                "num_pages": 0,
                "ordering": order,
                "results": [],
            }
        )
        return base

    qs = CaseFilaSampleItem.objects.filter(snapshot=snap)
    bucket = (idade_bucket or "").strip()
    if bucket:
        qs = qs.filter(idade_bucket=bucket)
    status = (transaction_status or "").strip()
    if status:
        qs = qs.filter(transaction_status__iexact=status)
    workflow = (workflow_origem or "").strip()
    if workflow:
        qs = qs.filter(workflow_origem__icontains=workflow)
    term = (q or "").strip()
    if term:
        qs = qs.filter(
            Q(protocolo_id__icontains=term)
            | Q(protocolo_origem__icontains=term)
            | Q(cliente_origem__icontains=term)
        )

    total = qs.count()
    num_pages = (total + page_size - 1) // page_size if total else 0
    offset = (page - 1) * page_size
    # desempate estável
    order_by = (order, "protocolo_id") if "protocolo_id" not in order.lstrip("-") else (order,)
    rows = list(qs.order_by(*order_by)[offset : offset + page_size])
    results = [
        {
            "protocolo_id": item.protocolo_id,
            "protocolo_origem": item.protocolo_origem,
            "transaction_status": item.transaction_status,
            "idade_bucket": item.idade_bucket,
            "created_ts": _isoformat_local(item.created_ts),
            "cadastro_origem_at": _isoformat_local(item.cadastro_origem_at),
            "workflow_origem": item.workflow_origem,
            "cliente_origem": item.cliente_origem,
        }
        for item in rows
    ]
    base.update(
        {
            "idade_bucket": bucket or None,
            "transaction_status": status or None,
            "workflow_origem": workflow or None,
            "q": term or None,
            "ordering": order,
            "count": total,
            "page": page,
            "page_size": page_size,
            "num_pages": num_pages,
            "results": results,
            "list_snapshot_id": snap.pk,
        }
    )
    return base
