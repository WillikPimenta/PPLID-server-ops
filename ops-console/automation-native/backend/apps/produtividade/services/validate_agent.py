# -*- coding: utf-8 -*-
"""Reconciliação de métricas de produtividade por agente (validação operacional)."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any

from django.db.models import Count, Max, Min, QuerySet
from django.db.models.functions import Lower
from django.utils import timezone

from apps.monitor_eventos.models import MonitorEventoRecord
from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.analytics import (
    META_SHIFT_SECONDS,
    _agent_count_normalized,
    _agent_etapa_impact_totals,
    _agent_period_pct_count,
    _agent_productivity_breakdown_maps,
    _agent_stage_goal_totals,
    _agent_tma_maps,
    _build_shift_groups_raw,
    _compute_rate_metrics,
    _format_hms,
    _iter_shift_groups,
    _meta_sec_per_prot,
    _shift_group_metrics,
    build_pace_for_agent,
    serialize_record,
)
from apps.produtividade.services.goal_adjustment import (
    build_analyzed_daily_lookup_for_qs,
    build_logado_lookup_for_qs,
    build_monitor_ociosidade_lookup_for_qs,
    build_net_ociosidade_lookup_for_qs,
    build_ociosidade_lookup_for_qs,
    jornada_base_seconds,
    build_productivity_discount_lookup_for_qs,
)
from apps.produtividade.services.pace_tracking import (
    build_monitor_daily_logado_lookup,
    build_monitor_hourly_logado_lookup,
    public_pace_fields,
)


def _filter_agent_qs(
    matricula: str,
    *,
    day: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> QuerySet:
    mat = matricula.strip().lower()
    qs = ProductivityRecord.objects.filter(matricula_norm=mat)
    if day is not None:
        qs = qs.filter(
            recorded_at__date=day,
        )
    else:
        if start_date:
            qs = qs.filter(recorded_at__date__gte=start_date)
        if end_date:
            qs = qs.filter(recorded_at__date__lte=end_date)
    return qs.order_by("recorded_at")


def _shift_group_rows(qs: QuerySet) -> list[dict[str, Any]]:
    raw = _build_shift_groups_raw(qs)
    adjusted = _iter_shift_groups(qs, adjust_goal=True)
    rows: list[dict[str, Any]] = []
    for key in sorted(raw.keys()):
        matricula, day, etapa = key
        raw_bucket = raw[key]
        adj_bucket = adjusted.get(key, raw_bucket)
        goal_raw = float(raw_bucket["goal"])
        goal_adj = float(adj_bucket["goal"])
        count = int(raw_bucket["count"])
        seconds = int(raw_bucket["seconds"])
        metrics = _shift_group_metrics(count, seconds, goal_adj)
        meta_spp = _meta_sec_per_prot(goal_adj) if goal_adj > 0 else None
        charged = round(count * meta_spp, 2) if meta_spp and count > 0 else None
        rows.append(
            {
                "matricula": matricula,
                "date": day.isoformat(),
                "etapa": etapa,
                "goal_raw": goal_raw,
                "goal_adjusted": goal_adj,
                "count": count,
                "seconds": seconds,
                "seconds_hms": _format_hms(seconds),
                "pct_count_adjusted": metrics.get("productivity_pct_count"),
                "meta_sec_per_prot": meta_spp,
                "agent_sec_per_prot": metrics.get("agent_sec_per_prot"),
                "charged_seconds": charged,
                "charged_hms": _format_hms(int(charged)) if charged else None,
            }
        )
    return rows


def _impact_by_etapa(qs: QuerySet, matricula: str) -> list[dict[str, Any]]:
    matricula = matricula.strip().lower()
    by_etapa: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "seconds": 0, "goal": 0.0, "meta_seconds": 0, "days": 0}
    )
    for (mat, _day, etapa), totals in _iter_shift_groups(qs, adjust_goal=False).items():
        if mat != matricula:
            continue
        bucket = by_etapa[etapa or ""]
        bucket["count"] += totals["count"]
        bucket["seconds"] += totals["seconds"]
        bucket["goal"] += totals["goal"]
        bucket["meta_seconds"] += META_SHIFT_SECONDS
        bucket["days"] += 1

    result: list[dict[str, Any]] = []
    for etapa, totals in sorted(by_etapa.items()):
        metrics = _compute_rate_metrics(
            totals["count"],
            totals["seconds"],
            totals["goal"],
            totals["meta_seconds"],
        )
        result.append(
            {
                "etapa": etapa,
                "days_in_period": totals["days"],
                "count": totals["count"],
                "seconds": totals["seconds"],
                "goal_sum": round(totals["goal"], 2),
                "agent_sec_per_prot": metrics.get("agent_sec_per_prot"),
                "meta_sec_per_prot": metrics.get("meta_sec_per_prot"),
                "gap_sec_per_prot": metrics.get("gap_sec_per_prot"),
                "impact_time_seconds": metrics.get("impact_time_seconds"),
                "impact_time_hms": metrics.get("impact_time_hms"),
            }
        )
    return result


def _audit_stage_goals(qs: QuerySet) -> dict[str, Any]:
    goals = (
        qs.filter(stage_goal__gt=0)
        .values("etapa")
        .annotate(
            goal_min=Min("stage_goal"),
            goal_max=Max("stage_goal"),
            rows=Count("id"),
            distinct_goals=Count("stage_goal", distinct=True),
        )
        .order_by("etapa")
    )
    per_etapa = [
        {
            "etapa": row["etapa"],
            "goal_min": float(row["goal_min"]) if row["goal_min"] else None,
            "goal_max": float(row["goal_max"]) if row["goal_max"] else None,
            "distinct_goals": row["distinct_goals"],
            "rows": row["rows"],
            "meta_sec_per_prot_at_max": (
                round(META_SHIFT_SECONDS / float(row["goal_max"]), 2)
                if row["goal_max"]
                else None
            ),
        }
        for row in goals
    ]
    inconsistent = [e for e in per_etapa if e["distinct_goals"] > 1]
    return {
        "per_etapa": per_etapa,
        "inconsistent_etapas": inconsistent,
        "has_inconsistent_goals": len(inconsistent) > 0,
    }


def _audit_monitor_link(matricula: str, qs: QuerySet) -> dict[str, Any]:
    mat = matricula.strip().lower()
    bounds = qs.aggregate(min_d=Min("recorded_at"), max_d=Max("recorded_at"))
    min_d = bounds["min_d"]
    max_d = bounds["max_d"]
    if not min_d or not max_d:
        return {"linked": False, "reason": "sem_registros_produtividade"}

    start = min_d.date() if hasattr(min_d, "date") else min_d
    end = max_d.date() if hasattr(max_d, "date") else max_d

    monitor_exact = MonitorEventoRecord.objects.filter(
        matricula_usuario__iexact=mat,
        data__gte=start,
        data__lte=end,
    ).count()

    similar = list(
        MonitorEventoRecord.objects.filter(data__gte=start, data__lte=end)
        .annotate(mat_lower=Lower("matricula_usuario"))
        .filter(mat_lower__icontains=mat[:6] if len(mat) >= 6 else mat)
        .values_list("matricula_usuario", flat=True)
        .distinct()[:10]
    )

    logado_lookup = build_logado_lookup_for_qs(qs)
    analyzed_lookup = build_analyzed_daily_lookup_for_qs(qs)
    monitor_idle_lookup = build_monitor_ociosidade_lookup_for_qs(qs)
    net_idle_lookup = build_net_ociosidade_lookup_for_qs(qs)
    ociosidade_lookup = build_ociosidade_lookup_for_qs(qs)
    hourly = build_monitor_hourly_logado_lookup(qs)
    daily = build_monitor_daily_logado_lookup(qs)

    logado_total = sum(v for (m, _d), v in logado_lookup.items() if m == mat)
    analyzed_total = sum(v for (m, _d), v in analyzed_lookup.items() if m == mat)
    display_idle = max(0, logado_total - analyzed_total)
    monitor_idle_sum = sum(v for (m, _d), v in monitor_idle_lookup.items() if m == mat)
    net_idle_sum = sum(v for (m, _d), v in net_idle_lookup.items() if m == mat)
    abatement_idle_sum = sum(v for (m, _d), v in ociosidade_lookup.items() if m == mat)

    goal_totals = _agent_stage_goal_totals(qs).get(mat, {})
    goal_raw = float(goal_totals.get("goal_raw") or 0)
    goal_adj = float(goal_totals.get("goal_adjusted") or 0)
    effective_idle_ratio = (
        round(1.0 - goal_adj / goal_raw, 4) if goal_raw > 0 else None
    )
    idle_mismatch_seconds = monitor_idle_sum - display_idle

    days: set[date] = set()
    for (m, d) in logado_lookup:
        if m == mat:
            days.add(d)
    for (m, d) in analyzed_lookup:
        if m == mat:
            days.add(d)

    idle_by_day: list[dict[str, Any]] = []
    for d in sorted(days):
        logado_dia = int(logado_lookup.get((mat, d), 0) or 0)
        analyzed_dia = int(analyzed_lookup.get((mat, d), 0) or 0)
        monitor_idle_dia = int(monitor_idle_lookup.get((mat, d), 0) or 0)
        net_idle_dia = int(net_idle_lookup.get((mat, d), 0) or 0)
        idle_by_day.append(
            {
                "date": d.isoformat(),
                "logado_seconds": logado_dia,
                "logado_hms": _format_hms(logado_dia) if logado_dia else None,
                "analyzed_seconds": analyzed_dia,
                "analyzed_hms": _format_hms(analyzed_dia) if analyzed_dia else None,
                "display_idle_seconds": max(0, logado_dia - analyzed_dia),
                "monitor_idle_seconds": monitor_idle_dia,
                "net_idle_seconds": net_idle_dia,
                "idle_mismatch_seconds": monitor_idle_dia - max(0, logado_dia - analyzed_dia),
            }
        )

    agent_hourly = {h: s for (m, _d, h), s in hourly.items() if m == mat}
    agent_daily = {d.isoformat(): s for (m, d), s in daily.items() if m == mat}

    return {
        "matricula_produtividade": mat,
        "monitor_events_exact_match": monitor_exact,
        "monitor_matriculas_similar": similar,
        "logado_total_seconds": logado_total,
        "logado_total_hms": _format_hms(logado_total) if logado_total else None,
        "analyzed_total_seconds": analyzed_total,
        "analyzed_total_hms": _format_hms(analyzed_total) if analyzed_total else None,
        "display_idle_seconds": display_idle,
        "display_idle_hms": _format_hms(display_idle) if display_idle else None,
        "monitor_idle_sum_seconds": monitor_idle_sum,
        "monitor_idle_sum_hms": _format_hms(monitor_idle_sum) if monitor_idle_sum else None,
        "net_idle_sum_seconds": net_idle_sum,
        "abatement_idle_sum_seconds": abatement_idle_sum,
        "idle_mismatch_seconds": idle_mismatch_seconds,
        "effective_idle_ratio": effective_idle_ratio,
        "expected_idle_ratio_from_display": (
            round(
                min(display_idle, jornada_base_seconds(logado_total))
                / float(jornada_base_seconds(logado_total)),
                4,
            )
            if display_idle > 0
            else 0.0
        ),
        "goal_raw_total": round(goal_raw, 2) if goal_raw else None,
        "goal_adjusted_total": round(goal_adj, 2) if goal_adj else None,
        "idle_by_day": idle_by_day,
        "hourly_logado": {str(h): s for h, s in sorted(agent_hourly.items())},
        "daily_logado": agent_daily,
        "ociosidade_by_day": {
            f"{m}:{d.isoformat()}": s
            for (m, d), s in ociosidade_lookup.items()
            if m == mat
        },
        "linked": logado_total > 0 or monitor_exact > 0,
    }


def validate_agent_metrics(
    matricula: str,
    *,
    day: date | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Monta relatório de reconciliação para um agente."""
    mat = matricula.strip().lower()
    qs = _filter_agent_qs(mat, day=day, start_date=start_date, end_date=end_date)
    record_count = qs.count()

    if record_count == 0:
        return {
            "matricula": mat,
            "record_count": 0,
            "error": "Nenhum registro de produtividade no recorte.",
        }

    shift_groups = _shift_group_rows(qs)
    sums = _agent_period_pct_count(qs)
    normalized = _agent_count_normalized(qs)
    tma = _agent_tma_maps(qs).get(mat, {})
    impact_total = _agent_etapa_impact_totals(qs).get(mat, 0)
    impact_etapas = _impact_by_etapa(qs, mat)
    pace = build_pace_for_agent(mat, qs, include_by_day=True)

    sample_records = []
    ociosidade_lookup = build_ociosidade_lookup_for_qs(qs)
    discount_lookup = build_productivity_discount_lookup_for_qs(qs)
    logado_lookup = build_logado_lookup_for_qs(qs)
    for record in qs[:5]:
        payload = serialize_record(
            record, ociosidade_lookup, discount_lookup, logado_lookup
        )
        sample_records.append(
            {
                "recorded_at": payload.get("recorded_at"),
                "etapa": payload.get("etapa"),
                "count": payload.get("analysis_count"),
                "seconds": payload.get("analysis_seconds"),
                "stage_goal": payload.get("stage_goal"),
                "stage_goal_adjusted": payload.get("stage_goal_adjusted"),
                "meta_sec_per_prot": payload.get("meta_sec_per_prot"),
                "agent_sec_per_prot": payload.get("agent_sec_per_prot"),
            }
        )

    group_count = len(shift_groups)
    soma_pct = sums.get(mat)
    media_norm = normalized.get(mat)
    raw_pct, adj_pct, abatement_pct, abatement_pcd, abatement_idle = (
        _agent_productivity_breakdown_maps(qs)
    )
    goal_totals = _agent_stage_goal_totals(qs).get(mat, {})

    return {
        "matricula": mat,
        "record_count": record_count,
        "period": {
            "day": day.isoformat() if day else None,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
        },
        "shift_groups": shift_groups,
        "totals": {
            "productivity_pct_sum": soma_pct,
            "productivity_pct_sum_raw": raw_pct.get(mat),
            "productivity_pct_abatement": abatement_pct.get(mat),
            "productivity_pct_abatement_pcd": abatement_pcd.get(mat),
            "productivity_pct_abatement_idle": abatement_idle.get(mat),
            "productivity_pct_normalized": media_norm,
            "stage_goal_raw_total": round(goal_totals.get("goal_raw", 0), 2),
            "stage_goal_adjusted_total": round(goal_totals.get("goal_adjusted", 0), 2),
            "shift_group_count": group_count,
            "tma_actual_seconds": tma.get("tma_actual_seconds"),
            "tma_charged_seconds": tma.get("tma_charged_seconds"),
            "tma_actual_hms": tma.get("tma_actual_hms"),
            "tma_charged_hms": tma.get("tma_charged_hms"),
            "tma_ratio_pct": tma.get("tma_ratio_pct"),
            "impact_time_seconds": impact_total,
            "impact_time_hms": _format_hms(impact_total),
        },
        "impact_by_etapa": impact_etapas,
        "pace": public_pace_fields(pace, include_by_day=True),
        "stage_goal_audit": _audit_stage_goals(qs),
        "monitor_audit": _audit_monitor_link(mat, qs),
        "sample_records": sample_records,
        "reconciliation_notes": _reconciliation_notes(
            soma_pct, media_norm, tma.get("tma_ratio_pct"), pace.get("pace_status")
        ),
    }


