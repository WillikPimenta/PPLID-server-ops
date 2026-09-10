"""Higienização de ciclos vigentes duplicados (MetaEtapa / ProjecaoSla)."""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.db.models import Count

from apps.dimensoes_processos.models import MetaEtapa, ProjecaoSla
from apps.dimensoes_processos.services.vigencia import finalizar_ciclo


def _group_duplicate_vigentes_sla() -> list[dict[str, Any]]:
    dup_keys = (
        ProjecaoSla.objects.filter(data_fim__isnull=True)
        .values("cliente_id", "workflow_id", "nivel_hierarquico_id", "dias_semana")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )
    groups: list[dict[str, Any]] = []
    for item in dup_keys:
        rows = list(
            ProjecaoSla.objects.filter(
                cliente_id=item["cliente_id"],
                workflow_id=item["workflow_id"],
                nivel_hierarquico_id=item["nivel_hierarquico_id"],
                dias_semana=item["dias_semana"],
                data_fim__isnull=True,
            ).order_by("-data_inicio", "-id")
        )
        groups.append(
            {
                "kind": "projecao_sla",
                "cliente_id": item["cliente_id"],
                "workflow_id": item["workflow_id"],
                "nivel_hierarquico_id": item["nivel_hierarquico_id"],
                "dias_semana": item["dias_semana"],
                "count": len(rows),
                "keep_id": rows[0].pk,
                "keep_data_inicio": rows[0].data_inicio.isoformat(),
                "finalize_ids": [row.pk for row in rows[1:]],
            }
        )
    return groups


def _group_duplicate_vigentes_meta() -> list[dict[str, Any]]:
    dup_keys = (
        MetaEtapa.objects.filter(data_fim__isnull=True)
        .values("etapa_id", "servico_id")
        .annotate(count=Count("id"))
        .filter(count__gt=1)
    )
    groups: list[dict[str, Any]] = []
    for item in dup_keys:
        qs = MetaEtapa.objects.filter(
            etapa_id=item["etapa_id"],
            data_fim__isnull=True,
        )
        if item["servico_id"] is None:
            qs = qs.filter(servico_id__isnull=True)
        else:
            qs = qs.filter(servico_id=item["servico_id"])
        rows = list(qs.order_by("-data_inicio", "-id"))
        groups.append(
            {
                "kind": "meta_etapa",
                "etapa_id": item["etapa_id"],
                "servico_id": item["servico_id"],
                "count": len(rows),
                "keep_id": rows[0].pk,
                "keep_data_inicio": rows[0].data_inicio.isoformat(),
                "finalize_ids": [row.pk for row in rows[1:]],
            }
        )
    return groups


def list_duplicate_vigente_groups() -> dict[str, Any]:
    sla_groups = _group_duplicate_vigentes_sla()
    meta_groups = _group_duplicate_vigentes_meta()
    return {
        "projecao_sla": sla_groups,
        "meta_etapa": meta_groups,
        "total_groups": len(sla_groups) + len(meta_groups),
        "total_rows_to_finalize": sum(len(g["finalize_ids"]) for g in sla_groups + meta_groups),
    }


@transaction.atomic
def cleanup_duplicate_vigentes(*, dry_run: bool = True) -> dict[str, Any]:
    """Mantém o ciclo vigente mais recente e finaliza os demais."""
    groups = list_duplicate_vigente_groups()
    actions: list[dict[str, Any]] = []

    for group in groups["projecao_sla"]:
        keeper = ProjecaoSla.objects.get(pk=group["keep_id"])
        for row_id in group["finalize_ids"]:
            row = ProjecaoSla.objects.get(pk=row_id)
            data_fim = keeper.data_inicio - timedelta(days=1)
            if data_fim < row.data_inicio:
                data_fim = row.data_inicio
            actions.append(
                {
                    "kind": "projecao_sla",
                    "action": "finalize",
                    "id": row_id,
                    "data_fim": data_fim.isoformat(),
                    "kept_id": keeper.pk,
                }
            )
            if not dry_run:
                finalizar_ciclo(row, data_fim)

    for group in groups["meta_etapa"]:
        keeper = MetaEtapa.objects.get(pk=group["keep_id"])
        for row_id in group["finalize_ids"]:
            row = MetaEtapa.objects.get(pk=row_id)
            data_fim = keeper.data_inicio - timedelta(days=1)
            if data_fim < row.data_inicio:
                data_fim = row.data_inicio
            actions.append(
                {
                    "kind": "meta_etapa",
                    "action": "finalize",
                    "id": row_id,
                    "data_fim": data_fim.isoformat(),
                    "kept_id": keeper.pk,
                }
            )
            if not dry_run:
                finalizar_ciclo(row, data_fim)

    return {
        "dry_run": dry_run,
        "groups_before": groups["total_groups"],
        "rows_finalized": 0 if dry_run else len(actions),
        "actions": actions,
        "groups_after": list_duplicate_vigente_groups()["total_groups"] if not dry_run else groups["total_groups"],
    }
