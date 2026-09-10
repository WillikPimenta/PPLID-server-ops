# -*- coding: utf-8 -*-
"""Agregação de auditorias G Auditoria por agente (rotina_g_auditoria_record)."""
from __future__ import annotations

import csv
import io
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Any

from django.db.models import Count, Max, Min
from django.db.models.functions import Lower

from apps.replicacao_d1.config_models import ReplicacaoD1ConfigGeral, ReplicacaoD1EscalaDia
from apps.replicacao_d1.models import ReplicacaoD1FonteRegistro
from apps.replicacao_d1.services.dashboard import parse_dashboard_params
from apps.replicacao_d1.services.source_batch import classify_matricula_tipo, matricula_tipo_q
from apps.rotina_bruto.models import RotinaGAuditoriaRecord
from apps.workforce.models import Agent

MATRICULA_TIPO_LABELS = {
    ReplicacaoD1FonteRegistro.MATRICULA_MANUAL: "Manual",
    ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA: "Automático",
    ReplicacaoD1FonteRegistro.MATRICULA_DESCONHECIDA: "Desconhecido",
}


@dataclass
class AgentesDashboardParams:
    data_de: date | None = None
    data_ate: date | None = None
    matricula_tipo: str = ""


def parse_agentes_params(raw: dict[str, Any]) -> AgentesDashboardParams:
    base = parse_dashboard_params(raw)
    matricula_tipo = str(raw.get("matricula_tipo") or "").strip().casefold()
    if isinstance(raw.get("matricula_tipo"), (list, tuple)) and raw.get("matricula_tipo"):
        matricula_tipo = str(raw["matricula_tipo"][0]).strip().casefold()
    allowed = {
        ReplicacaoD1FonteRegistro.MATRICULA_MANUAL,
        ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA,
        ReplicacaoD1FonteRegistro.MATRICULA_DESCONHECIDA,
    }
    return AgentesDashboardParams(
        data_de=base.data_de,
        data_ate=base.data_ate,
        matricula_tipo=matricula_tipo if matricula_tipo in allowed else "",
    )


def _agents_lookup() -> dict[str, str]:
    agents: dict[str, str] = {}
    for row in Agent.objects.annotate(lan_lower=Lower("user_lan_id")).values(
        "user_lan_id", "full_name"
    ):
        key = (row["user_lan_id"] or "").strip().lower()
        if key:
            agents[key] = (row["full_name"] or "").strip()
    return agents


def _base_queryset(params: AgentesDashboardParams):
    assert params.data_de and params.data_ate
    qs = RotinaGAuditoriaRecord.objects.filter(
        is_active=True,
        is_quarantined=False,
        data_auditoria__gte=params.data_de,
        data_auditoria__lte=params.data_ate,
        data_auditoria__isnull=False,
    ).exclude(matricula_agente="")
    tipo_q = matricula_tipo_q(params.matricula_tipo, field="matricula_agente")
    if tipo_q is not None:
        qs = qs.filter(tipo_q)
    return qs


def _period_days(params: AgentesDashboardParams) -> int:
    assert params.data_de and params.data_ate
    return (params.data_ate - params.data_de).days + 1


def _capacity_context(params: AgentesDashboardParams, media_por_agente: float) -> dict[str, Any]:
    assert params.data_de and params.data_ate
    escala_rows = ReplicacaoD1EscalaDia.objects.filter(
        data__gte=params.data_de,
        data__lte=params.data_ate,
    )
    escala_count = escala_rows.count()
    auditores_total = sum(row.auditores_brflow for row in escala_rows)
    auditores_media = round(auditores_total / escala_count, 1) if escala_count else 0.0

    geral = ReplicacaoD1ConfigGeral.objects.order_by("-updated_at").first()
    meta_produ = float(geral.meta_produ_diaria) if geral else 300.0

    cota_esperada_agente = meta_produ
    gap_sugerido = None
    if media_por_agente > cota_esperada_agente * 1.2:
        gap_sugerido = round(media_por_agente - cota_esperada_agente, 1)

    return {
        "auditores_escala_media": auditores_media,
        "dias_com_escala": escala_count,
        "meta_produ_diaria": meta_produ,
        "cota_esperada_por_agente": cota_esperada_agente,
        "gap_sugerido": gap_sugerido,
    }


