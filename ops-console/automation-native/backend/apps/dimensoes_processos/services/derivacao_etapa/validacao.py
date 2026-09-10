# -*- coding: utf-8 -*-
"""Resumo de validação da base derivacao_etapa_diaria."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Count, Max, Min, Sum

from apps.dimensoes_processos.models import DerivacaoEtapaDiaria, DerivacaoEtapaImportRun
from apps.dimensoes_processos.services.derivacao_etapa.normalize import (
    normalize_csv_etapa_key,
    normalize_csv_name_key,
)
from apps.dimensoes_processos.services.meta_etapa_lookup import normalize_etapa_nome


def _filter_qs(qs, params: dict[str, Any]):
    if params.get("start_date"):
        qs = qs.filter(data__gte=params["start_date"])
    if params.get("end_date"):
        qs = qs.filter(data__lte=params["end_date"])
    if params.get("id_cliente"):
        qs = qs.filter(cliente_id=params["id_cliente"])
    if params.get("id_workflow"):
        qs = qs.filter(workflow_id=params["id_workflow"])
    if params.get("id_etapa"):
        qs = qs.filter(etapa_id=params["id_etapa"])
    return qs


def _nome_divergente(row: DerivacaoEtapaDiaria) -> bool:
    c_ok = normalize_csv_name_key(row.cliente_nome_origem) == normalize_csv_name_key(row.cliente.nome)
    w_ok = normalize_csv_name_key(row.workflow_nome_origem) == normalize_csv_name_key(row.workflow.nome)
    e_ok = normalize_csv_etapa_key(row.etapa_nome_origem) == normalize_etapa_nome(row.etapa.nome)
    return not (c_ok and w_ok and e_ok)


def _divergencia_detalhe(row: DerivacaoEtapaDiaria) -> list[str]:
    issues: list[str] = []
    if normalize_csv_name_key(row.cliente_nome_origem) != normalize_csv_name_key(row.cliente.nome):
        issues.append("cliente")
    if normalize_csv_name_key(row.workflow_nome_origem) != normalize_csv_name_key(row.workflow.nome):
        issues.append("workflow")
    if normalize_csv_etapa_key(row.etapa_nome_origem) != normalize_etapa_nome(row.etapa.nome):
        issues.append("etapa")
    return issues


def build_derivacao_validacao(params: dict[str, Any]) -> dict[str, Any]:
    qs = _filter_qs(
        DerivacaoEtapaDiaria.objects.select_related("cliente", "workflow", "etapa"),
        params,
    )

    agg = qs.aggregate(
        linhas=Count("id"),
        registros=Sum("registros"),
        dias_distintos=Count("data", distinct=True),
        clientes_distintos=Count("cliente_id", distinct=True),
        workflows_distintos=Count("workflow_id", distinct=True),
        etapas_distintas=Count("etapa_id", distinct=True),
        data_min=Min("data"),
        data_max=Max("data"),
        percentual_min=Min("percentual"),
        percentual_max=Max("percentual"),
    )

    pct_fora = 0
    nome_divergente = 0
    linhas_ok = 0
    amostra_alertas: list[dict[str, Any]] = []
    for row in qs.iterator(chunk_size=1000):
        issues: list[str] = []
        if row.percentual > Decimal("100") or row.percentual < Decimal("0"):
            issues.append("percentual")
            pct_fora += 1
        if _nome_divergente(row):
            issues.append("nome")
            nome_divergente += 1
            issues.extend(_divergencia_detalhe(row))
        if not issues:
            linhas_ok += 1
        if issues and len(amostra_alertas) < 30:
            amostra_alertas.append(
                {
                    "id": row.pk,
                    "data": row.data.isoformat(),
                    "cliente_nome": row.cliente.nome,
                    "workflow_nome": row.workflow.nome,
                    "etapa_nome": row.etapa.nome,
                    "registros": row.registros,
                    "percentual": str(row.percentual),
                    "issues": sorted(set(issues)),
                }
            )

    cobertura_diaria = [
        {
            "data": row["data"].isoformat(),
            "linhas": row["linhas"],
            "registros": row["registros"] or 0,
        }
        for row in qs.values("data")
        .annotate(linhas=Count("id"), registros=Sum("registros"))
        .order_by("data")
    ]

    top_workflows = [
        {
            "id_workflow": row["workflow_id"],
            "workflow_nome": row["workflow__nome"],
            "linhas": row["linhas"],
            "registros": row["registros"] or 0,
        }
        for row in qs.values("workflow_id", "workflow__nome")
        .annotate(linhas=Count("id"), registros=Sum("registros"))
        .order_by("-registros")[:10]
    ]

    last = (
        DerivacaoEtapaImportRun.objects.filter(run_kind=DerivacaoEtapaImportRun.KIND_IMPORT)
        .order_by("-started_at")
        .first()
    )
    last_import = None
    if last is not None:
        metrics = last.metrics or {}
        last_import = {
            "id": last.pk,
            "status": last.status,
            "started_at": last.started_at.isoformat(),
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "files_processed": last.files_processed,
            "rows_inserted": last.rows_inserted,
            "rows_rejected": last.rows_rejected,
            "rows_skipped_total": last.rows_skipped_total,
            "unmatched_cliente_count": metrics.get("unmatched_cliente_count", 0),
            "unmatched_workflow_count": metrics.get("unmatched_workflow_count", 0),
            "unmatched_etapa_count": metrics.get("unmatched_etapa_count", 0),
            "unmatched_cliente": metrics.get("unmatched_cliente", [])[:10],
            "unmatched_workflow": metrics.get("unmatched_workflow", [])[:10],
            "unmatched_etapa": metrics.get("unmatched_etapa", [])[:10],
        }

    linhas = int(agg["linhas"] or 0)
    return {
        "ok": True,
        "totais": {
            "linhas": linhas,
            "registros": int(agg["registros"] or 0),
            "dias_distintos": int(agg["dias_distintos"] or 0),
            "clientes_distintos": int(agg["clientes_distintos"] or 0),
            "workflows_distintos": int(agg["workflows_distintos"] or 0),
            "etapas_distintas": int(agg["etapas_distintas"] or 0),
            "data_min": agg["data_min"].isoformat() if agg["data_min"] else None,
            "data_max": agg["data_max"].isoformat() if agg["data_max"] else None,
            "percentual_min": str(agg["percentual_min"]) if agg["percentual_min"] is not None else None,
            "percentual_max": str(agg["percentual_max"]) if agg["percentual_max"] is not None else None,
        },
        "alertas": {
            "percentual_fora_faixa": pct_fora,
            "nome_origem_divergente": nome_divergente,
            "linhas_ok": linhas_ok,
            "taxa_ok_pct": round(100.0 * linhas_ok / linhas, 2) if linhas else None,
        },
        "cobertura_diaria": cobertura_diaria,
        "top_workflows": top_workflows,
        "amostra_alertas": amostra_alertas,
        "ultimo_import": last_import,
    }