def _reconciliation_notes(
    soma_pct: float | None,
    media_norm: float | None,
    tma_ratio_pct: float | None,
    pace_status: str | None,
) -> list[str]:
    notes: list[str] = []
    if soma_pct is not None and media_norm is not None:
        notes.append(
            f"Soma % produção ({soma_pct}%) pode diferir da média normalizada ({media_norm}%) "
            "quando há várias etapas/dias — meta 85% usa a média."
        )
    if tma_ratio_pct is not None:
        if tma_ratio_pct < 100:
            notes.append(
                f"TMA {tma_ratio_pct}% < 100%: tempo cobrado menor que o analisado "
                "(qntd × meta sec/prot) — ritmo mais lento que a meta de tempo."
            )
        elif tma_ratio_pct >= 100:
            notes.append(
                f"TMA {tma_ratio_pct}% ≥ 100%: tempo cobrado cobre ou supera o tempo analisado."
            )
    if pace_status == "unknown":
        notes.append(
            "Ritmo 'Sem dados': sem tempo logado no monitor ou hora extra na escala "
            "para calcular forecast acumulado."
        )
    return notes


def format_validation_report(report: dict[str, Any]) -> str:
    """Renderiza relatório em texto legível."""
    if report.get("error"):
        return f"ERRO: {report['error']}"

    lines = [
        f"=== Validação produtividade: {report['matricula']} ===",
        f"Registros: {report['record_count']}",
        "",
        "--- Totais ---",
    ]
    totals = report.get("totals") or {}
    for key, val in totals.items():
        lines.append(f"  {key}: {val}")

    lines.extend(["", "--- Grupos (agente, dia, etapa) ---"])
    for g in report.get("shift_groups") or []:
        etapa_label = g.get("etapa") or ""
        if len(etapa_label) > 50:
            etapa_label = etapa_label[:50] + "…"
        lines.append(f"  {g['date']} | {etapa_label}")
        lines.append(
            f"    goal {g['goal_raw']}->{g['goal_adjusted']} | "
            f"count {g['count']} | sec {g['seconds_hms']} | "
            f"pct {g['pct_count_adjusted']}% | "
            f"meta {g['meta_sec_per_prot']} s/prot | charged {g['charged_hms']}"
        )

    lines.extend(["", "--- Impacto por etapa ---"])
    for row in report.get("impact_by_etapa") or []:
        lines.append(
            f"  {row['etapa'][:60]}: impact {row['impact_time_hms']} "
            f"(gap {row['gap_sec_per_prot']} s/prot × {row['count']} prot)"
        )

    lines.extend(["", "--- Ritmo (pace) ---"])
    pace = report.get("pace") or {}
    for key in (
        "pace_actual_pct",
        "pace_expected_pct",
        "pace_delta_pp",
        "pace_status",
        "pace_days_count",
    ):
        if key in pace:
            lines.append(f"  {key}: {pace.get(key)}")

    lines.extend(["", "--- Auditoria stage_goal ---"])
    audit = report.get("stage_goal_audit") or {}
    if audit.get("has_inconsistent_goals"):
        lines.append("  ATENÇÃO: metas distintas na mesma etapa no período.")
    for row in audit.get("per_etapa") or []:
        lines.append(
            f"  {row['etapa'][:50]}: goal {row['goal_min']}–{row['goal_max']} "
            f"({row['distinct_goals']} distintas) -> meta ~{row['meta_sec_per_prot_at_max']} s/prot"
        )

    lines.extend(["", "--- Monitor / ociosidade ---"])
    mon = report.get("monitor_audit") or {}
    for key in (
        "logado_total_seconds",
        "analyzed_total_seconds",
        "display_idle_seconds",
        "monitor_idle_sum_seconds",
        "net_idle_sum_seconds",
        "abatement_idle_sum_seconds",
        "idle_mismatch_seconds",
        "effective_idle_ratio",
        "expected_idle_ratio_from_display",
        "goal_raw_total",
        "goal_adjusted_total",
        "ociosidade_by_day",
        "linked",
    ):
        if key in mon:
            lines.append(f"  {key}: {mon.get(key)}")
    for row in mon.get("idle_by_day") or []:
        lines.append(
            f"  {row['date']}: logado {row.get('logado_hms')} | analisado {row.get('analyzed_hms')} | "
            f"exibido {row.get('display_idle_seconds')}s | monitor {row.get('monitor_idle_seconds')}s | "
            f"líquido {row.get('net_idle_seconds')}s"
        )
    if mon.get("hourly_logado"):
        lines.append(f"  hourly_logado: {mon['hourly_logado']}")

    lines.extend(["", "--- Notas ---"])
    for note in report.get("reconciliation_notes") or []:
        lines.append(f"  - {note}")

    return "\n".join(lines)


def validation_report_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2, default=str)