def build_agentes_dashboard(params: AgentesDashboardParams) -> dict[str, Any]:
    qs = _base_queryset(params)
    period_days = _period_days(params)
    agents_lookup = _agents_lookup()

    grouped = list(
        qs.values("matricula_agente")
        .annotate(
            auditorias=Count("id"),
            protocolos=Count("protocolo_normalizado", distinct=True),
            dias_ativos=Count("data_auditoria", distinct=True),
            primeira_auditoria=Min("data_auditoria"),
            ultima_auditoria=Max("data_auditoria"),
        )
        .order_by("-auditorias", "matricula_agente")
    )

    total_auditorias = sum(row["auditorias"] for row in grouped)
    total_protocolos_unicos = (
        qs.exclude(protocolo_normalizado="")
        .values("protocolo_normalizado")
        .distinct()
        .count()
    )
    agentes_com_volume = len(grouped)
    counts = [row["auditorias"] for row in grouped]
    media_por_agente = round(total_auditorias / agentes_com_volume, 1) if agentes_com_volume else 0.0
    mediana_por_agente = round(statistics.median(counts), 1) if counts else 0.0

    top3_total = sum(row["auditorias"] for row in grouped[:3])
    top3_share_pct = round((top3_total / total_auditorias) * 100, 1) if total_auditorias else 0.0

    daily_rows = list(
        qs.values("matricula_agente", "data_auditoria")
        .annotate(auditorias=Count("id"))
        .order_by("data_auditoria", "matricula_agente")
    )
    daily_by_matricula: dict[str, list[dict[str, Any]]] = {}
    daily_totals: dict[date, int] = {}
    for daily_row in daily_rows:
        day = daily_row["data_auditoria"]
        if not day:
            continue
        matricula_key = daily_row["matricula_agente"] or ""
        auditorias_dia = int(daily_row["auditorias"])
        daily_by_matricula.setdefault(matricula_key, []).append(
            {"data": day.isoformat(), "auditorias": auditorias_dia}
        )
        daily_totals[day] = daily_totals.get(day, 0) + auditorias_dia

    results = []
    for row in grouped:
        matricula_raw = row["matricula_agente"] or ""
        matricula = matricula_raw.strip()
        mat_key = matricula.lower()
        auditorias = int(row["auditorias"])
        matricula_tipo = classify_matricula_tipo(matricula)
        results.append(
            {
                "matricula": matricula,
                "label": agents_lookup.get(mat_key) or matricula,
                "matricula_tipo": matricula_tipo,
                "matricula_tipo_label": MATRICULA_TIPO_LABELS.get(matricula_tipo, matricula_tipo),
                "auditorias": auditorias,
                "protocolos": int(row["protocolos"]),
                "dias_ativos": int(row["dias_ativos"]),
                "primeira_auditoria": row["primeira_auditoria"].isoformat() if row["primeira_auditoria"] else None,
                "ultima_auditoria": row["ultima_auditoria"].isoformat() if row["ultima_auditoria"] else None,
                "daily_series": daily_by_matricula.get(matricula_raw, []),
                "share_pct": round((auditorias / total_auditorias) * 100, 1) if total_auditorias else 0.0,
                "media_dia": round(auditorias / period_days, 1) if period_days else 0.0,
            }
        )

    daily_series = [
        {"data": day.isoformat(), "auditorias": auditorias}
        for day, auditorias in sorted(daily_totals.items())
    ]

    top_chart = [
        {"matricula": row["matricula"], "label": row["label"], "auditorias": row["auditorias"]}
        for row in results[:10]
    ]

    return {
        "meta": {
            "data_de": params.data_de.isoformat() if params.data_de else None,
            "data_ate": params.data_ate.isoformat() if params.data_ate else None,
            "matricula_tipo": params.matricula_tipo or None,
            "date_axis": "data_auditoria",
            "agent_field": "matricula_agente",
            "source_table": "rotina_g_auditoria_record",
            "dias_no_periodo": period_days,
        },
        "filter_options": {
            "matricula_tipo": [
                {"value": ReplicacaoD1FonteRegistro.MATRICULA_MANUAL, "label": "Manual"},
                {"value": ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA, "label": "Automático"},
            ],
        },
        "summary": {
            "total_auditorias": total_auditorias,
            "total_protocolos_unicos": total_protocolos_unicos,
            "agentes_com_volume": agentes_com_volume,
            "media_por_agente": media_por_agente,
            "mediana_por_agente": mediana_por_agente,
            "top3_share_pct": top3_share_pct,
            "dias_no_periodo": period_days,
        },
        "capacity": _capacity_context(params, media_por_agente),
        "daily_series": daily_series,
        "top_chart": top_chart,
        "results": results,
    }


def build_agentes_export_csv(raw_params: dict[str, Any]) -> tuple[str, str]:
    params = parse_agentes_params(raw_params)
    payload = build_agentes_dashboard(params)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "matricula",
            "nome",
            "padrao_matricula",
            "auditorias",
            "protocolos",
            "share_pct",
            "media_dia",
            "dias_ativos",
            "primeira_auditoria",
            "ultima_auditoria",
        ]
    )
    for row in payload["results"]:
        writer.writerow(
            [
                row["matricula"],
                row["label"],
                row["matricula_tipo_label"],
                row["auditorias"],
                row["protocolos"],
                row["share_pct"],
                row["media_dia"],
                row["dias_ativos"],
                row["primeira_auditoria"],
                row["ultima_auditoria"],
            ]
        )
    data_de = params.data_de.isoformat() if params.data_de else "inicio"
    data_ate = params.data_ate.isoformat() if params.data_ate else "fim"
    suffix = f"_{params.matricula_tipo}" if params.matricula_tipo else ""
    filename = f"replicacao_d1_agentes_{data_de}_{data_ate}{suffix}.csv"
    return filename, buffer.getvalue()
