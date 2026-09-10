# -*- coding: utf-8 -*-
"""Métricas do MVP operacional (mediana/P90, matriz origem×destino, aging)."""

from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from datetime import date
from typing import Any

from django.db.models import Count
from django.utils import timezone

from apps.produtividade_case.models import CaseConsolidadoFact, CaseFilaSnapshot
from apps.produtividade_case.services.consolidado_agg import (
    _norm_key,
    latest_consolidado_snapshot,
    selected_consolidado_snapshots,
    serialize_analitica_status,
)


def percentile_nearest_rank(sorted_vals: list[float], pct: float) -> float | None:
    """Percentil por nearest-rank (pct em 0–100). Lista deve estar ordenada."""
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    rank = max(1, min(n, int(math.ceil(pct / 100.0 * n))))
    return float(sorted_vals[rank - 1])


def _tempo_stats(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {
            "count_com_tempo": 0,
            "tma_seconds": None,
            "mediana_seconds": None,
            "p90_seconds": None,
            "horas_consumidas": 0.0,
        }
    ordered = sorted(values)
    total = sum(ordered)
    n = len(ordered)
    return {
        "count_com_tempo": n,
        "tma_seconds": round(total / n, 1),
        "mediana_seconds": percentile_nearest_rank(ordered, 50),
        "p90_seconds": percentile_nearest_rank(ordered, 90),
        "horas_consumidas": round(total / 3600.0, 3),
    }


def build_workflow_tempo_stats_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Materializa mediana/P90/horas por workflow a partir das linhas do Excel."""
    buckets: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        wf = str(row.get("workflow_origem") or "")
        secs = row.get("tempo_analise_segundos")
        if secs is None:
            continue
        try:
            s = int(secs)
        except (TypeError, ValueError):
            continue
        if s < 0:
            continue
        buckets[wf].append(s)
    return {wf: _tempo_stats(vals) for wf, vals in buckets.items()}


def enrich_workflow_tempo_stats(
    items: list[dict[str, Any]],
    *,
    periodo_mes: str | None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Acrescenta mediana/P90/horas por workflow (JSON materializado ou fallback facts)."""
    snaps = selected_consolidado_snapshots(
        periodo_mes=periodo_mes, date_from=date_from, date_to=date_to
    )
    if not snaps:
        for row in items:
            row.setdefault("mediana_seconds", None)
            row.setdefault("p90_seconds", None)
            row.setdefault("horas_consumidas", 0.0)
        return items

    snap = snaps[-1]
    stats_map = getattr(snap, "tempo_stats_json", None) or {}
    # O materializado representa o mês inteiro; com intervalo, calcular nos facts.
    if not date_from and not date_to and isinstance(stats_map, dict) and stats_map:
        for row in items:
            key = str(row.get("key") or "")
            stats = stats_map.get(key) or {}
            if stats.get("tma_seconds") is not None:
                row["tma_seconds"] = stats["tma_seconds"]
            row["mediana_seconds"] = stats.get("mediana_seconds")
            row["p90_seconds"] = stats.get("p90_seconds")
            row["horas_consumidas"] = stats.get("horas_consumidas") or 0.0
            row["count_com_tempo"] = int(stats.get("count_com_tempo") or 0)
            total = int(row.get("count") or 0)
            row["cobertura_tempo_pct"] = (
                round(100.0 * row["count_com_tempo"] / total, 2) if total else 0.0
            )
        return items

    # Fallback: snapshots antigos sem materialização
    buckets: dict[str, list[int]] = defaultdict(list)
    qs = (
        CaseConsolidadoFact.objects.filter(snapshot__in=snaps)
        .exclude(tempo_analise_segundos__isnull=True)
    )
    if date_from:
        qs = qs.filter(conclusao_destino_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(conclusao_destino_at__date__lte=date_to)
    qs = qs.values_list("workflow_origem", "tempo_analise_segundos")
    for wf, secs in qs.iterator(chunk_size=2000):
        try:
            s = int(secs)
        except (TypeError, ValueError):
            continue
        if s < 0:
            continue
        buckets[str(wf or "")].append(s)

    for row in items:
        key = str(row.get("key") or "")
        stats = _tempo_stats(buckets.get(key, []))
        if stats["tma_seconds"] is not None:
            row["tma_seconds"] = stats["tma_seconds"]
        row["mediana_seconds"] = stats["mediana_seconds"]
        row["p90_seconds"] = stats["p90_seconds"]
        row["horas_consumidas"] = stats["horas_consumidas"]
        row["count_com_tempo"] = int(stats["count_com_tempo"] or 0)
        total = int(row.get("count") or 0)
        row["cobertura_tempo_pct"] = (
            round(100.0 * row["count_com_tempo"] / total, 2) if total else 0.0
        )
    return items


def enrich_agent_tempo_stats(
    items: list[dict[str, Any]],
    *,
    periodo_mes: str | None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Calcula tempos/cobertura por matrícula no mesmo universo temporal da lista."""
    snaps = selected_consolidado_snapshots(
        periodo_mes=periodo_mes, date_from=date_from, date_to=date_to
    )
    if not snaps:
        return items
    facts = CaseConsolidadoFact.objects.filter(snapshot__in=snaps)
    if date_from:
        facts = facts.filter(conclusao_destino_at__date__gte=date_from)
    if date_to:
        facts = facts.filter(conclusao_destino_at__date__lte=date_to)
    buckets: dict[str, list[int]] = defaultdict(list)
    for mat, secs in (
        facts.exclude(tempo_analise_segundos__isnull=True)
        .values_list("matricula_destino", "tempo_analise_segundos")
        .iterator(chunk_size=2000)
    ):
        buckets[str(mat or "")].append(int(secs))
    for row in items:
        stats = _tempo_stats(buckets.get(str(row.get("key") or ""), []))
        row.update(stats)
        total = int(row.get("count") or 0)
        row["cobertura_tempo_pct"] = (
            round(100.0 * int(stats["count_com_tempo"] or 0) / total, 2)
            if total
            else 0.0
        )
    return items


def serialize_matriz_origem_destino(
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    top: int = 12,
) -> dict[str, Any]:
    """Matriz resultado origem × destino (alinhamento textual — não prova erro)."""
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    snaps = selected_consolidado_snapshots(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
    )
    if not snaps:
        base.update(
            {
                "cells": [],
                "total_comparavel": 0,
                "total_coincidente": 0,
                "total_nao_coincidente": 0,
                "total_nao_comparavel": 0,
                "taxa_coincidencia": None,
                "nota": (
                    "Comparação textual origem×destino. Não interpreta divergência como erro "
                    "sem regra de negócio (A validar)."
                ),
            }
        )
        return base

    facts = CaseConsolidadoFact.objects.filter(snapshot__in=snaps)
    if date_from:
        facts = facts.filter(conclusao_destino_at__date__gte=date_from)
    if date_to:
        facts = facts.filter(conclusao_destino_at__date__lte=date_to)

    total_facts = facts.count()
    conclusion_totals = {"automatico": 0, "manual": 0, "nao_classificado": 0}
    for row in facts.values("tipo_conclusao_origem").annotate(count=Count("id")):
        raw_kind = str(row.get("tipo_conclusao_origem") or "").strip().casefold()
        kind = "".join(
            ch
            for ch in unicodedata.normalize("NFKD", raw_kind)
            if not unicodedata.combining(ch)
        )
        count = int(row.get("count") or 0)
        if kind.startswith("autom"):
            conclusion_totals["automatico"] += count
        elif kind.startswith("manual"):
            conclusion_totals["manual"] += count
        else:
            conclusion_totals["nao_classificado"] += count
    comparable_facts = facts.exclude(resultado_origem="").exclude(resultado_destino="")
    rows = (
        comparable_facts.values("resultado_origem", "resultado_destino")
        .annotate(count=Count("id"))
        .order_by("-count")
    )
    cells: list[dict[str, Any]] = []
    total = 0
    coincidente = 0
    for row in rows:
        orig = _norm_key(row["resultado_origem"], empty="(vazio)")
        dest = _norm_key(row["resultado_destino"], empty="(vazio)")
        count = int(row["count"] or 0)
        total += count
        match = orig.casefold() == dest.casefold()
        if match:
            coincidente += count
        cells.append(
            {
                "resultado_origem": orig,
                "resultado_destino": dest,
                "count": count,
                "coincidente": match,
            }
        )

    # Limita células exibidas, mantendo totais no universo completo
    top_cells = cells[: max(1, top * top)]
    taxa = round(100.0 * coincidente / total, 2) if total else None
    base.update(
        {
            "cells": top_cells,
            "total_comparavel": total,
            "total_coincidente": coincidente,
            "total_nao_coincidente": total - coincidente,
            "total_nao_comparavel": total_facts - total,
            "total_automatico": conclusion_totals["automatico"],
            "total_manual": conclusion_totals["manual"],
            "total_tipo_conclusao_nao_classificado": conclusion_totals[
                "nao_classificado"
            ],
            "taxa_coincidencia": taxa,
            "nota": (
                "Comparação textual origem×destino. Não interpreta divergência como erro "
                "sem regra de negócio (A validar)."
            ),
        }
    )
    return base


def empty_fila_aging() -> dict[str, Any]:
    return {
        "aging_count": 0,
        "aging_medio_seconds": None,
        "aging_mediano_seconds": None,
        "aging_p90_seconds": None,
    }


def compute_aging_stats(
    ref,
    timestamps: list,
) -> dict[str, Any]:
    """Calcula aging contínuo (segundos) a partir de timestamps de cadastro."""
    empty = empty_fila_aging()
    if ref is None:
        ref = timezone.now()
    ages: list[float] = []
    for ts in timestamps:
        if ts is None:
            continue
        delta = (ref - ts).total_seconds()
        if delta < 0:
            delta = 0.0
        ages.append(delta)
    if not ages:
        return empty
    ordered = sorted(ages)
    total = sum(ordered)
    n = len(ordered)
    return {
        "aging_count": n,
        "aging_medio_seconds": round(total / n, 1),
        "aging_mediano_seconds": percentile_nearest_rank(ordered, 50),
        "aging_p90_seconds": percentile_nearest_rank(ordered, 90),
    }


def _iter_fila_aging_timestamps(snap: CaseFilaSnapshot):
    """Timestamps da amostra (values_list — sem rehidratar FK)."""
    pairs = snap.sample_items.values_list(
        "cadastro_origem_at", "created_ts"
    ).iterator(chunk_size=2000)
    for cad, created in pairs:
        yield cad or created


def serialize_fila_aging(snap: CaseFilaSnapshot | None) -> dict[str, Any]:
    """Aging contínuo: preferir campos pré-calculados no snapshot (O(1))."""
    empty = empty_fila_aging()
    if snap is None:
        return empty

    # aging_count null = snapshot antigo sem materialização
    if getattr(snap, "aging_count", None) is not None:
        return {
            "aging_count": int(snap.aging_count or 0),
            "aging_medio_seconds": snap.aging_medio_seconds,
            "aging_mediano_seconds": snap.aging_mediano_seconds,
            "aging_p90_seconds": snap.aging_p90_seconds,
        }

    try:
        timestamps = list(_iter_fila_aging_timestamps(snap))
        return compute_aging_stats(snap.captured_at or timezone.now(), timestamps)
    except Exception:
        return empty
