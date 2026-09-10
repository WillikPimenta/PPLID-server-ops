"""Reconciliação somente leitura dos snapshots do Case Manager."""

from __future__ import annotations

from typing import Any

from django.db.models import Count, Sum

from apps.produtividade_case.constants import DIM_VOLUME_DIA
from apps.produtividade_case.models import CaseConsolidadoFact, CaseFilaSampleItem
from apps.produtividade_case.services.consolidado_agg import latest_consolidado_snapshot
from apps.produtividade_case.services.fila_sync import latest_successful_snapshot


def reconcile_case_manager(*, periodo_mes: str | None = None) -> dict[str, Any]:
    """Retorna invariantes agregados, sem identificadores de protocolo ou PII."""
    checks: list[dict[str, Any]] = []
    consolidado = latest_consolidado_snapshot(periodo_mes)
    if consolidado is None:
        checks.append({"name": "consolidado_disponivel", "ok": False, "actual": 0})
    else:
        facts = CaseConsolidadoFact.objects.filter(snapshot=consolidado)
        facts_count = facts.count()
        agg_count = int(
            consolidado.daily_aggs.filter(dimension=DIM_VOLUME_DIA).aggregate(
                total=Sum("count")
            )["total"]
            or 0
        )
        timed = facts.aggregate(
            count=Count("tempo_analise_segundos"),
            seconds=Sum("tempo_analise_segundos"),
        )
        checks.extend(
            [
                {
                    "name": "consolidado_header_vs_facts",
                    "ok": consolidado.total_protocolos == facts_count,
                    "expected": consolidado.total_protocolos,
                    "actual": facts_count,
                },
                {
                    "name": "consolidado_facts_vs_volume",
                    "ok": facts_count == agg_count,
                    "expected": facts_count,
                    "actual": agg_count,
                },
                {
                    "name": "tempos_validos",
                    "ok": int(timed["seconds"] or 0) >= 0,
                    "count_com_tempo": int(timed["count"] or 0),
                    "seconds": int(timed["seconds"] or 0),
                },
            ]
        )

    fila = latest_successful_snapshot()
    if fila is None:
        checks.append({"name": "fila_disponivel", "ok": False, "actual": 0})
    else:
        items_count = CaseFilaSampleItem.objects.filter(snapshot=fila).count()
        checks.append(
            {
                "name": "fila_detalhe_nao_excede_header",
                "ok": items_count <= fila.total_abertos,
                "total_abertos": fila.total_abertos,
                "items_count": items_count,
                "items_truncated": items_count < fila.total_abertos,
            }
        )

    return {
        "periodo_mes": periodo_mes or (consolidado.periodo_mes if consolidado else ""),
        "ok": bool(checks) and all(check["ok"] for check in checks),
        "checks": checks,
    }
