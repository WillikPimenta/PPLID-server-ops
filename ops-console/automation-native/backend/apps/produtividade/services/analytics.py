# -*- coding: utf-8 -*-
"""
Métricas de produtividade HxH.

Produtividade principal (volume):
- productivity_pct_count por grupo: analysis_count / stage_goal * 100
- Agente: SOMA dos % count por (agente, dia, etapa)
- Agente normalizado (vs meta diária): soma / qtd_grupos
- Dashboard KPI: média das normalizadas; pct_sum_avg = média das somas

Métricas operacionais (ritmo / sec/prot):
- meta_sec_per_prot, agent_sec_per_prot, impact_time_seconds
- productivity_pct (taxa): meta_sec_per_prot / agent_sec_per_prot * 100 — secundário
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from django.db.models import Count, Max, Min, Q, QuerySet, Sum
from django.utils import timezone

from apps.monitor_eventos.services.tabela_monitor import jornada_from_recorded_at
from apps.produtividade.models import ProductivityRecord
from apps.produtividade.services.goal_adjustment import (
    adjust_stage_goal,
    adjust_stage_goal_full,
    build_logado_lookup_for_qs,
    build_ociosidade_lookup_for_qs,
    build_productivity_discount_lookup_for_qs,
    discount_for_agent_day,
    ociosidade_for_goal_adjustment,
)
from apps.produtividade.services.hourly_forecast import (
    build_hourly_threshold_map_for_qs,
    build_schedule_context_lookup,
    threshold_for_agent_hour,
)
from apps.produtividade.services.criticality import (
    classify_criticality,
    criticality_methodology_payload,
)
from apps.produtividade.services.meta_context import (
    META_MISTA,
    agent_meta_map,
    context_meta,
    resolve_meta_from_teams,
    teams_by_agent,
)
from apps.produtividade.services.operational_status import (
    classify_operational_status,
    is_priority_negative,
    operational_priority_rank,
)
from apps.produtividade.services.pace_tracking import (
    PACE_DAILY_TARGET,
    build_monitor_hourly_logado_lookup,
    build_pace_for_agent,
    build_pace_map_for_qs,
    public_pace_fields,
)
from apps.produtividade.services.semaphore import meta_performance_tone

_SHIFT_GROUPS_RAW_CACHE = "_pplid_shift_groups_raw"
_SHIFT_GROUPS_PCD_CACHE = "_pplid_shift_groups_pcd"
_SHIFT_GROUPS_ADJ_CACHE = "_pplid_shift_groups_adj"
_HOURLY_PCT_SUMS_CACHE = "_pplid_hourly_pct_sums"

# Default legado = meta mista (95%). Preferir meta_context por escopo filtrado.
DAILY_THRESHOLD = META_MISTA
HOURLY_THRESHOLD = round(META_MISTA / 5.5, 2)  # ~17.27
META_SHIFT_SECONDS = 19800  # 5h30 por (matricula, dia, etapa)

# Criticidade absoluta (v1). Códigos novos; aliases legados mantidos para imports de teste.
SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_EXPECTED = "expected"
SEVERITY_UNCLASSIFIABLE = "unclassifiable"
# Aliases legados (mapear gradualmente no FE)
SEVERITY_MODERATE = "high"
SEVERITY_LIGHT = "medium"
SEVERITY_OK = "expected"

IDLE_METHODOLOGY_VERSION = "v2-net-logged"

CLUSTER_TOP = "top"
CLUSTER_MID = "mid"
CLUSTER_CRITICAL = "critical"

CAUSE_MIN_AGENTS = 3
CAUSE_MIN_PROTOCOLS = 10
CAUSE_ENV_PCT = 60.0
CAUSE_EXEC_PCT = 25.0
CAUSE_GAP_DELTA_SPP = 2.0

CAUSE_AMBIENTE = "ambiente"
CAUSE_EXECUCAO = "execucao"
CAUSE_MISTO = "misto"
CAUSE_INCONCLUSIVO = "inconclusivo"

CAUSE_LABELS = {
    CAUSE_AMBIENTE: "Ambiente",
    CAUSE_EXECUCAO: "Execução",
    CAUSE_MISTO: "Misto",
    CAUSE_INCONCLUSIVO: "—",
}


def _agent_eval(agent_metas: dict, matricula: str, ctx_meta):
    return agent_metas.get(matricula) or ctx_meta


def _agent_threshold(agent_metas: dict, matricula: str, ctx_meta) -> float | None:
    """Meta efetiva do agente; None quando operação não identificada (sem fallback 95%)."""
    return _agent_eval(agent_metas, matricula, ctx_meta).applied_meta


def _meta_payload(meta, realized_pct: float | None = None) -> dict:
    return {"meta_evaluation": meta.to_dict(realized_pct)}


def _severity(pct: float | None, threshold: float = DAILY_THRESHOLD) -> str:
    """Faixa de criticidade absoluta (não usa meta). ``threshold`` ignorado (compat)."""
    _ = threshold
    return classify_criticality(pct)


def _agent_below_daily(
    volume_pct: float | None,
    threshold: float | None = DAILY_THRESHOLD,
) -> bool:
    """Abaixo da meta = produção total com abatimento (barra) < meta diária, não a média por etapa."""
    if volume_pct is None or threshold is None:
        return False
    return volume_pct < threshold


def _row_below_operational(row: dict) -> bool:
    """Preferir limiar operacional; fallback ao diário absoluto (legado/cache)."""
    if row.get("below_operational_threshold") is not None:
        return bool(row["below_operational_threshold"])
    return bool(row.get("below_daily_threshold"))


def _gap_pp_to_threshold(
    volume_pct: float | None,
    threshold: float | None = DAILY_THRESHOLD,
) -> float | None:
    if volume_pct is None or threshold is None:
        return None
    return round(threshold - volume_pct, 2)


def _leader_idle_logged_fields(rows: list[dict], logado_lookup: dict) -> dict:
    """Média de tempo logado (colaborador-dia) e mediana de ociosidade por líder."""
    mats = {
        str(r.get("matricula") or "").strip().lower()
        for r in rows
        if r.get("matricula")
    }
    logged_sum = 0
    logged_days = 0
    for (mat, _day), seconds in logado_lookup.items():
        if mat not in mats:
            continue
        sec = int(seconds or 0)
        if sec <= 0:
            continue
        logged_sum += sec
        logged_days += 1

    idle_sum = sum(int(r.get("tempo_ocioso_seconds") or 0) for r in rows)
    idle_values = [
        int(r["tempo_ocioso_seconds"])
        for r in rows
        if r.get("tempo_ocioso_seconds") is not None
    ]
    idle_median = (
        int(round(statistics.median(idle_values))) if idle_values else None
    )
    # % ponderada mantida no payload (compat); a coluna exibe a mediana em tempo.
    idle_pct = None
    if logged_sum > 0:
        idle_pct = round(min(100.0, max(0.0, (idle_sum / logged_sum) * 100.0)), 2)

    avg_logged = round(logged_sum / logged_days) if logged_days > 0 else None
    return {
        "avg_logged_seconds": avg_logged,
        "avg_logged_hms": _format_hms(avg_logged) if avg_logged is not None else None,
        "idle_seconds": idle_median,
        "idle_hms": _format_hms(idle_median) if idle_median is not None else None,
        "idle_pct": idle_pct,
        "logged_agent_days_count": logged_days,
        "idle_methodology_version": IDLE_METHODOLOGY_VERSION,
        # Soma legada para barras de abatimento; não usar como valor da coluna.
        "tempo_ocioso_seconds": idle_sum if logged_sum > 0 else None,
        "tempo_logado_seconds": logged_sum if logged_sum > 0 else None,
        "tempo_logado_hms": _format_hms(logged_sum) if logged_sum > 0 else None,
    }


def _format_hms(total_seconds: int | float) -> str:
    total = max(0, int(round(total_seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _protocols_per_hour(count: int | float, seconds: int | float) -> float | None:
    seconds = int(seconds or 0)
    count = float(count or 0)
    if seconds <= 0:
        return None
    return round((count / seconds) * 3600, 1)


def _sec_per_prot(seconds: int | float, count: int | float) -> float | None:
    count = int(count or 0)
    seconds = int(seconds or 0)
    if count <= 0 or seconds <= 0:
        return None
    return round(seconds / count, 2)


def _meta_sec_per_prot(total_goal: float, meta_seconds: int | None = None) -> float | None:
    if total_goal <= 0:
        return None
    budget = meta_seconds if meta_seconds is not None else META_SHIFT_SECONDS
    return round(budget / total_goal, 2)


def _compute_rate_metrics(
    total_count: int,
    total_seconds: int,
    total_goal: float,
    meta_seconds: int | None = None,
) -> dict:
    """Métricas agregadas de taxa (sec/prot + alias pph deprecado)."""
    shift_meta_seconds = meta_seconds if meta_seconds is not None else META_SHIFT_SECONDS
    agent_sec_per_prot = _sec_per_prot(total_seconds, total_count)
    meta_sec_per_prot = _meta_sec_per_prot(total_goal, shift_meta_seconds)
    agent_pph = _protocols_per_hour(total_count, total_seconds)
    meta_pph = _protocols_per_hour(total_goal, shift_meta_seconds)

    pct = None
    pct_count = None
    gap_sec_per_prot = None
    impact_pph = 0.0
    impact_time_seconds = 0
    surplus_time_seconds = 0

    if (
        agent_sec_per_prot is not None
        and agent_sec_per_prot > 0
        and meta_sec_per_prot is not None
        and meta_sec_per_prot > 0
    ):
        pct = round((meta_sec_per_prot / agent_sec_per_prot) * 100, 2)
        gap_sec_per_prot = round(agent_sec_per_prot - meta_sec_per_prot, 2)
        if gap_sec_per_prot > 0:
            impact_time_seconds = int(round(gap_sec_per_prot * total_count))
        elif gap_sec_per_prot < 0:
            surplus_time_seconds = int(round(abs(gap_sec_per_prot) * total_count))

    net_impact_time_seconds = int(impact_time_seconds) - int(surplus_time_seconds)

    if agent_pph is not None and meta_pph is not None and meta_pph > 0:
        impact_pph = round(max(0.0, meta_pph - agent_pph), 1)

    if total_goal > 0:
        pct_count = round((total_count / total_goal) * 100, 2)

    return {
        "agent_sec_per_prot": agent_sec_per_prot,
        "meta_sec_per_prot": meta_sec_per_prot,
        "gap_sec_per_prot": gap_sec_per_prot,
        "impact_time_seconds": impact_time_seconds,
        "impact_time_hms": _format_hms(impact_time_seconds),
        "surplus_time_seconds": surplus_time_seconds,
        "surplus_time_hms": _format_hms(surplus_time_seconds),
        "net_impact_time_seconds": net_impact_time_seconds,
        "net_impact_time_hms": _format_hms(abs(net_impact_time_seconds)),

        "agent_pph": agent_pph,
        "meta_pph": meta_pph,
        "productivity_pct": pct,
        "productivity_pct_count": pct_count,
        "impact_pph": impact_pph,
        "impact_protocols": round(impact_pph * (total_seconds / 3600), 1) if impact_pph > 0 and total_seconds > 0 else 0.0,
        "actual_seconds": total_seconds,
        "meta_seconds": shift_meta_seconds,
        "actual_hms": _format_hms(total_seconds),
        "meta_hms": _format_hms(shift_meta_seconds),
    }


def _format_signed_hms(total_seconds: int | float | None) -> str | None:
    if total_seconds is None:
        return None
    total = int(round(total_seconds))
    if total == 0:
        return "00:00:00"
    sign = "+" if total > 0 else "−"
    return f"{sign}{_format_hms(abs(total))}"


def _potential_protocols_at_meta_pace(
    count: int,
    seconds: int,
    goal: float,
    meta_seconds: int | None = None,
) -> float | None:
    """Protocolos esperados no tempo analisado (meta da etapa).

    potencial = analysis_seconds ÷ meta_sec_per_prot
              = analysis_seconds × stage_goal ÷ meta_seconds

    ``count`` não entra na fórmula (mantido na assinatura por compatibilidade).
    Potencial nunca é negativo; meta inválida → None.
    """
    fields = _potential_balance_fields(count, seconds, goal, meta_seconds)
    return fields.get("potential_protocols")


def _potential_balance_fields(
    count: int,
    seconds: int,
    goal: float,
    meta_seconds: int | None = None,
) -> dict:
    """Potencial = tempo ÷ meta s/prot; cobrado/saldo só para auditoria.

    charged_seconds = realizado × meta_sec_per_prot
    time_balance_seconds = charged − analysis_seconds
    potential_protocols = max(0, analysis_seconds ÷ meta_sec_per_prot)

    Relação: realizado − potencial = time_balance_seconds ÷ meta_sec_per_prot.
    """
    count = int(count or 0)
    seconds = int(seconds or 0)
    goal = float(goal or 0)
    empty = {
        "charged_seconds": None,
        "time_balance_seconds": None,
        "charged_hms": None,
        "time_balance_hms": None,
        "potential_protocols": None,
    }
    if goal <= 0:
        return empty
    meta_spp = _meta_sec_per_prot(goal, meta_seconds)
    if meta_spp is None or meta_spp <= 0:
        return empty
    charged = round(count * meta_spp, 4)
    balance = round(charged - seconds, 4)
    potential = round(max(0.0, seconds / meta_spp), 4)
    return {
        "charged_seconds": charged,
        "time_balance_seconds": balance,
        "charged_hms": _format_hms(charged),
        "time_balance_hms": _format_signed_hms(balance),
        "potential_protocols": potential,
    }


def _potential_display_fields(
    rates_pure: dict | None,
    rates_pcd: dict | None = None,
) -> dict:
    """Campos de potencial para UI: PcD como principal, puro no tooltip.

    Potencial = tempo ÷ meta s/prot. Ociosidade não entra.
    Sem desconto PcD, rates_pcd ≈ rates_pure (potencial PCD <= puro).
    """
    pure = rates_pure or {}
    pcd = rates_pcd or {}
    primary = pcd if pcd.get("potential_protocols") is not None else pure
    return {
        "potential_protocols": primary.get("potential_protocols"),
        "potential_protocols_pure": pure.get("potential_protocols"),
        "charged_seconds": primary.get("charged_seconds"),
        "time_balance_seconds": primary.get("time_balance_seconds"),
        "charged_hms": primary.get("charged_hms"),
        "time_balance_hms": primary.get("time_balance_hms"),
    }


def _shift_group_metrics(total_count: int, total_seconds: int, total_goal: float) -> dict:
    return _compute_rate_metrics(total_count, total_seconds, total_goal, META_SHIFT_SECONDS)


def _cached_ociosidade_lookup(qs: QuerySet) -> dict[tuple[str, date], int]:
    cache_attr = "_pplid_ociosidade_lookup"
    cached = getattr(qs, cache_attr, None)
    if cached is None:
        cached = build_ociosidade_lookup_for_qs(qs)
        setattr(qs, cache_attr, cached)
    return cached


def _cached_logado_lookup(qs: QuerySet) -> dict[tuple[str, date], int]:
    cache_attr = "_pplid_logado_lookup"
    cached = getattr(qs, cache_attr, None)
    if cached is None:
        cached = build_logado_lookup_for_qs(qs)
        setattr(qs, cache_attr, cached)
    return cached


def _cached_discount_lookup(qs: QuerySet) -> dict[tuple[str, date], float]:
    """Productivity Discount (horas) do headcount por agente/jornada."""
    cache_attr = "_pplid_productivity_discount_lookup"
    cached = getattr(qs, cache_attr, None)
    if cached is None:
        cached = build_productivity_discount_lookup_for_qs(qs)
        setattr(qs, cache_attr, cached)
    return cached


def _build_shift_groups_raw(qs: QuerySet) -> dict[tuple[str, date, str], dict]:
    groups: dict[tuple[str, date, str], dict] = defaultdict(
        lambda: {"count": 0, "seconds": 0, "goal": 0.0}
    )
    rows = qs.filter(stage_goal__gt=0).values(
        "matricula_norm",
        "etapa",
        "recorded_at",
        "analysis_seconds",
        "analysis_count",
        "stage_goal",
    )
    for row in rows:
        recorded_at = row["recorded_at"]
        matricula = row["matricula_norm"]
        if recorded_at is None or not matricula:
            continue
        day = recorded_at.date() if hasattr(recorded_at, "date") else recorded_at
        key = (matricula, day, row["etapa"] or "")
        bucket = groups[key]
        bucket["count"] += row["analysis_count"] or 0
        bucket["seconds"] += row["analysis_seconds"] or 0
        goal_val = float(row["stage_goal"] or 0)
        if goal_val > bucket["goal"]:
            bucket["goal"] = goal_val
    return dict(groups)


def _shift_group_jornada_map(
    qs: QuerySet,
) -> dict[tuple[str, date, str], date]:
    """Jornada (data operacional) por grupo (matricula, dia civil, etapa)."""
    key_jornada: dict[tuple[str, date, str], date] = {}
    key_goal: dict[tuple[str, date, str], float] = {}
    for row in qs.filter(stage_goal__gt=0).values(
        "matricula_norm", "recorded_at", "etapa", "stage_goal"
    ):
        recorded_at = row["recorded_at"]
        matricula = row["matricula_norm"]
        if recorded_at is None or not matricula:
            continue
        day = recorded_at.date() if hasattr(recorded_at, "date") else recorded_at
        key = (matricula, day, row["etapa"] or "")
        goal_val = float(row["stage_goal"] or 0)
        jornada = jornada_from_recorded_at(recorded_at)
        if jornada is None:
            continue
        if key not in key_jornada or goal_val >= key_goal.get(key, -1.0):
            key_jornada[key] = jornada
            key_goal[key] = goal_val
    return key_jornada


def _shift_groups_pcd_only(
    qs: QuerySet,
    raw: dict[tuple[str, date, str], dict],
) -> dict[tuple[str, date, str], dict]:
    """Grupos com meta só após desconto PCD (sem ociosidade)."""
    discount_lookup = _cached_discount_lookup(qs)
    key_jornada = _shift_group_jornada_map(qs)
    adjusted: dict[tuple[str, date, str], dict] = {}
    for key, bucket in raw.items():
        matricula, day, _etapa = key
        jornada = key_jornada.get(key, day)
        adjusted[key] = {
            "count": bucket["count"],
            "seconds": bucket["seconds"],
            "goal": adjust_stage_goal(
                bucket["goal"],
                discount_for_agent_day(discount_lookup, matricula, jornada),
            ),
        }
    return adjusted


def _shift_groups_adjusted(
    qs: QuerySet,
    raw: dict[tuple[str, date, str], dict],
) -> dict[tuple[str, date, str], dict]:
    discount_lookup = _cached_discount_lookup(qs)
    ociosidade_lookup = _cached_ociosidade_lookup(qs)
    logado_lookup = _cached_logado_lookup(qs)
    key_jornada = _shift_group_jornada_map(qs)

    adjusted: dict[tuple[str, date, str], dict] = {}
    for key, bucket in raw.items():
        matricula, day, etapa = key
        jornada = key_jornada.get(key, day)
        mat_key = (matricula or "").strip().lower()
        adjusted[key] = {
            "count": bucket["count"],
            "seconds": bucket["seconds"],
            "goal": adjust_stage_goal_full(
                bucket["goal"],
                discount_for_agent_day(discount_lookup, matricula, jornada),
                ociosidade_for_goal_adjustment(ociosidade_lookup, matricula, jornada),
                logado_seconds=logado_lookup.get((mat_key, jornada), 0),
            ),
        }
    return adjusted


def _iter_shift_groups(
    qs: QuerySet,
    *,
    adjust_goal: bool = True,
    pcd_only: bool = False,
) -> dict[tuple[str, date, str], dict]:
    raw = getattr(qs, _SHIFT_GROUPS_RAW_CACHE, None)
    if raw is None:
        raw = _build_shift_groups_raw(qs)
        setattr(qs, _SHIFT_GROUPS_RAW_CACHE, raw)
    if not adjust_goal:
        return raw
    if pcd_only:
        pcd = getattr(qs, _SHIFT_GROUPS_PCD_CACHE, None)
        if pcd is None:
            pcd = _shift_groups_pcd_only(qs, raw)
            setattr(qs, _SHIFT_GROUPS_PCD_CACHE, pcd)
        return pcd
    adjusted = getattr(qs, _SHIFT_GROUPS_ADJ_CACHE, None)
    if adjusted is None:
        adjusted = _shift_groups_adjusted(qs, raw)
        setattr(qs, _SHIFT_GROUPS_ADJ_CACHE, adjusted)
    return adjusted


def _rate_gap(
    qs: QuerySet,
    groups: dict[tuple[str, date, str], dict] | None = None,
) -> dict:
    if groups is None:
        groups = _iter_shift_groups(qs, adjust_goal=False)
    total_count = 0
    total_seconds = 0
    total_goal = 0.0
    meta_seconds = 0
    impact_bruto = 0
    surplus_bruto = 0
    for totals in groups.values():
        total_count += totals["count"]
        total_seconds += totals["seconds"]
        total_goal += totals["goal"]
        meta_seconds += META_SHIFT_SECONDS
        # Soma por (agente, dia, etapa): não deixa etapa rápida anular lento no blend.
        group_metrics = _compute_rate_metrics(
            totals["count"],
            totals["seconds"],
            totals["goal"],
            META_SHIFT_SECONDS,
        )
        impact_bruto += int(group_metrics.get("impact_time_seconds") or 0)
        surplus_bruto += int(group_metrics.get("surplus_time_seconds") or 0)

    metrics = _compute_rate_metrics(total_count, total_seconds, total_goal, meta_seconds)
    net = impact_bruto - surplus_bruto
    return {
        **metrics,
        "impact_time_seconds": impact_bruto,
        "impact_time_hms": _format_hms(impact_bruto),
        "surplus_time_seconds": surplus_bruto,
        "surplus_time_hms": _format_hms(surplus_bruto),
        "net_impact_time_seconds": net,
        "net_impact_time_hms": _format_hms(abs(net)),
        "gap_pph": metrics["impact_pph"],
    }


def _time_gap(qs: QuerySet, rate_gap_data: dict | None = None) -> dict:
    """Deprecated alias — maps rate_gap to legacy time_gap shape."""
    gap = rate_gap_data if rate_gap_data is not None else _rate_gap(qs)
    return {
        "expected_seconds": gap["meta_seconds"],
        "actual_seconds": gap["actual_seconds"],
        "gap_seconds": max(0, gap["meta_seconds"] - gap["actual_seconds"]),
        "expected_hms": gap["meta_hms"],
        "actual_hms": gap["actual_hms"],
        "gap_hms": _format_hms(max(0, gap["meta_seconds"] - gap["actual_seconds"])),
    }


def _volume_gap(qs: QuerySet, rate_gap_data: dict | None = None) -> dict:
    """Deprecated alias for legacy attainment.volume_gap."""
    gap = rate_gap_data if rate_gap_data is not None else _rate_gap(qs)
    return {
        "expected": gap["meta_pph"] or 0,
        "actual": gap["agent_pph"] or 0,
        "gap": gap["gap_pph"],
    }


def _impact_pph(meta_pph: float | None, agent_pph: float | None) -> float:
    if meta_pph is None or agent_pph is None:
        return 0.0
    return round(max(0.0, meta_pph - agent_pph), 1)


def _impact_seconds(gap_pp: float, expected_seconds: float) -> float:
    """Deprecated alias — returns gap_pp for backward compatibility."""
    if gap_pp <= 0:
        return 0.0
    return round(gap_pp, 2)


def _impact_score(gap_pp: float, expected_volume: float) -> float:
    """Deprecated alias."""
    return _impact_seconds(gap_pp, expected_volume)


def _last_activity_map(qs: QuerySet) -> dict[str, datetime]:
    result: dict[str, datetime] = {}
    for row in qs.values("matricula_norm").annotate(last_at=Max("recorded_at")):
        matricula = row["matricula_norm"]
        if matricula and row["last_at"]:
            result[matricula] = row["last_at"]
    return result


def _agent_rate_maps(
    qs: QuerySet,
    *,
    adjust_goal: bool = True,
    pcd_only: bool = False,
) -> dict[str, dict]:
    per_agent: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "seconds": 0,
            "goal": 0.0,
            "meta_seconds": 0,
            "potential_protocols": None,
            "charged_seconds": None,
            "time_balance_seconds": None,
            "_has_potential": False,
        }
    )
    for (matricula, _day, _etapa), totals in _iter_shift_groups(
        qs, adjust_goal=adjust_goal, pcd_only=pcd_only
    ).items():
        bucket = per_agent[matricula]
        count = int(totals["count"] or 0)
        seconds = int(totals["seconds"] or 0)
        goal = float(totals["goal"] or 0)
        bucket["count"] += count
        bucket["seconds"] += seconds
        bucket["goal"] += goal
        bucket["meta_seconds"] += META_SHIFT_SECONDS
        # Soma dos potenciais por (dia, etapa); metas distintas não usam média.
        bal = _potential_balance_fields(count, seconds, goal, META_SHIFT_SECONDS)
        if bal["potential_protocols"] is not None:
            if not bucket["_has_potential"]:
                bucket["potential_protocols"] = 0.0
                bucket["charged_seconds"] = 0.0
                bucket["time_balance_seconds"] = 0.0
                bucket["_has_potential"] = True
            bucket["potential_protocols"] += float(bal["potential_protocols"])
            bucket["charged_seconds"] += float(bal["charged_seconds"] or 0)
            bucket["time_balance_seconds"] += float(bal["time_balance_seconds"] or 0)

    result: dict[str, dict] = {}
    for matricula, totals in per_agent.items():
        metrics = _compute_rate_metrics(
            totals["count"],
            totals["seconds"],
            totals["goal"],
            totals["meta_seconds"],
        )
        protocols_count = int(totals["count"] or 0)
        has_potential = bool(totals["_has_potential"])
        potential = (
            round(float(totals["potential_protocols"] or 0), 4) if has_potential else None
        )
        charged = (
            round(float(totals["charged_seconds"] or 0), 4) if has_potential else None
        )
        balance = (
            round(float(totals["time_balance_seconds"] or 0), 4) if has_potential else None
        )
        result[matricula] = {
            **metrics,
            "gap_pph": metrics["impact_pph"],
            "protocols_count": protocols_count,
            "potential_protocols": potential,
            "charged_seconds": charged,
            "time_balance_seconds": balance,
            "charged_hms": _format_hms(charged) if charged is not None else None,
            "time_balance_hms": (
                _format_signed_hms(balance) if balance is not None else None
            ),
        }
    return result


def _agent_tma_maps(qs: QuerySet, *, adjust_goal: bool = False) -> dict[str, dict]:
    """TMA: tempo cobrado / tempo analisado (meta bruta stage_goal, sem abatimento)."""
    per_agent: dict[str, dict] = defaultdict(lambda: {"actual": 0, "charged": 0.0})
    for (matricula, _day, _etapa), totals in _iter_shift_groups(qs, adjust_goal=adjust_goal).items():
        goal = float(totals["goal"] or 0)
        count = int(totals["count"] or 0)
        seconds = int(totals["seconds"] or 0)
        if goal <= 0 or count <= 0:
            continue
        meta_spp = META_SHIFT_SECONDS / goal
        bucket = per_agent[matricula]
        bucket["actual"] += seconds
        bucket["charged"] += count * meta_spp

    result: dict[str, dict] = {}
    for matricula, bucket in per_agent.items():
        actual = int(bucket["actual"])
        charged = float(bucket["charged"])
        charged_int = int(round(charged))
        ratio = None
        ratio_pct = None
        if charged > 0 and actual > 0:
            ratio = round(charged / actual, 4)
            ratio_pct = round(ratio * 100, 2)
        result[matricula] = {
            "tma_actual_seconds": actual,
            "tma_charged_seconds": charged_int,
            "tma_actual_hms": _format_hms(actual),
            "tma_charged_hms": _format_hms(charged_int),
            "tma_ratio": ratio,
            "tma_ratio_pct": ratio_pct,
        }
    return result


def _agent_etapa_impact_detail(
    qs: QuerySet,
) -> tuple[dict[str, int], dict[str, str], dict[str, int]]:
    """
    Por agente: soma do tempo a recuperar (mais lento) e do tempo adiantado (mais rápido)
    por etapa — líquido = impacto − superávit.
    """
    per_agent_etapa: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "seconds": 0, "goal": 0.0, "meta_seconds": 0}
    )
    for (matricula, _day, etapa), totals in _iter_shift_groups(qs, adjust_goal=False).items():
        if not matricula:
            continue
        bucket = per_agent_etapa[(matricula, etapa or "")]
        bucket["count"] += totals["count"]
        bucket["seconds"] += totals["seconds"]
        bucket["goal"] += totals["goal"]
        bucket["meta_seconds"] += META_SHIFT_SECONDS

    impact_by_agent: dict[str, int] = defaultdict(int)
    surplus_by_agent: dict[str, int] = defaultdict(int)
    best_etapa: dict[str, tuple[str, int]] = {}
    for (matricula, etapa), t in per_agent_etapa.items():
        metrics = _compute_rate_metrics(t["count"], t["seconds"], t["goal"], t["meta_seconds"])
        impact = int(metrics.get("impact_time_seconds") or 0)
        surplus = int(metrics.get("surplus_time_seconds") or 0)
        impact_by_agent[matricula] += impact
        surplus_by_agent[matricula] += surplus
        prev = best_etapa.get(matricula)
        if prev is None or impact > prev[1]:
            best_etapa[matricula] = (etapa or "", impact)
    top_etapa = {mat: etapa for mat, (etapa, impact) in best_etapa.items() if impact > 0 and etapa}
    return dict(impact_by_agent), top_etapa, dict(surplus_by_agent)


def _agent_etapa_impact_totals(qs: QuerySet) -> dict[str, int]:
    """Soma impact_time_seconds por etapa (ritmo puro / meta bruta)."""
    totals, _, _ = _agent_etapa_impact_detail(qs)
    return totals


def _liquid_impact_fields(impact_seconds: int, surplus_seconds: int) -> dict:
    impact = int(impact_seconds or 0)
    surplus = int(surplus_seconds or 0)
    net = impact - surplus
    return {
        "impact_time_seconds": impact,
        "impact_time_hms": _format_hms(impact),
        "surplus_time_seconds": surplus,
        "surplus_time_hms": _format_hms(surplus),
        "net_impact_time_seconds": net,
        "net_impact_time_hms": _format_hms(abs(net)),
    }


def _pcd_impact_fields(metrics_pcd: dict | None) -> dict:
    """Impacto/surplus/net a partir de métricas com meta PCD (sem ociosidade)."""
    m = metrics_pcd or {}
    impact = int(m.get("impact_time_seconds") or 0)
    surplus = int(m.get("surplus_time_seconds") or 0)
    net = int(m.get("net_impact_time_seconds") or (impact - surplus))
    return {
        "impact_time_seconds_pcd": impact,
        "impact_time_hms_pcd": _format_hms(impact),
        "surplus_time_seconds_pcd": surplus,
        "surplus_time_hms_pcd": _format_hms(surplus),
        "net_impact_time_seconds_pcd": net,
        "net_impact_time_hms_pcd": _format_hms(abs(net)),
    }


def _cut_off_label(last_activity: dict[str, datetime] | None) -> str:
    if last_activity:
        latest = max(last_activity.values())
        return timezone.localtime(latest).strftime("%H:%M")
    return timezone.localtime(timezone.now()).strftime("%H:%M")


def _team_intraday_from_pace(
    pace_map: dict[str, dict],
    agent_volume: dict[str, float],
    last_activity: dict[str, datetime] | None = None,
) -> dict:
    """Agrega pace do time: atingimento intraday e projeção de fechamento."""
    actuals: list[float] = []
    expecteds: list[float] = []
    for pace in pace_map.values():
        actual = pace.get("pace_actual_pct")
        expected = pace.get("pace_expected_pct")
        if actual is not None and expected is not None:
            actuals.append(float(actual))
            expecteds.append(float(expected))

    pace_actual = round(sum(actuals) / len(actuals), 2) if actuals else None
    pace_expected = round(sum(expecteds) / len(expecteds), 2) if expecteds else None

    atingimento = None
    projected = None
    projection_insufficient = True
    if pace_actual is not None and pace_expected is not None and pace_expected > 0:
        atingimento = round((pace_actual / pace_expected) * 100, 1)
        projected = round((pace_actual / pace_expected) * PACE_DAILY_TARGET, 1)
        projection_insufficient = False

    attention: set[str] = set()
    for matricula, volume_pct in agent_volume.items():
        status = (pace_map.get(matricula) or {}).get("pace_status")
        if status in ("near", "behind") or _agent_below_daily(volume_pct):
            attention.add(matricula)
    for matricula, pace in pace_map.items():
        if pace.get("pace_status") in ("near", "behind"):
            attention.add(matricula)

    return {
        "pace_actual_pct": pace_actual,
        "pace_expected_pct": pace_expected,
        "atingimento_intraday_pct": atingimento,
        "projected_closing_pct": projected,
        "projection_insufficient": projection_insufficient,
        "cut_off_label": _cut_off_label(last_activity),
        "agents_attention_count": len(attention),
    }


def _priority_cause_action(
    *,
    tempo_ocioso_seconds: int | None,
    impact_time_seconds: int,
    gap_sec_per_prot: float | None,
    pace_status: str | None,
    top_etapa: str | None,
) -> dict[str, str | None]:
    idle = int(tempo_ocioso_seconds or 0)
    impact = int(impact_time_seconds or 0)
    gap = float(gap_sec_per_prot or 0)

    if idle > 0 and (impact <= 0 or idle >= impact * 0.5):
        return {
            "primary_cause": "Ociosidade impactante",
            "suggested_action": "Validar disponibilidade e demanda nas últimas horas",
        }
    if top_etapa and gap > 0:
        return {
            "primary_cause": f"Ritmo na etapa {top_etapa}",
            "suggested_action": f"Revisar tratamento da etapa {top_etapa}",
        }
    if gap > 0:
        return {
            "primary_cause": "Ritmo acima da meta",
            "suggested_action": "Priorizar etapas com maior tempo a recuperar",
        }
    if pace_status in ("near", "behind"):
        return {
            "primary_cause": "Abaixo do esperado até agora",
            "suggested_action": "Acelerar produção nas próximas horas elegíveis",
        }
    return {
        "primary_cause": "Abaixo da meta diária",
        "suggested_action": "Abrir detalhe e priorizar etapas críticas",
    }


def agent_projected_closing_pct(pace: dict | None, applied_meta: float | None = None) -> float | None:
    """Projeção de fechamento: (actual/expected) * meta contextual.

    No aquecimento / sem dados, retorna null (UI: “Aguardando mais dados”).
    """
    if not pace:
        return None
    if pace.get("operational_status") == "warming_up":
        return None
    if pace.get("projected_closing_pct") is not None:
        return pace.get("projected_closing_pct")
    actual = pace.get("pace_actual_pct")
    expected = pace.get("pace_expected_pct")
    if actual is None or expected is None or float(expected) <= 0:
        return None
    meta = applied_meta
    if meta is None:
        meta = pace.get("applied_meta")
    if meta is None:
        meta = PACE_DAILY_TARGET  # legado somente se meta ausente no snapshot antigo
    return round((float(actual) / float(expected)) * float(meta), 1)


def _enrich_with_operational_status(
    *,
    applied_meta: float | None,
    pace_fields: dict,
    tempo_logado_seconds: int | None,
    productivity_pct: float | None = None,
    shift_closed: bool = False,
    data_delayed: bool = False,
    consecutive_critical_windows: int = 0,
    evaluated_at=None,
) -> dict:
    """Anexa contrato operacional ao payload do agente (não altera ritmo/TMA).

    Não reutiliza ``pace_expected_pct`` do forecast horário (meta diária 95% fixa);
    o contrato v2 recalcula o esperado com a meta contextual Fraud/Mista/Compliance.
    """
    result = classify_operational_status(
        applied_meta=applied_meta,
        pace_actual_pct=(
            pace_fields.get("pace_actual_pct")
            if pace_fields.get("pace_actual_pct") is not None
            else productivity_pct
        ),
        # Força recálculo com meta contextual (não usar expected do pace_tracking).
        pace_expected_pct=None,
        productive_logged_seconds=tempo_logado_seconds,
        shift_closed=shift_closed,
        final_actual_pct=productivity_pct if shift_closed else None,
        data_delayed=data_delayed,
        consecutive_critical_windows=consecutive_critical_windows,
        evaluated_at=evaluated_at,
    )
    payload = result.as_dict()
    if payload.get("pace_attainment_pct") is not None:
        payload["atingimento_intraday_pct"] = round(float(payload["pace_attainment_pct"]), 1)
    # Compat: mapear status operacional → pace_status legado quando útil.
    op = result.operational_status
    if op in ("on_track", "ahead"):
        payload["pace_status"] = "ok"
    elif op == "attention":
        payload["pace_status"] = "near"
    elif op in ("behind", "critical", "final_below", "final_critical"):
        payload["pace_status"] = "behind"
    elif op in ("warming_up", "not_started", "data_delayed"):
        payload["pace_status"] = "unknown"
    elif op == "unknown":
        payload["pace_status"] = pace_fields.get("pace_status") or "unknown"
    return payload


def _group_pct_sums(
    qs: QuerySet,
    field: str,
    *,
    adjust_goal: bool = True,
    pcd_only: bool = False,
) -> dict[str, float]:
    """Soma dos % por grupo (agente+dia+etapa) no período, por matricula."""
    per_agent: dict[str, float] = defaultdict(float)
    for (matricula, _day, _etapa), totals in _iter_shift_groups(
        qs, adjust_goal=adjust_goal, pcd_only=pcd_only
    ).items():
        metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        value = metrics.get(field)
        if value is not None:
            per_agent[matricula] += value
    return {matricula: round(total, 2) for matricula, total in per_agent.items()}


def _abatement_split_maps(
    raw: dict[str, float],
    pcd: dict[str, float],
    adjusted: dict[str, float],
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    """Abatimento total, PCD e ociosidade (p.p.) a partir de bruto / só PCD / final."""
    abatement: dict[str, float] = {}
    abatement_pcd: dict[str, float] = {}
    abatement_idle: dict[str, float] = {}
    for matricula in set(raw) | set(pcd) | set(adjusted):
        raw_v = float(raw.get(matricula, 0.0) or 0.0)
        pcd_v = float(pcd.get(matricula, raw_v) or raw_v)
        adj_v = float(adjusted.get(matricula, pcd_v) or pcd_v)
        pcd_pp = round(max(0.0, pcd_v - raw_v), 2)
        idle_pp = round(max(0.0, adj_v - pcd_v), 2)
        abatement_pcd[matricula] = pcd_pp
        abatement_idle[matricula] = idle_pp
        abatement[matricula] = round(pcd_pp + idle_pp, 2)
    return abatement, abatement_pcd, abatement_idle


def _agent_productivity_breakdown_maps(
    qs: QuerySet,
) -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
]:
    """% bruto, ajustado, abatimento total, abatimento PCD e ociosidade (p.p.)."""
    adjusted = _agent_period_pct_count(qs)
    raw = _group_pct_sums(qs, "productivity_pct_count", adjust_goal=False)
    pcd = _group_pct_sums(qs, "productivity_pct_count", adjust_goal=True, pcd_only=True)
    abatement, abatement_pcd, abatement_idle = _abatement_split_maps(raw, pcd, adjusted)
    return raw, adjusted, abatement, abatement_pcd, abatement_idle


def _productivity_abatement_payload(
    matricula: str,
    abatement_map: dict[str, float],
    abatement_pcd_map: dict[str, float],
    abatement_idle_map: dict[str, float],
) -> dict:
    return {
        "productivity_pct_abatement": abatement_map.get(matricula, 0),
        "productivity_pct_abatement_pcd": abatement_pcd_map.get(matricula, 0),
        "productivity_pct_abatement_idle": abatement_idle_map.get(matricula, 0),
    }


def _agent_stage_goal_totals(qs: QuerySet) -> dict[str, dict[str, float]]:
    """Meta bruta vs ajustada (soma por agente no período)."""
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"goal_raw": 0.0, "goal_adjusted": 0.0}
    )
    for (matricula, _day, _etapa), bucket in _iter_shift_groups(qs, adjust_goal=False).items():
        totals[matricula]["goal_raw"] += bucket["goal"]
    for (matricula, _day, _etapa), bucket in _iter_shift_groups(qs, adjust_goal=True).items():
        totals[matricula]["goal_adjusted"] += bucket["goal"]
    return {matricula: dict(values) for matricula, values in totals.items()}


def _agent_period_pct(qs: QuerySet) -> dict[str, float]:
    """% taxa sec/prot do período: soma dos % por (agente, dia, etapa)."""
    return _group_pct_sums(qs, "productivity_pct")


def _overall_avg_agent_period_pct(qs: QuerySet) -> float | None:
    """Deprecated para KPI — use _overall_avg_agent_count_normalized."""
    return _overall_avg_agent_count_normalized(qs)


def _agent_period_pct_count(qs: QuerySet) -> dict[str, float]:
    """% count do período: soma dos % por (agente, dia, etapa)."""
    return _group_pct_sums(qs, "productivity_pct_count")


def _agent_group_counts(qs: QuerySet) -> dict[str, int]:
    """Quantidade de grupos (agente, dia, etapa) por matricula."""
    counts: dict[str, int] = defaultdict(int)
    for (matricula, _day, _etapa), totals in _iter_shift_groups(qs).items():
        metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        if metrics.get("productivity_pct_count") is not None:
            counts[matricula] += 1
    return dict(counts)


def _agent_count_normalized(qs: QuerySet) -> dict[str, float]:
    """Média count % por etapa/dia: soma_count_pct / qtd_grupos."""
    sums = _agent_period_pct_count(qs)
    group_counts = _agent_group_counts(qs)
    result: dict[str, float] = {}
    for matricula, total in sums.items():
        n = group_counts.get(matricula, 0)
        if n > 0:
            result[matricula] = round(total / n, 2)
    return result


def _overall_avg_agent_count_sums(qs: QuerySet) -> float | None:
    sums = _agent_period_pct_count(qs)
    if not sums:
        return None
    return round(sum(sums.values()) / len(sums), 2)


def _overall_avg_agent_count_normalized(qs: QuerySet) -> float | None:
    normalized = _agent_count_normalized(qs)
    if not normalized:
        return None
    return round(sum(normalized.values()) / len(normalized), 2)


def _agent_time_maps(qs: QuerySet) -> tuple[dict[str, int], dict[str, int]]:
    """Deprecated alias — returns meta/actual seconds per agent."""
    rates = _agent_rate_maps(qs)
    expected = {m: v["meta_seconds"] for m, v in rates.items()}
    actual = {m: v["actual_seconds"] for m, v in rates.items()}
    return expected, actual


def _agent_volume_maps(qs: QuerySet) -> tuple[dict[str, float], dict[str, float]]:
    """Deprecated alias — returns meta/agent pph as floats."""
    rates = _agent_rate_maps(qs)
    return (
        {m: float(v["meta_pph"] or 0) for m, v in rates.items()},
        {m: float(v["agent_pph"] or 0) for m, v in rates.items()},
    )


def _percentile_rank(value: float, population: list[float]) -> float | None:
    if not population:
        return None
    below = sum(1 for v in population if v < value)
    return round((below / len(population)) * 100, 1)


def _cluster(pct: float, mean: float, threshold: float | None = DAILY_THRESHOLD) -> str:
    if threshold is not None and pct >= threshold:
        return CLUSTER_TOP
    if pct < mean * 0.7 or pct < 50:
        return CLUSTER_CRITICAL
    return CLUSTER_MID


def _compute_trend(hourly_overview: list[dict]) -> dict:
    valid = [row for row in hourly_overview if row.get("productivity_pct") is not None]
    if len(valid) < 4:
        return {"direction": "flat", "delta_pp": 0.0, "label": "dados insuficientes para tendência"}
    recent = valid[-2:]
    previous = valid[-4:-2]
    recent_avg = sum(r["productivity_pct"] for r in recent) / len(recent)
    prev_avg = sum(r["productivity_pct"] for r in previous) / len(previous)
    delta = round(recent_avg - prev_avg, 2)
    if delta > 1:
        direction = "up"
        label = f"melhorando nas últimas 2h ({delta:+.1f} p.p.)"
    elif delta < -1:
        direction = "down"
        label = f"piorando nas últimas 2h ({delta:+.1f} p.p.)"
    else:
        direction = "flat"
        label = "estável nas últimas 2h"
    return {"direction": direction, "delta_pp": delta, "label": label}


def _compute_intraday_series(hourly_overview: list[dict]) -> list[dict]:
    series = []
    window: list[float] = []
    for row in hourly_overview:
        pct = row.get("productivity_pct")
        if pct is not None:
            window.append(pct)
            if len(window) > 3:
                window = window[-3:]
            rolling = round(sum(window) / len(window), 2)
        else:
            rolling = None
        series.append(
            {
                "hour": row["hour"],
                "productivity_pct": pct,
                "rolling_avg_3h": rolling,
                "hourly_forecast_pct": row.get("hourly_forecast_pct"),
                "below_threshold": row.get("below_threshold", False),
            }
        )
    return series


# Eixo fixo do heatmap: 0h → 23h (dia civil).
HEATMAP_CLOCK_HOURS: tuple[int, ...] = tuple(range(24))


def _civil_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Início inclusivo e fim exclusivo do dia civil no fuso local."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(day, time.min), tz)
    end_excl = timezone.make_aware(
        datetime.combine(day + timedelta(days=1), time.min), tz
    )
    return start, end_excl


def _to_local_date(value: datetime | None) -> date | None:
    if value is None:
        return None
    if timezone.is_aware(value):
        return timezone.localtime(value).date()
    return value.date()


def _civil_slot_in_filter(qs: QuerySet, data_jornada: date, hour: int) -> bool:
    """Mantém só horas cujo instante civil cai no intervalo do filtro de datas."""
    civil_start = getattr(qs, "_pplid_civil_start", None)
    civil_end = getattr(qs, "_pplid_civil_end", None)
    if civil_start is None and civil_end is None:
        return True
    slot_day = _civil_datetime_from_jornada_hour(data_jornada, hour).date()
    if civil_start is not None and slot_day < civil_start:
        return False
    if civil_end is not None and slot_day > civil_end:
        return False
    return True


def _hour_to_clock(hour) -> int | None:
    """Extrai a hora do relógio (0–23) no fuso local (madrugada SP, não UTC)."""
    if hour is None:
        return None
    if isinstance(hour, datetime):
        local = timezone.localtime(hour) if timezone.is_aware(hour) else hour
        try:
            return int(local.hour) % 24
        except (TypeError, ValueError):
            return None
    if hasattr(hour, "hour") and not isinstance(hour, str):
        # time / datetime-like sem ser datetime (ex.: pandas)
        try:
            return int(hour.hour) % 24
        except (TypeError, ValueError):
            return None
    if isinstance(hour, int):
        return hour % 24
    s = str(hour).strip()
    if "T" in s:
        try:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if timezone.is_aware(parsed):
                parsed = timezone.localtime(parsed)
            return int(parsed.hour) % 24
        except (TypeError, ValueError, IndexError):
            try:
                return int(s.split("T", 1)[1][:2]) % 24
            except (TypeError, ValueError, IndexError):
                return None
    if ":" in s:
        try:
            return int(s.split(":", 1)[0]) % 24
        except (TypeError, ValueError):
            return None
    try:
        return int(s) % 24
    except (TypeError, ValueError):
        return None


def _avg_or_none(vals: list[float]) -> float | None:
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


def _heatmap_row_values(
    per_agent_hours: dict[int, list[float]],
) -> list[float | None]:
    return [_avg_or_none(per_agent_hours.get(clock, [])) for clock in HEATMAP_CLOCK_HOURS]


def _staircase_sort_key(values: list[float | None], volume: float) -> tuple:
    """Ordena em escada: quem produz mais cedo no eixo 0h→23h sobe; volume desempata."""
    first_idx = next((i for i, v in enumerate(values) if v is not None), len(values))
    # Dentro do mesmo início, maior volume primeiro (bloco mais “cheio” no topo da escada).
    return (first_idx, -float(volume or 0.0))


def _compute_hourly_heatmap(
    hourly_sums: dict[tuple[str, object], float],
    names: dict[str, str],
    agent_avgs: dict[str, float],
    threshold_map: dict[tuple[str, object], float | None] | None = None,
    max_agents: int | None = None,
) -> dict:
    """Heatmap agente × hora com eixo fixo 0h–23h (dia civil).

    Inclui todos os agentes do filtro, ordenados em escada pela 1ª hora com produção.
    ``max_agents`` só limita se passado explicitamente (testes / uso pontual).
    """
    per_agent: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    per_agent_thr: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))

    for (matricula, hour), total in hourly_sums.items():
        clock = _hour_to_clock(hour)
        if clock is None:
            continue
        per_agent[matricula][clock].append(float(total))
        if threshold_map is not None:
            thr = threshold_map.get((matricula, hour))
            if thr is not None:
                per_agent_thr[matricula][clock].append(float(thr))

    hour_labels = [f"{h:02d}:00" for h in HEATMAP_CLOCK_HOURS]

    agents = set(agent_avgs.keys()) | set(per_agent.keys())
    prepared: list[tuple[str, list[float | None], list[float | None] | None, float]] = []
    for matricula in agents:
        values = _heatmap_row_values(per_agent.get(matricula, {}))
        thresholds = None
        if threshold_map is not None:
            thresholds = [
                _avg_or_none(per_agent_thr.get(matricula, {}).get(clock, []))
                for clock in HEATMAP_CLOCK_HOURS
            ]
        volume = float(agent_avgs.get(matricula, 0.0) or 0.0)
        prepared.append((matricula, values, thresholds, volume))

    prepared.sort(key=lambda item: _staircase_sort_key(item[1], item[3]))
    if max_agents is not None:
        prepared = prepared[: max(0, int(max_agents))]

    rows = []
    selected: list[str] = []
    for matricula, values, thresholds, volume in prepared:
        selected.append(matricula)
        hour_sum = round(sum(v for v in values if v is not None), 2)
        # Preferir soma das células; se vazia, cai no volume do período.
        total = hour_sum if any(v is not None for v in values) else round(float(volume or 0.0), 2)
        row_payload: dict = {
            "matricula": matricula,
            "nome": names.get(matricula, matricula),
            "total": total,
            "values": values,
        }
        if thresholds is not None:
            row_payload["thresholds"] = thresholds
        rows.append(row_payload)

    hour_avgs: list[tuple[str, float | None]] = []
    for idx, clock in enumerate(HEATMAP_CLOCK_HOURS):
        vals = [
            _avg_or_none(per_agent[m].get(clock, []))
            for m in selected
            if m in per_agent
        ]
        vals = [v for v in vals if v is not None]
        hour_avgs.append((hour_labels[idx], _avg_or_none(vals)))

    insights: dict = {
        "worst_hour": None,
        "worst_hour_avg_pct": None,
        "best_hour": None,
        "best_hour_avg_pct": None,
        "peak_drop_from_prev_hour": None,
    }
    valid_hours = [(h, a) for h, a in hour_avgs if a is not None]
    if valid_hours:
        worst = min(valid_hours, key=lambda x: x[1])
        best = max(valid_hours, key=lambda x: x[1])
        insights["worst_hour"] = worst[0]
        insights["worst_hour_avg_pct"] = worst[1]
        insights["best_hour"] = best[0]
        insights["best_hour_avg_pct"] = best[1]
        if len(valid_hours) >= 2:
            max_drop = 0.0
            drop_hour = None
            for i in range(1, len(valid_hours)):
                drop = valid_hours[i - 1][1] - valid_hours[i][1]
                if drop > max_drop:
                    max_drop = drop
                    drop_hour = valid_hours[i][0]
            if drop_hour:
                insights["peak_drop_from_prev_hour"] = {
                    "hour": drop_hour,
                    "drop_pp": round(max_drop, 2),
                }

    return {"hours": hour_labels, "rows": rows, "insights": insights}


def _build_attainment(
    qs: QuerySet,
    hourly_overview: list[dict],
    *,
    agent_normalized: dict[str, float] | None = None,
    agent_sums: dict[str, float] | None = None,
    rate_gap_data: dict | None = None,
    pace_map: dict[str, dict] | None = None,
    last_activity: dict[str, datetime] | None = None,
    ctx_meta=None,
    agent_metas: dict | None = None,
) -> dict:
    if agent_normalized is None:
        agent_normalized = _agent_count_normalized(qs)
    if agent_sums is None:
        agent_sums = _agent_period_pct_count(qs)
    if ctx_meta is None:
        ctx_meta = context_meta(qs)
    if agent_metas is None:
        agent_metas = agent_meta_map(qs)
    ctx_threshold = ctx_meta.applied_meta
    avg_pct = (
        round(sum(agent_normalized.values()) / len(agent_normalized), 2)
        if agent_normalized
        else None
    )
    avg_sum = round(sum(agent_sums.values()) / len(agent_sums), 2) if agent_sums else None
    monitored = len(agent_sums) if agent_sums else len(agent_normalized)
    below_count = sum(
        1
        for mat, v in agent_sums.items()
        if _agent_below_daily(v, _agent_threshold(agent_metas, mat, ctx_meta))
    )
    below_pct = round((below_count / monitored) * 100, 1) if monitored else 0.0
    delta_pp = (
        round((avg_sum or 0) - ctx_threshold, 2)
        if avg_sum is not None and ctx_threshold is not None
        else None
    )
    if rate_gap_data is None:
        rate_gap_data = _rate_gap(qs)
    if pace_map is None:
        pace_map = build_pace_map_for_qs(qs)
    if last_activity is None:
        last_activity = _last_activity_map(qs)
    intraday = _team_intraday_from_pace(pace_map, agent_sums, last_activity)
    return {
        "pct": avg_pct,
        "pct_sum_avg": avg_sum,
        "target": ctx_meta.applied_meta,
        "delta_pp": delta_pp,
        "agents_below_pct": below_pct,
        "agents_below_count": below_count,
        "agents_monitored": monitored,
        "time_gap": _time_gap(qs, rate_gap_data=rate_gap_data),
        "rate_gap": rate_gap_data,
        "volume_gap": _volume_gap(qs, rate_gap_data=rate_gap_data),
        "trend": _compute_trend(hourly_overview),
        **_meta_payload(ctx_meta, avg_sum),
        **intraday,
    }


def _build_priority_agents(
    qs: QuerySet,
    agent_normalized: dict[str, float],
    agent_sums: dict[str, float],
    names: dict[str, str],
    teams: dict[str, str],
    leaders: dict[str, str],
    last_activity: dict[str, datetime],
    rate_map: dict[str, dict],
    impact_totals: dict[str, int],
    logado_totals: dict[str, int],
    top_n: int | None = None,
    productivity_breakdown: tuple[
        dict[str, float],
        dict[str, float],
        dict[str, float],
        dict[str, float],
        dict[str, float],
    ]
    | None = None,
    goal_totals: dict[str, dict[str, float]] | None = None,
    pace_map: dict[str, dict] | None = None,
    tma_map: dict[str, dict] | None = None,
    top_etapa_map: dict[str, str] | None = None,
    surplus_totals: dict[str, int] | None = None,
    ctx_meta=None,
    agent_metas: dict | None = None,
    rate_map_pcd: dict[str, dict] | None = None,
) -> list[dict]:
    if productivity_breakdown is None:
        raw_pct_map, _, abatement_map, abatement_pcd_map, abatement_idle_map = (
            _agent_productivity_breakdown_maps(qs)
        )
    else:
        raw_pct_map, _, abatement_map, abatement_pcd_map, abatement_idle_map = (
            productivity_breakdown
        )
    if goal_totals is None:
        goal_totals = _agent_stage_goal_totals(qs)
    if pace_map is None:
        pace_map = build_pace_map_for_qs(qs)
    if tma_map is None:
        tma_map = _agent_tma_maps(qs)
    if top_etapa_map is None or surplus_totals is None:
        _, detail_top, detail_surplus = _agent_etapa_impact_detail(qs)
        if top_etapa_map is None:
            top_etapa_map = detail_top
        if surplus_totals is None:
            surplus_totals = detail_surplus
    if rate_map_pcd is None:
        rate_map_pcd = _agent_rate_maps(qs, adjust_goal=True, pcd_only=True)
    surplus_from_detail = surplus_totals or {}
    if ctx_meta is None:
        ctx_meta = context_meta(qs)
    if agent_metas is None:
        agent_metas = agent_meta_map(qs)
    agents = []
    for matricula in sorted(set(agent_sums) | set(agent_normalized)):
        pct_sum = agent_sums.get(matricula)
        productivity_norm = agent_normalized.get(matricula)
        rates = rate_map.get(matricula, {})
        rates_pcd = rate_map_pcd.get(matricula, {})
        impact_seconds = impact_totals.get(matricula, 0)
        surplus_seconds = surplus_from_detail.get(matricula, 0)
        liquid = _liquid_impact_fields(impact_seconds, surplus_seconds)
        agent_eval = _agent_eval(agent_metas, matricula, ctx_meta)
        thr = agent_eval.applied_meta
        gap_pp = _gap_pp_to_threshold(pct_sum, thr)
        last_at = last_activity.get(matricula)
        goals = goal_totals.get(matricula, {})
        monitor_fields = _monitor_time_fields(
            matricula,
            int(rates.get("actual_seconds") or 0),
            logado_totals,
        )
        pace_fields = public_pace_fields(pace_map.get(matricula, {}))
        op_fields = _enrich_with_operational_status(
            applied_meta=thr,
            pace_fields=pace_fields,
            tempo_logado_seconds=monitor_fields.get("tempo_logado_seconds"),
            productivity_pct=pct_sum,
        )
        # Preferir pace_status derivado do operacional quando classificado.
        if op_fields.get("pace_status"):
            pace_fields = {**pace_fields, "pace_status": op_fields["pace_status"]}
        cause_action = _priority_cause_action(
            tempo_ocioso_seconds=monitor_fields.get("tempo_ocioso_seconds"),
            impact_time_seconds=impact_seconds,
            gap_sec_per_prot=rates.get("gap_sec_per_prot"),
            pace_status=pace_fields.get("pace_status"),
            top_etapa=top_etapa_map.get(matricula),
        )
        # warming_up / data_delayed / not_started: nunca “abaixo” operacional.
        # unknown (ex.: sem tempo logado): None → contadores caem no below_daily.
        op_status = op_fields.get("operational_status")
        if op_status in ("warming_up", "data_delayed", "not_started"):
            below_operational = False
        elif op_status == "unknown" or op_fields.get("status_phase") in (
            "unavailable",
            None,
        ):
            below_operational = None
        else:
            below_operational = is_priority_negative(op_status)
        agents.append(
            {
                "matricula": matricula,
                "nome": names.get(matricula, matricula),
                "team": teams.get(matricula, ""),
                "leader_name": leaders.get(matricula, "") or None,
                "productivity_pct": productivity_norm if productivity_norm is not None else pct_sum,
                "productivity_pct_sum": pct_sum,
                "productivity_pct_count": pct_sum,
                "productivity_pct_count_raw": raw_pct_map.get(matricula),
                **_productivity_abatement_payload(
                    matricula, abatement_map, abatement_pcd_map, abatement_idle_map
                ),
                "stage_goal_raw_total": round(goals.get("goal_raw", 0), 2) if goals else None,
                "stage_goal_adjusted_total": round(goals.get("goal_adjusted", 0), 2) if goals else None,
                "productivity_pct_normalized": productivity_norm,
                "gap_pp": gap_pp,
                "applied_meta": agent_eval.applied_meta,
                "evaluation_type": agent_eval.evaluation_type,
                # Depreciado: comparação absoluta com meta final; preferir operational_status.
                "below_daily_threshold": _agent_below_daily(pct_sum, thr),
                "below_operational_threshold": below_operational,
                **_meta_payload(agent_eval, pct_sum),
                **{k: rates.get(k) for k in (
                    "agent_sec_per_prot", "meta_sec_per_prot", "gap_sec_per_prot",
                    "agent_pph", "meta_pph", "impact_pph", "impact_protocols",
                    "actual_seconds", "meta_seconds", "actual_hms", "meta_hms",
                    "protocols_count",
                )},
                **_potential_display_fields(rates, rates_pcd),
                **monitor_fields,
                **liquid,
                "expected_seconds": rates.get("meta_seconds", 0),
                "expected_hms": rates.get("meta_hms", "00:00:00"),
                "time_gap_seconds": max(0, (rates.get("meta_seconds") or 0) - (rates.get("actual_seconds") or 0)),
                "impact_seconds": liquid["net_impact_time_seconds"],
                "impact_hms": liquid["net_impact_time_hms"],
                "impact_score": liquid["net_impact_time_seconds"],
                "expected_volume": rates.get("meta_pph"),
                "actual_volume": rates.get("agent_pph"),
                "severity": _severity(pct_sum, thr),
                "last_activity_at": last_at.isoformat() if last_at else None,
                **pace_fields,
                **op_fields,
                "projected_closing_pct": agent_projected_closing_pct(
                    {**pace_fields, **op_fields}, thr
                ),
                **cause_action,
                **(tma_map.get(matricula) or {}),
            }
        )
    # Prioriza status operacional negativo; warming_up nunca no topo como crítico.
    agents.sort(
        key=lambda x: (
            operational_priority_rank(x.get("operational_status")),
            -(x.get("net_impact_time_seconds") or 0),
        )
    )
    if top_n:
        return agents[: int(top_n)]
    return agents


def productivity_pct_count(
    analysis_seconds: int,
    stage_goal,
    analysis_count: int = 0,
) -> float | None:
    if stage_goal is None:
        return None
    goal = float(stage_goal)
    if goal <= 0:
        return None
    if analysis_count > 0:
        return round((analysis_count / goal) * 100, 2)
    if analysis_seconds > 0:
        return round((analysis_seconds / goal) * 100, 2)
    return 0.0


def productivity_pct_time(
    analysis_seconds: int,
    stage_goal,
    analysis_count: int = 0,
) -> float | None:
    if stage_goal is None:
        return None
    goal = float(stage_goal)
    if goal <= 0:
        return None
    agent_spp = _sec_per_prot(analysis_seconds, analysis_count)
    meta_spp = _meta_sec_per_prot(goal)
    if agent_spp is None or meta_spp is None or agent_spp <= 0:
        return None if analysis_seconds <= 0 else 0.0
    return round((meta_spp / agent_spp) * 100, 2)


def productivity_pct(
    analysis_seconds: int,
    stage_goal,
    analysis_count: int = 0,
) -> float | None:
    """% principal baseado em protocolos/hora."""
    return productivity_pct_time(analysis_seconds, stage_goal, analysis_count)


def _aggregate_productivity_pct(
    total_seconds: int,
    total_goal: float,
    total_count: int,
    shift_group_count: int = 1,
) -> float | None:
    if total_goal <= 0:
        return None
    meta_seconds = META_SHIFT_SECONDS * max(1, shift_group_count)
    metrics = _compute_rate_metrics(total_count, total_seconds, total_goal, meta_seconds)
    return metrics.get("productivity_pct")


def _agent_daily_pct_sums(qs: QuerySet) -> dict[tuple[str, date], float]:
    """Soma do % taxa por grupo (agente+dia+etapa) naquele dia."""
    per_day: dict[tuple[str, date], float] = defaultdict(float)
    for (matricula, day, _etapa), totals in _iter_shift_groups(qs).items():
        metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        if metrics["productivity_pct"] is not None:
            per_day[(matricula, day)] += metrics["productivity_pct"]
    return {key: round(total, 2) for key, total in per_day.items()}


def _agent_daily_pct_count_sums(qs: QuerySet) -> dict[tuple[str, date], float]:
    """Soma do % count por grupo (agente+dia+etapa) naquele dia."""
    per_day: dict[tuple[str, date], float] = defaultdict(float)
    for (matricula, day, _etapa), totals in _iter_shift_groups(qs).items():
        metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        if metrics["productivity_pct_count"] is not None:
            per_day[(matricula, day)] += metrics["productivity_pct_count"]
    return {key: round(total, 2) for key, total in per_day.items()}


def _agent_avg_daily_pct_from_sums(daily_sums: dict[tuple[str, date], float]) -> dict[str, float]:
    per_agent: dict[str, list[float]] = defaultdict(list)
    for (matricula, _), day_total in daily_sums.items():
        per_agent[matricula].append(day_total)
    return {
        matricula: round(sum(values) / len(values), 2)
        for matricula, values in per_agent.items()
        if values
    }


def _agent_avg_daily_pct(qs: QuerySet) -> dict[str, float]:
    """Por agente: média das médias diárias de % taxa."""
    return _agent_avg_daily_pct_from_sums(_agent_daily_pct_sums(qs))


def _agent_avg_daily_pct_count(qs: QuerySet) -> dict[str, float]:
    """Por agente: média das médias diárias de % count."""
    return _agent_avg_daily_pct_from_sums(_agent_daily_pct_count_sums(qs))


def _overall_avg_agent_daily_pct(qs: QuerySet) -> float | None:
    """Deprecated — use _overall_avg_agent_period_pct."""
    return _overall_avg_agent_period_pct(qs)


def _compute_hourly_pct_sums(qs: QuerySet) -> dict[tuple[str, object], float]:
    """Soma o % count de cada registro por agente e hora (heatmap/alertas horários)."""
    sums: dict[tuple[str, object], float] = defaultdict(float)
    discount_lookup = _cached_discount_lookup(qs)
    ociosidade_lookup = _cached_ociosidade_lookup(qs)
    logado_lookup = _cached_logado_lookup(qs)
    rows = qs.filter(stage_goal__gt=0).values(
        "matricula_norm",
        "analysis_seconds",
        "analysis_count",
        "stage_goal",
        "recorded_at",
        "etapa",
        "source",
    )
    for row in rows:
        recorded_at = row["recorded_at"]
        if recorded_at is None:
            continue
        day = recorded_at.date() if hasattr(recorded_at, "date") else recorded_at
        jornada = jornada_from_recorded_at(recorded_at) or day
        mat_key = (row["matricula_norm"] or "").strip().lower()
        adjusted_goal = adjust_stage_goal_full(
            float(row["stage_goal"] or 0),
            discount_for_agent_day(discount_lookup, row["matricula_norm"], jornada),
            ociosidade_for_goal_adjustment(
                ociosidade_lookup,
                row["matricula_norm"],
                jornada,
            ),
            logado_seconds=logado_lookup.get((mat_key, jornada), 0),
        )
        pct = productivity_pct_count(
            row["analysis_seconds"] or 0,
            adjusted_goal,
            row["analysis_count"] or 0,
        )
        if pct is None:
            continue
        hour = recorded_at.replace(minute=0, second=0, microsecond=0)
        sums[(row["matricula_norm"], hour)] += pct
    return dict(sums)


def _agent_hourly_pct_sums(qs: QuerySet) -> dict[tuple[str, object], float]:
    cached = getattr(qs, _HOURLY_PCT_SUMS_CACHE, None)
    if cached is None:
        cached = _compute_hourly_pct_sums(qs)
        setattr(qs, _HOURLY_PCT_SUMS_CACHE, cached)
    return cached


def _agent_monitor_logado_totals(qs: QuerySet) -> dict[str, int]:
    """Soma tempo_logado_dia por matrícula no período do queryset."""
    lookup = build_logado_lookup_for_qs(qs)
    totals: dict[str, int] = defaultdict(int)
    for (matricula, _day), seconds in lookup.items():
        totals[matricula] += int(seconds or 0)
    return dict(totals)


def _monitor_time_fields(
    matricula: str,
    actual_seconds: int,
    logado_totals: dict[str, int],
) -> dict:
    logado = int(logado_totals.get(matricula, 0) or 0)
    analyzed = int(actual_seconds or 0)
    ocioso = max(0, logado - analyzed) if logado > 0 else 0
    return {
        "tempo_logado_seconds": logado if logado > 0 else None,
        "tempo_logado_hms": _format_hms(logado) if logado > 0 else None,
        "tempo_analisado_seconds": analyzed if analyzed > 0 else None,
        "tempo_analisado_hms": _format_hms(analyzed) if analyzed > 0 else None,
        "tempo_ocioso_seconds": ocioso if logado > 0 else None,
        "tempo_ocioso_hms": _format_hms(ocioso) if logado > 0 else None,
    }


def _agent_metadata_maps(qs: QuerySet) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    names: dict[str, str] = {}
    teams: dict[str, str] = {}
    leaders: dict[str, str] = {}
    for row in qs.order_by("matricula_norm", "-recorded_at").values(
        "matricula_norm", "agent_name", "team", "leader_name"
    ):
        matricula = row["matricula_norm"]
        if not matricula:
            continue
        if matricula not in names:
            names[matricula] = row["agent_name"] or matricula
        if matricula not in teams:
            teams[matricula] = row["team"] or ""
        if matricula not in leaders:
            leaders[matricula] = row["leader_name"] or ""
    return names, teams, leaders


def _agent_group_map(qs: QuerySet, field: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for row in qs.order_by("matricula_norm", "-recorded_at").values("matricula_norm", field):
        matricula = row["matricula_norm"]
        if matricula and matricula not in mapping:
            mapping[matricula] = row[field] or ""
    return mapping


def _parse_filter_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_str_list(value) -> list[str]:
    """Normaliza filtro único ou múltiplo (lista, getlist ou CSV)."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        out: list[str] = []
        for item in value:
            out.extend(_as_str_list(item))
        return out
    text = str(value).strip()
    if not text:
        return []
    if "," in text:
        return [part.strip() for part in text.split(",") if part.strip()]
    return [text]


def _query_multi(query_params, key: str) -> list[str] | None:
    if hasattr(query_params, "getlist"):
        raw_list = [v for v in query_params.getlist(key) if v not in (None, "")]
        if raw_list:
            values = _as_str_list(raw_list)
            return values or None
    values = _as_str_list(query_params.get(key))
    return values or None


def apply_record_filters(qs: QuerySet, params: dict) -> QuerySet:
    start_date = _parse_filter_date(params.get("start_date"))
    end_date = _parse_filter_date(params.get("end_date"))
    etapas = _as_str_list(params.get("etapa"))
    teams = _as_str_list(params.get("team"))
    locations = _as_str_list(params.get("location"))
    journey_shifts = _as_str_list(params.get("journey_shift"))
    matriculas = [m.strip().lower() for m in _as_str_list(params.get("matricula"))]
    leader_names = _as_str_list(params.get("leader_name"))
    below_goal = params.get("below_goal")
    severity = params.get("severity")
    active_within_hours = params.get("active_within_hours")

    # start_date/end_date = dia civil (00:00–23:59 local).
    # Dia D: 0h–23h de D (inclui manhã da madrugada e o início às 23h de D).
    # A 23h do dia anterior só entra quando o intervalo inclui D-1.
    if start_date:
        start_dt, _ = _civil_day_bounds(start_date)
        qs = qs.filter(recorded_at__gte=start_dt)
    if end_date:
        _, end_excl = _civil_day_bounds(end_date)
        qs = qs.filter(recorded_at__lt=end_excl)
    if etapas:
        qs = qs.filter(etapa__in=etapas)
    if teams:
        qs = qs.filter(team__in=teams)
    if locations:
        qs = qs.filter(location__in=locations)
    if journey_shifts:
        qs = qs.filter(journey_shift__in=journey_shifts)
    if matriculas:
        qs = qs.filter(matricula_norm__in=matriculas)
    if leader_names:
        leader_q = Q()
        for name in leader_names:
            leader_q |= Q(leader_name__iexact=name)
        qs = qs.filter(leader_q)

    meu_time_mats = params.get("_meu_time_mats")
    if meu_time_mats is not None:
        if meu_time_mats:
            qs = qs.filter(matricula_norm__in=meu_time_mats)
        else:
            qs = qs.none()

    if below_goal in (True, "true", "1", "yes"):
        agent_sums = _agent_period_pct_count(qs)
        matching = [m for m, p in agent_sums.items() if _agent_below_daily(p)]
        qs = qs.filter(matricula_norm__in=matching) if matching else qs.none()

    if severity:
        agent_sums = _agent_period_pct_count(qs)
        matching = [m for m, p in agent_sums.items() if _severity(p) == severity]
        qs = qs.filter(matricula_norm__in=matching) if matching else qs.none()

    if active_within_hours:
        try:
            hours = int(active_within_hours)
        except (TypeError, ValueError):
            hours = 2
        cutoff = timezone.now() - timedelta(hours=hours)
        last_activity = _last_activity_map(qs)
        matching = [m for m, dt in last_activity.items() if dt >= cutoff]
        qs = qs.filter(matricula_norm__in=matching) if matching else qs.none()

    # Bridge do monitor: carrega jornadas que cobrem o civil (0h–5h = jornada D-1).
    # Overview/heatmap logado ainda recortam pelo civil via _pplid_civil_*.
    # Cada .filter() clona o QuerySet e descarta atributos livres. Reaplica o
    # período no resultado final para tabela, seleção e filtros usarem as mesmas
    # jornadas do Monitor.
    if start_date is not None:
        setattr(qs, "_pplid_civil_start", start_date)
        setattr(qs, "_pplid_jornada_start", start_date - timedelta(days=1))
    if end_date is not None:
        setattr(qs, "_pplid_civil_end", end_date)
        setattr(qs, "_pplid_jornada_end", end_date)

    return qs


def parse_query_params(query_params) -> dict:
    return {
        "start_date": query_params.get("start_date") or None,
        "end_date": query_params.get("end_date") or None,
        "etapa": _query_multi(query_params, "etapa"),
        "team": _query_multi(query_params, "team"),
        "location": _query_multi(query_params, "location"),
        "journey_shift": _query_multi(query_params, "journey_shift"),
        "matricula": _query_multi(query_params, "matricula"),
        "leader_name": _query_multi(query_params, "leader_name"),
        "meu_time": query_params.get("meu_time"),
        "below_goal": query_params.get("below_goal"),
        "severity": query_params.get("severity") or None,
        "active_within_hours": query_params.get("active_within_hours") or None,
        "top_n": query_params.get("top_n") or None,
        "sort": query_params.get("sort") or "impact",
        "compare_days": query_params.get("compare_days") or None,
        "granularity": query_params.get("granularity") or "hour",
        "group_by": query_params.get("group_by") or "team",
        "page": query_params.get("page"),
        "page_size": query_params.get("page_size"),
        "ordering": query_params.get("ordering") or "-recorded_at",
        "detail_etapa": query_params.get("detail_etapa") or None,
        "detail_hour": query_params.get("detail_hour") or None,
    }


def get_filter_options(qs=None) -> dict:
    from django.db.models import Max, Min

    if qs is None:
        qs = ProductivityRecord.objects.all()

    date_bounds = qs.aggregate(min_date=Min("recorded_at"), max_date=Max("recorded_at"))

    def distinct_values(field: str) -> list[str]:
        """Valores únicos após strip; preserva a primeira grafia encontrada."""
        seen_key: set[str] = set()
        unique: list[str] = []
        for raw in qs.values_list(field, flat=True).distinct():
            if raw is None:
                continue
            value = str(raw).strip()
            if not value:
                continue
            key = value.casefold()
            if key in seen_key:
                continue
            seen_key.add(key)
            unique.append(value)
        return sorted(unique, key=lambda x: x.casefold())

    agentes = []
    seen = set()
    for row in qs.values("matricula_norm", "agent_name").order_by("agent_name"):
        mat = (row["matricula_norm"] or "").strip().lower()
        if not mat or mat in seen:
            continue
        seen.add(mat)
        nome = (row["agent_name"] or mat).strip() if row["agent_name"] else mat
        agentes.append({"matricula": mat, "nome": nome})

    min_dt = date_bounds["min_date"]
    max_dt = date_bounds["max_date"]
    min_civil = _to_local_date(min_dt)
    max_civil = _to_local_date(max_dt)
    max_date_iso = max_civil.isoformat() if max_civil else None
    # Padrão = último dia civil (0h–23h). Ampliar 1 dia inclui a 23h do dia anterior
    # (início do turno da madrugada).
    default_start_iso = max_date_iso
    return {
        "etapas": distinct_values("etapa"),
        "teams": distinct_values("team"),
        "locations": distinct_values("location"),
        "journey_shifts": distinct_values("journey_shift"),
        "leaders": distinct_values("leader_name"),
        "agentes": agentes,
        "min_date": min_civil.isoformat() if min_civil else None,
        "max_date": max_date_iso,
        "default_start_date": default_start_iso,
        "default_end_date": max_date_iso,
        "team_filter": {
            "can_filter_team": False,
        },
    }


def build_kpis(qs: QuerySet) -> dict:
    total_records = qs.count()
    total_seconds = qs.aggregate(total=Sum("analysis_seconds"))["total"] or 0
    etapas = qs.values("etapa").distinct().count()
    agentes = qs.values("matricula_norm").distinct().count()

    with_goal_count = qs.filter(stage_goal__gt=0).values("matricula_norm").distinct().count()
    agent_sums = _agent_period_pct_count(qs)
    avg_pct = _overall_avg_agent_count_normalized(qs)
    ctx = context_meta(qs)
    metas = agent_meta_map(qs)
    agents_below_goal = sum(
        1
        for mat, value in agent_sums.items()
        if _agent_below_daily(value, _agent_threshold(metas, mat, ctx))
    )
    hourly = ctx.hourly_threshold if ctx.hourly_threshold is not None else HOURLY_THRESHOLD

    return {
        "total_records": total_records,
        "total_analysis_hours": round(total_seconds / 3600, 2),
        "distinct_agents": agentes,
        "distinct_etapas": etapas,
        "avg_productivity_pct": avg_pct,
        "agents_below_goal": agents_below_goal,
        "agents_with_goal": with_goal_count,
        "daily_threshold": ctx.applied_meta,
        "hourly_threshold": hourly,
        **_meta_payload(ctx, avg_pct if avg_pct is not None else (
            round(sum(agent_sums.values()) / len(agent_sums), 2) if agent_sums else None
        )),
    }


def build_por_etapa(qs: QuerySet) -> list[dict]:
    results = []
    etapas = qs.values_list("etapa", flat=True).distinct()
    for etapa in etapas:
        sub = qs.filter(etapa=etapa)
        groups = _iter_shift_groups(sub)
        total_seconds = sum(g["seconds"] for g in groups.values())
        total_count = sum(g["count"] for g in groups.values())
        total_goal = sum(g["goal"] for g in groups.values())
        pct = _aggregate_productivity_pct(
            total_seconds,
            total_goal,
            total_count,
            shift_group_count=len(groups) or 1,
        )
        row = sub.aggregate(
            count=Count("id"),
            agents=Count("matricula_norm", distinct=True),
        )
        results.append(
            {
                "etapa": etapa,
                "count": row["count"],
                "agents": row["agents"],
                "total_seconds": total_seconds,
                "total_goal": total_goal if total_goal else None,
                "productivity_pct": pct,
            }
        )
    results.sort(key=lambda item: item["etapa"].lower())
    return results


def build_por_agente(qs: QuerySet, sort: str = "pct") -> tuple[list[dict], dict]:
    agent_sums = _agent_period_pct_count(qs)
    agent_normalized = _agent_count_normalized(qs)
    agent_rate_sums = _agent_period_pct(qs)
    daily_sums = _agent_daily_pct_count_sums(qs)
    names, teams, leaders = _agent_metadata_maps(qs)
    raw_pct_map, _, abatement_map, abatement_pcd_map, abatement_idle_map = (
        _agent_productivity_breakdown_maps(qs)
    )
    goal_totals = _agent_stage_goal_totals(qs)
    rate_map = _agent_rate_maps(qs, adjust_goal=False)
    rate_map_adjusted = _agent_rate_maps(qs, adjust_goal=True)
    impact_totals, _, surplus_totals = _agent_etapa_impact_detail(qs)
    last_activity = _last_activity_map(qs)
    logado_totals = _agent_monitor_logado_totals(qs)
    pace_map = build_pace_map_for_qs(qs)
    tma_map = _agent_tma_maps(qs)

    aggregates: dict[str, dict] = {}
    for row in qs.values("matricula_norm").annotate(
        total_seconds=Sum("analysis_seconds"),
        total_count=Sum("analysis_count"),
        total_goal=Sum("stage_goal"),
        count=Count("id"),
    ):
        aggregates[row["matricula_norm"]] = row

    population_norm = list(agent_normalized.values())
    population_sums = list(agent_sums.values())
    mean_pct = round(sum(population_sums) / len(population_sums), 2) if population_sums else None
    mean_pct_normalized = (
        round(sum(population_norm) / len(population_norm), 2) if population_norm else None
    )
    mean_pct_count = mean_pct
    median_pct = round(statistics.median(population_norm), 2) if population_norm else None

    results = []
    mean_volume = mean_pct if mean_pct is not None else DAILY_THRESHOLD
    ctx = context_meta(qs)
    metas = agent_meta_map(qs)
    for matricula, productivity_sum in agent_sums.items():
        productivity_norm = agent_normalized.get(matricula, 0)
        agg = aggregates.get(matricula, {})
        days_worked = sum(1 for key in daily_sums if key[0] == matricula)
        agent_eval = _agent_eval(metas, matricula, ctx)
        thr = agent_eval.applied_meta
        below = _agent_below_daily(productivity_sum, thr)
        raw_gap = _gap_pp_to_threshold(productivity_sum, thr)
        gap_pp = max(0.0, raw_gap) if below and raw_gap is not None else 0.0
        rates = rate_map.get(matricula, {})
        rates_adj = rate_map_adjusted.get(matricula, {})
        liquid = _liquid_impact_fields(
            impact_totals.get(matricula, 0),
            surplus_totals.get(matricula, 0),
        )
        goals = goal_totals.get(matricula, {})
        results.append(
            {
                "matricula": matricula,
                "nome": names.get(matricula, matricula),
                "team": teams.get(matricula, ""),
                "leader_name": leaders.get(matricula, "") or None,
                "count": agg.get("count") or 0,
                "days_worked": days_worked,
                "total_seconds": agg.get("total_seconds") or 0,
                "total_goal": float(agg["total_goal"]) if agg.get("total_goal") else None,
                "productivity_pct": productivity_sum,
                "productivity_pct_sum": productivity_sum,
                "productivity_pct_normalized": productivity_norm,
                "productivity_pct_rate": agent_rate_sums.get(matricula),
                "productivity_pct_count": productivity_sum,
                "productivity_pct_count_raw": raw_pct_map.get(matricula),
                **_productivity_abatement_payload(
                    matricula, abatement_map, abatement_pcd_map, abatement_idle_map
                ),
                "stage_goal_raw_total": round(goals.get("goal_raw", 0), 2) if goals else None,
                "stage_goal_adjusted_total": round(goals.get("goal_adjusted", 0), 2) if goals else None,
                "gap_pp": gap_pp,
                "applied_meta": agent_eval.applied_meta,
                "evaluation_type": agent_eval.evaluation_type,
                "below_daily_threshold": below,
                **_meta_payload(agent_eval, productivity_sum),
                **{k: rates.get(k) for k in (
                    "agent_sec_per_prot", "meta_sec_per_prot", "gap_sec_per_prot",
                    "agent_pph", "meta_pph", "impact_pph", "impact_protocols",
                    "actual_seconds", "meta_seconds", "actual_hms", "meta_hms",
                )},
                **_monitor_time_fields(
                    matricula,
                    int(rates.get("actual_seconds") or 0),
                    logado_totals,
                ),
                "meta_sec_per_prot_adjusted": rates_adj.get("meta_sec_per_prot"),
                "gap_sec_per_prot_adjusted": rates_adj.get("gap_sec_per_prot"),
                **liquid,
                "expected_seconds": rates.get("meta_seconds", 0),
                "expected_hms": rates.get("meta_hms", "00:00:00"),
                "time_gap_seconds": max(0, (rates.get("meta_seconds") or 0) - (rates.get("actual_seconds") or 0)),
                "impact_seconds": liquid["net_impact_time_seconds"],
                "impact_hms": liquid["net_impact_time_hms"],
                "impact_score": liquid["net_impact_time_seconds"],
                "severity": _severity(productivity_sum, thr),
                "percentile": _percentile_rank(productivity_sum, population_sums),
                "deviation_from_mean": (
                    round(productivity_sum - mean_volume, 2) if mean_pct is not None else None
                ),
                "cluster": _cluster(productivity_sum, mean_volume, thr),
                "last_activity_at": (
                    last_activity[matricula].isoformat() if matricula in last_activity else None
                ),
                **public_pace_fields(pace_map.get(matricula, {})),
                **(tma_map.get(matricula) or {}),
            }
        )

    if sort == "impact":
        results.sort(key=lambda item: item.get("net_impact_time_seconds") or 0, reverse=True)
    else:
        results.sort(key=lambda item: item.get("productivity_pct_sum") or 0, reverse=True)

    summary = {
        "mean_pct": mean_pct,
        "mean_pct_normalized": mean_pct_normalized,
        "mean_pct_count": mean_pct_count,
        "median_pct": median_pct,
        "daily_threshold": ctx.applied_meta,
        **_meta_payload(ctx, mean_pct),
    }
    return results, summary


def build_por_equipe(qs: QuerySet, group_by: str = "team") -> list[dict]:
    field_map = {
        "team": "team",
        "location": "location",
        "journey_shift": "journey_shift",
    }
    field = field_map.get(group_by, "team")
    agent_sums = _agent_period_pct_count(qs)
    agent_normalized = _agent_count_normalized(qs)
    group_map = _agent_group_map(qs, field)
    metas = agent_meta_map(qs)
    by_agent_teams = teams_by_agent(qs)
    groups_sums: dict[str, list[tuple[str, float]]] = defaultdict(list)
    groups_norm: dict[str, list[float]] = defaultdict(list)
    groups_teams: dict[str, set[str]] = defaultdict(set)
    for matricula, productivity_sum in agent_sums.items():
        label = group_map.get(matricula) or "(sem valor)"
        groups_sums[label].append((matricula, productivity_sum))
        if matricula in agent_normalized:
            groups_norm[label].append(agent_normalized[matricula])
        for t in by_agent_teams.get(matricula, []):
            groups_teams[label].add(t)

    results = []
    for label in sorted(groups_sums.keys(), key=lambda x: x.lower()):
        pairs = groups_sums[label]
        pcts = [p for _, p in pairs]
        norms = groups_norm[label]
        if field == "team":
            group_meta = resolve_meta_from_teams([label])
        else:
            group_meta = resolve_meta_from_teams(groups_teams.get(label, []))
        below = sum(
            1
            for mat, value in pairs
            if _agent_below_daily(value, _agent_threshold(metas, mat, group_meta))
        )
        agents_count = len(pcts)
        avg = round(sum(pcts) / len(pcts), 2) if pcts else None
        results.append(
            {
                "group": label,
                "group_by": field,
                "agents": agents_count,
                "below_threshold": below,
                "pct_agents_below": round((below / agents_count) * 100, 1) if agents_count else 0,
                "productivity_pct": avg,
                "productivity_pct_normalized": round(sum(norms) / len(norms), 2) if norms else None,
                "agent_pcts": sorted(pcts),
                "min_pct": round(min(pcts), 2) if pcts else None,
                "max_pct": round(max(pcts), 2) if pcts else None,
                "median_pct": round(statistics.median(pcts), 2) if pcts else None,
                "applied_meta": group_meta.applied_meta,
                "evaluation_type": group_meta.evaluation_type,
                "daily_threshold": group_meta.applied_meta,
                **_meta_payload(group_meta, avg),
            }
        )
    results.sort(key=lambda item: item["productivity_pct_normalized"] or item["productivity_pct"] or 0, reverse=True)
    return results


def _avg_nullable(values: list[float | None]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def build_supervisao(qs: QuerySet) -> dict:
    """Agrega métricas do dashboard por líder (média de operadores)."""
    from apps.produtividade.services.pace_tracking import classify_pace_delta

    agent_normalized = _agent_count_normalized(qs)
    agent_sums = _agent_period_pct_count(qs)
    names, teams, leaders = _agent_metadata_maps(qs)
    locations = _agent_group_map(qs, "location")
    rate_map = _agent_rate_maps(qs, adjust_goal=False)
    logado_totals = _agent_monitor_logado_totals(qs)
    logado_lookup = build_logado_lookup_for_qs(qs)
    last_activity = _last_activity_map(qs)
    productivity_breakdown = _agent_productivity_breakdown_maps(qs)
    goal_totals = _agent_stage_goal_totals(qs)
    impact_totals, top_etapa_map, surplus_totals = _agent_etapa_impact_detail(qs)
    pace_map = build_pace_map_for_qs(qs)

    agents = _build_priority_agents(
        qs,
        agent_normalized,
        agent_sums,
        names,
        teams,
        leaders,
        last_activity,
        rate_map,
        impact_totals,
        logado_totals,
        top_n=None,
        productivity_breakdown=productivity_breakdown,
        goal_totals=goal_totals,
        pace_map=pace_map,
        top_etapa_map=top_etapa_map,
        surplus_totals=surplus_totals,
    )

    by_leader: dict[str, list[dict]] = defaultdict(list)
    for row in agents:
        label = (row.get("leader_name") or "").strip() or "(sem líder)"
        by_leader[label].append(row)

    results: list[dict] = []
    for leader_name, rows in by_leader.items():
        n = len(rows)
        prod_norm = _avg_nullable([r.get("productivity_pct_normalized") for r in rows])
        prod_count = _avg_nullable([r.get("productivity_pct_count") for r in rows])
        prod_raw = _avg_nullable([r.get("productivity_pct_count_raw") for r in rows])
        abatement = _avg_nullable([r.get("productivity_pct_abatement") for r in rows])
        abatement_pcd = _avg_nullable([r.get("productivity_pct_abatement_pcd") for r in rows])
        abatement_idle = _avg_nullable([r.get("productivity_pct_abatement_idle") for r in rows])
        # Meta do líder: média do volume (barra) dos operadores — regra alinhada ao agente.
        leader_volume = prod_count

        pace_actual = _avg_nullable([r.get("pace_actual_pct") for r in rows])
        pace_expected = _avg_nullable([r.get("pace_expected_pct") for r in rows])
        # Aproveitamento do líder = média dos aproveitamentos individuais
        # (não avg(actual)/avg(expected), que distorce).
        atingimento = _avg_nullable([r.get("atingimento_intraday_pct") for r in rows])
        if atingimento is not None:
            atingimento = round(atingimento, 1)
        elif pace_actual is not None and pace_expected is not None and pace_expected > 0:
            atingimento = round((pace_actual / pace_expected) * 100, 1)
        pace_delta = None
        if pace_actual is not None and pace_expected is not None:
            pace_delta = round(pace_actual - pace_expected, 2)
        pace_status = classify_pace_delta(pace_delta)

        impact_sum = sum(int(r.get("impact_time_seconds") or 0) for r in rows)
        surplus_sum = sum(int(r.get("surplus_time_seconds") or 0) for r in rows)
        liquid = _liquid_impact_fields(impact_sum, surplus_sum)

        idle_fields = _leader_idle_logged_fields(rows, logado_lookup)
        last_isos = [r.get("last_activity_at") for r in rows if r.get("last_activity_at")]
        last_activity_at = max(last_isos) if last_isos else None
        # Contagens/status a partir da meta de cada colaborador (não limiar global).
        below = sum(
            1
            for r in rows
            if (
                r["below_operational_threshold"]
                if r.get("below_operational_threshold") is not None
                else r.get("below_daily_threshold")
            )
        )

        leader_teams = [r.get("team") for r in rows]
        leader_meta = resolve_meta_from_teams(leader_teams)
        leader_thr = leader_meta.applied_meta

        # Mantém a ordem de prioridade já aplicada em _build_priority_agents.
        agents_payload = list(rows)

        results.append(
            {
                "leader_name": leader_name,
                "agents_count": n,
                "agents_below_count": below,
                "agents": agents_payload,
                "productivity_pct": leader_volume,
                "productivity_pct_sum": prod_count,
                "productivity_pct_count": prod_count,
                "productivity_pct_count_raw": prod_raw,
                "productivity_pct_abatement": abatement,
                "productivity_pct_abatement_pcd": abatement_pcd,
                "productivity_pct_abatement_idle": abatement_idle,
                "productivity_pct_normalized": prod_norm,
                "gap_pp": _gap_pp_to_threshold(leader_volume, leader_thr),
                "below_daily_threshold": below > 0,
                "applied_meta": leader_thr,
                "evaluation_type": leader_meta.evaluation_type,
                **_meta_payload(leader_meta, leader_volume),
                **idle_fields,
                **liquid,
                "impact_seconds": liquid["net_impact_time_seconds"],
                "impact_hms": liquid["net_impact_time_hms"],
                "impact_score": liquid["net_impact_time_seconds"],
                "severity": _severity(leader_volume),
                "performance_band": meta_performance_tone(leader_volume, leader_thr),
                "last_activity_at": last_activity_at,
                "pace_actual_pct": pace_actual,
                "pace_expected_pct": pace_expected,
                "pace_delta_pp": pace_delta,
                "pace_status": pace_status,
                "atingimento_intraday_pct": atingimento,
            }
        )

    results.sort(
        key=lambda x: (
            0 if x.get("below_daily_threshold") else 1,
            -(x.get("idle_seconds") if x.get("idle_seconds") is not None else -1),
            -(x.get("agents_below_count") or 0),
        )
    )

    location_payload = _build_supervisao_location_dashboard(qs, agents, locations)
    ctx = context_meta(qs)
    hourly = ctx.hourly_threshold if ctx.hourly_threshold is not None else HOURLY_THRESHOLD

    return {
        "daily_threshold": ctx.applied_meta,
        "hourly_threshold": hourly,
        "leaders_count": len(results),
        "results": results,
        "idle_methodology_version": IDLE_METHODOLOGY_VERSION,
        "criticality_methodology": criticality_methodology_payload(),
        **_meta_payload(ctx),
        **location_payload,
    }


def _location_performance_band(
    avg_pct: float | None,
    threshold: float | None = DAILY_THRESHOLD,
) -> str:
    """ok | near | below | neutral — faixa visual do ranking por localidade."""
    return meta_performance_tone(avg_pct, threshold)


def _format_hour_label(hour) -> str:
    if hasattr(hour, "strftime"):
        return hour.strftime("%H:%M")
    text = str(hour)
    if "T" in text:
        return text.split("T", 1)[1][:5]
    return text[:5] if len(text) >= 5 else text


def _supervisao_peak_impact_hour(qs: QuerySet) -> dict | None:
    """Horário com maior atraso relativo (menor produção média horária)."""
    hourly_sums = _agent_hourly_pct_sums(qs)
    if not hourly_sums:
        return None
    per_hour: dict[object, list[float]] = defaultdict(list)
    for (_mat, hour), pct in hourly_sums.items():
        per_hour[hour].append(float(pct))
    if not per_hour:
        return None
    hour, pcts = min(per_hour.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
    avg = round(sum(pcts) / len(pcts), 2)
    below = sum(1 for p in pcts if p < HOURLY_THRESHOLD)
    return {
        "hour": hour.isoformat() if hasattr(hour, "isoformat") else str(hour),
        "label": _format_hour_label(hour),
        "avg_productivity_pct": avg,
        "agents_count": len(pcts),
        "agents_below_count": below,
    }


def _build_supervisao_pareto(agents: list[dict], attention_pct: float = 80.0) -> list[dict]:
    """Pareto do impacto líquido (a recuperar − mais rápido) — só abaixo da meta.

    Acumulado sobre esse conjunto inteiro. ``in_attention_band`` marca os agentes
    que concentram os primeiros ``attention_pct``% do impacto líquido (foco vermelho).
    Nunca usa o bruto “a recuperar” sozinho para ranquear ou medir share.
    """

    def _net(row: dict) -> int:
        if row.get("net_impact_time_seconds") is not None:
            return int(row.get("net_impact_time_seconds") or 0)
        impact = int(row.get("impact_time_seconds") or 0)
        surplus = int(row.get("surplus_time_seconds") or 0)
        return impact - surplus

    below = [
        r
        for r in agents
        if _row_below_operational(r) and _net(r) > 0
    ]
    ranked = sorted(below, key=_net, reverse=True)
    total = sum(_net(r) for r in ranked)
    if total <= 0:
        return []

    cum = 0
    out = []
    crossed_attention = False
    for row in ranked:
        impact = _net(row)
        cum += impact
        cum_pct = round(100.0 * cum / total, 2)
        # Inclui o agente que “fecha” o bandão de atenção (80%).
        in_band = not crossed_attention
        if cum_pct >= attention_pct:
            crossed_attention = True
        mat = row.get("matricula") or ""
        out.append(
            {
                "matricula": mat,
                "nome": row.get("nome") or mat,
                # impact_time_seconds no Pareto = líquido (compat FE).
                "impact_time_seconds": impact,
                "net_impact_time_seconds": impact,
                "surplus_time_seconds": int(row.get("surplus_time_seconds") or 0),
                "impact_share_pct": round(100.0 * impact / total, 2),
                "cumulative_pct": cum_pct,
                "in_attention_band": in_band,
            }
        )
    return out


def _build_supervisao_shift_breakdown(
    agents: list[dict],
    shifts: dict[str, str],
) -> list[dict]:
    """Agrega produção / abaixo da meta / impacto por turno (journey_shift)."""
    by_shift: dict[str, dict] = defaultdict(
        lambda: {
            "matriculas": set(),
            "below": 0,
            "productivity_pct_sum": 0.0,
            "impact_time_seconds": 0,
        }
    )
    seen: set[str] = set()
    for row in agents:
        mat = row.get("matricula") or ""
        if not mat or mat in seen:
            continue
        seen.add(mat)
        shift = (shifts.get(mat) or "").strip() or "(sem turno)"
        bucket = by_shift[shift]
        bucket["matriculas"].add(mat)
        if _row_below_operational(row):
            bucket["below"] += 1
        bucket["productivity_pct_sum"] += float(row.get("productivity_pct_count") or 0)
        bucket["impact_time_seconds"] += _location_impact_seconds(row)

    results = []
    for shift in sorted(by_shift.keys(), key=lambda x: x.lower()):
        bucket = by_shift[shift]
        n = len(bucket["matriculas"])
        prod_sum = float(bucket["productivity_pct_sum"])
        prod_avg = round(prod_sum / n, 2) if n else None
        below = int(bucket["below"])
        results.append(
            {
                "journey_shift": shift,
                "agents_count": n,
                "agents_below_count": below,
                "agents_below_pct": round(100.0 * below / n, 2) if n else 0.0,
                "productivity_pct_avg": prod_avg,
                "impact_time_seconds": bucket["impact_time_seconds"],
                "performance_band": _location_performance_band(prod_avg),
            }
        )
    results.sort(
        key=lambda r: (
            -(r["productivity_pct_avg"] if r["productivity_pct_avg"] is not None else -1),
            r["journey_shift"].lower(),
        )
    )
    return results


def _format_recovery_insight(seconds: int) -> str:
    """Formata tempo de recuperação: <1h → '48 min'; <24h → '2h 15min'; ≥24h → '2d 9h'."""
    total = max(0, int(seconds))
    days = total // 86400
    hours = (total % 86400) // 3600
    minutes = (total % 3600) // 60
    if days:
        if hours:
            return f"{days}d {hours}h"
        return f"{days}d"
    if hours and minutes:
        return f"{hours}h {minutes}min"
    if hours:
        return f"{hours}h"
    if minutes:
        return f"{minutes} min"
    return f"{total}s"


def _location_net_impact_seconds(row: dict) -> int:
    """Impacto líquido assinado (positivo = a recuperar; negativo = adiantado)."""
    net = row.get("net_impact_time_seconds")
    if net is not None:
        return int(net)
    impact = int(row.get("impact_time_seconds") or 0)
    surplus = int(row.get("surplus_time_seconds") or 0)
    return impact - surplus


def _location_impact_seconds(row: dict) -> int:
    """Líquido não-negativo para priorização / tempo estimado (zera adiantados)."""
    return max(0, _location_net_impact_seconds(row))


def _build_supervisao_location_insights(
    location_breakdown: list[dict],
    kpis: dict,
    agents: list[dict],
    *,
    peak_hour: dict | None,
    shift_breakdown: list[dict],
) -> list[str]:
    """Alertas prioritários acionáveis (localidade / líder / visão ampla)."""
    insights: list[str] = []
    locs = [r for r in location_breakdown if (r.get("agents_count") or 0) > 0]
    leaders = {
        (r.get("leader_name") or "").strip()
        for r in agents
        if (r.get("leader_name") or "").strip()
    }
    single_loc = len(locs) == 1
    single_leader = len(leaders) == 1
    total_impact = sum(_location_impact_seconds(r) for r in locs)

    def _share_pct(seconds: int) -> float:
        if total_impact <= 0:
            return 0.0
        return round(100.0 * seconds / total_impact, 1)

    if single_loc:
        loc = locs[0]
        name = loc["location"]
        below = int(loc.get("agents_below_count") or 0)
        agents_n = int(loc.get("agents_count") or 0)
        avg = float(loc.get("productivity_pct_avg") or 0)
        meta = kpis.get("daily_threshold")
        if meta is not None:
            gap = max(0.0, float(meta) - avg)
            meta_txt = f" (↓ {gap:.1f} p.p. vs meta {float(meta):.0f}%)"
        else:
            meta_txt = " (meta não identificada)"
        insights.append(
            f"Prioridade 1: {name} requer atenção — "
            f"{below} de {agents_n} operador(es) abaixo da meta, "
            f"produção média {avg:.1f}%{meta_txt}."
        )
        recovery = _location_impact_seconds(loc)
        if recovery > 0:
            insights.append(
                f"Impacto recuperável em {name}: {_format_recovery_insight(recovery)} "
                f"(total da localidade; o KPI do topo é a média por operador)."
            )
        priority = [
            r
            for r in agents
            if _row_below_operational(r)
            and int(r.get("net_impact_time_seconds") or r.get("impact_time_seconds") or 0) > 0
        ]
        priority.sort(
            key=lambda r: int(
                r.get("net_impact_time_seconds") or r.get("impact_time_seconds") or 0
            ),
            reverse=True,
        )
        for i, row in enumerate(priority[:2], start=2):
            impact = int(
                row.get("net_impact_time_seconds") or row.get("impact_time_seconds") or 0
            )
            insights.append(
                f"Prioridade {i}: atuar com {row.get('nome') or row.get('matricula')} — "
                f"produção {row.get('productivity_pct_count') or 0:.1f}%, "
                f"impacto {_format_recovery_insight(impact)}."
            )
    elif single_leader:
        leader = next(iter(leaders))
        team = [
            r
            for r in agents
            if (r.get("leader_name") or "").strip() == leader
        ]
        below = [r for r in team if _row_below_operational(r)]
        insights.append(
            f"Prioridade 1: time de {leader} — "
            f"{len(below)} de {len(team)} operador(es) abaixo do ritmo operacional."
        )
        priority = sorted(
            below,
            key=lambda r: int(
                r.get("net_impact_time_seconds") or r.get("impact_time_seconds") or 0
            ),
            reverse=True,
        )
        for i, row in enumerate(priority[:3], start=2):
            insights.append(
                f"Prioridade {i}: {row.get('nome') or row.get('matricula')} — "
                f"produção {row.get('productivity_pct_count') or 0:.1f}%, "
                f"gap {row.get('gap_pp') or 0:.1f} p.p. para a meta."
            )
        if not below:
            insights.append(f"Time de {leader} sem operadores abaixo da meta no filtro atual.")
    else:
        focus = max(
            locs,
            key=lambda r: (
                r.get("agents_below_count") or 0,
                _location_impact_seconds(r),
            ),
            default=None,
        )
        if focus and (focus.get("agents_below_count") or 0) > 0:
            impact_s = _location_impact_seconds(focus)
            share = _share_pct(impact_s)
            avg = float(focus.get("productivity_pct_avg") or 0)
            insights.append(
                f"Prioridade 1: {focus['location']} requer maior atenção — "
                f"{focus['agents_below_count']} operadores abaixo da meta, "
                f"produção média {avg:.1f}%"
                + (
                    f", {share:.1f}% do impacto recuperável do período"
                    if share > 0
                    else ""
                )
                + (
                    f", {_format_recovery_insight(impact_s)} a recuperar"
                    if impact_s > 0
                    else ""
                )
                + "."
            )

    if shift_breakdown:
        worst_shift = max(
            shift_breakdown,
            key=lambda r: (
                r.get("agents_below_count") or 0,
                r.get("impact_time_seconds") or 0,
            ),
        )
        if (worst_shift.get("agents_below_count") or 0) > 0:
            prod = worst_shift.get("productivity_pct_avg")
            prod_txt = f"{prod:.1f}%" if prod is not None else "—"
            insights.append(
                f"Prioridade: turno {worst_shift['journey_shift']} concentra maior impacto — "
                f"{worst_shift['agents_below_count']} de {worst_shift.get('agents_count') or 0} "
                f"abaixo da meta, produção média {prod_txt}."
            )

    if peak_hour and (peak_hour.get("agents_count") or 0) > 0:
        label = peak_hour.get("label") or peak_hour.get("hour") or "—"
        avg_h = peak_hour.get("avg_productivity_pct")
        avg_txt = f"{avg_h:.2f}%" if isinstance(avg_h, (int, float)) else f"{avg_h}%"
        below_h = int(peak_hour.get("agents_below_count") or 0)
        agents_h = int(peak_hour.get("agents_count") or 0)
        insights.append(
            f"Horário crítico: às {label}, produção média horária {avg_txt} "
            f"({below_h} de {agents_h} agentes abaixo do limiar horário de "
            f"{HOURLY_THRESHOLD:.2f}%)."
        )

    critical_agents = [
        r
        for r in agents
        if (
            r.get("operational_status") == "critical"
            or (
                _row_below_operational(r)
                and r.get("severity") == SEVERITY_CRITICAL
            )
        )
    ]
    if critical_agents:
        insights.append(
            f"Faixa crítica: {len(critical_agents)} operador(es) com perda grave/sustendada "
            f"(priorize a tabela de casos ao final da tela)."
        )

    avg = kpis.get("productivity_avg")
    meta = kpis.get("daily_threshold")
    if avg is not None and meta is not None and avg < meta:
        gap = round(float(meta) - float(avg), 1)
        insights.append(
            f"Meta do filtro: produção média {avg:.1f}% está ↓ {gap:.1f} p.p. "
            f"abaixo da meta de {float(meta):.0f}%."
        )

    # Dedup preservando ordem
    seen: set[str] = set()
    unique: list[str] = []
    for text in insights:
        if text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique[:6]


def _build_supervisao_location_dashboard(
    qs: QuerySet,
    agents: list[dict],
    locations: dict[str, str],
) -> dict:
    """KPIs, ranking, scatter, críticos, Pareto, turnos e insights contextuais.

    Nota: séries semanais (location_trend) foram removidas — período típico é 1 dia.
    """
    by_location: dict[str, dict] = defaultdict(
        lambda: {
            "matriculas": set(),
            "below": 0,
            "productivity_pct_sum": 0.0,
            "impact_time_seconds": 0,
            "surplus_time_seconds": 0,
            "net_impact_time_seconds": 0,
        }
    )
    seen_mats: set[str] = set()
    for row in agents:
        mat = row.get("matricula") or ""
        if not mat or mat in seen_mats:
            continue
        seen_mats.add(mat)
        loc = (locations.get(mat) or "").strip() or "(sem localidade)"
        bucket = by_location[loc]
        bucket["matriculas"].add(mat)
        if _row_below_operational(row):
            bucket["below"] += 1
        bucket["productivity_pct_sum"] += float(row.get("productivity_pct_count") or 0)
        bucket["impact_time_seconds"] += int(row.get("impact_time_seconds") or 0)
        bucket["surplus_time_seconds"] += int(row.get("surplus_time_seconds") or 0)
        bucket["net_impact_time_seconds"] += int(row.get("net_impact_time_seconds") or 0)

    location_breakdown = []
    loc_meta = context_meta(qs)
    for loc in sorted(by_location.keys(), key=lambda x: x.lower()):
        bucket = by_location[loc]
        n_agents = len(bucket["matriculas"])
        prod_sum = float(bucket["productivity_pct_sum"])
        prod_avg = round(prod_sum / n_agents, 2) if n_agents else None
        below = int(bucket["below"])
        loc_thr = loc_meta.applied_meta
        location_breakdown.append(
            {
                "location": loc,
                "agents_count": n_agents,
                "agents_below_count": below,
                "agents_below_pct": round(100.0 * below / n_agents, 2) if n_agents else 0.0,
                "agents_on_goal_count": max(0, n_agents - below),
                "productivity_pct_sum": round(prod_sum, 2),
                "productivity_pct_avg": prod_avg,
                "gap_pp": _gap_pp_to_threshold(prod_avg, loc_thr),
                "performance_band": _location_performance_band(prod_avg, loc_thr),
                "impact_time_seconds": bucket["impact_time_seconds"],
                "surplus_time_seconds": bucket["surplus_time_seconds"],
                "net_impact_time_seconds": bucket["net_impact_time_seconds"],
                "avg_recovery_time_seconds": (
                    round(bucket["net_impact_time_seconds"] / below) if below else 0
                ),
            }
        )

    location_breakdown.sort(
        key=lambda r: (
            -(r["productivity_pct_avg"] if r["productivity_pct_avg"] is not None else -1),
            r["location"].lower(),
        )
    )

    all_pcts = [
        float(r.get("productivity_pct_count") or 0)
        for r in agents
        if r.get("productivity_pct_count") is not None
    ]
    below_rows = [r for r in agents if _row_below_operational(r)]
    recovery_vals = [_location_impact_seconds(r) for r in below_rows]
    prod_avg_all = round(sum(all_pcts) / len(all_pcts), 2) if all_pcts else None
    realized_vals = [int(r.get("protocols_count") or 0) for r in agents]
    potential_vals = [
        float(r.get("potential_protocols") or 0)
        for r in agents
        if r.get("potential_protocols") is not None
    ]
    avg_realized = (
        round(sum(realized_vals) / len(realized_vals), 1) if realized_vals else None
    )
    avg_potential = (
        round(sum(potential_vals) / len(potential_vals), 1) if potential_vals else None
    )

    # Delta vs metade anterior da série diária (mesmo recorte filtrado).
    daily_sums = _agent_daily_pct_count_sums(qs)
    per_day: dict[date, list[float]] = defaultdict(list)
    for (_, day), total in daily_sums.items():
        per_day[day].append(total)
    sorted_days = sorted(per_day.keys())
    delta_prod = None
    delta_below = None
    if len(sorted_days) >= 2:
        mid = len(sorted_days) // 2
        prev_days, curr_days = sorted_days[:mid], sorted_days[mid:]
        prev_pcts = [p for d in prev_days for p in per_day[d]]
        curr_pcts = [p for d in curr_days for p in per_day[d]]
        if prev_pcts and curr_pcts:
            prev_avg = sum(prev_pcts) / len(prev_pcts)
            curr_avg = sum(curr_pcts) / len(curr_pcts)
            delta_prod = round(curr_avg - prev_avg, 2)
            prev_below = sum(1 for p in prev_pcts if _agent_below_daily(p))
            curr_below = sum(1 for p in curr_pcts if _agent_below_daily(p))
            delta_below = curr_below - prev_below

    location_kpis = {
        "productivity_avg": prod_avg_all,
        "daily_threshold": context_meta(qs).applied_meta,
        "agents_monitored": len(agents),
        "agents_below_count": len(below_rows),
        "agents_below_pct": (
            round(100.0 * len(below_rows) / len(agents), 2) if agents else 0.0
        ),
        "avg_recovery_time_seconds": (
            round(sum(recovery_vals) / len(recovery_vals)) if recovery_vals else 0
        ),
        "avg_realized_protocols": avg_realized,
        "avg_potential_protocols": avg_potential,
        "delta_productivity_pp": delta_prod,
        "delta_agents_below": delta_below,
    }

    # Quadrante: produção × tempo líquido assinado; outlier = abaixo e líquido alto.
    impact_all = [_location_impact_seconds(r) for r in agents]
    impact_p75 = 0
    if impact_all:
        ordered = sorted(impact_all)
        impact_p75 = ordered[max(0, int(len(ordered) * 0.75) - 1)]

    location_scatter = []
    for row in agents:
        mat = row.get("matricula") or ""
        loc = (locations.get(mat) or "").strip() or "(sem localidade)"
        pct = row.get("productivity_pct_count")
        net = _location_net_impact_seconds(row)
        recovery = max(0, net)
        below = _row_below_operational(row)
        outlier = below and recovery >= max(impact_p75, 1)
        location_scatter.append(
            {
                "matricula": mat,
                "nome": row.get("nome") or mat,
                "location": loc,
                "productivity_pct": pct,
                # Compat FE antigo: campo bruto de eixo Y; agora = líquido assinado.
                "impact_time_seconds": net,
                "net_impact_time_seconds": net,
                "below_daily_threshold": below,
                "below_operational_threshold": below,
                "severity": row.get("severity"),
                "outlier": outlier,
            }
        )

    critical_agents = []
    for row in below_rows[:50]:
        mat = row.get("matricula") or ""
        loc = (locations.get(mat) or "").strip() or "(sem localidade)"
        pct = row.get("productivity_pct_count")
        net = _location_net_impact_seconds(row)
        recovery = max(0, net)
        critical_agents.append(
            {
                "matricula": mat,
                "nome": row.get("nome") or mat,
                "location": loc,
                "productivity_pct": pct,
                "gap_pp": row.get("gap_pp") or _gap_pp_to_threshold(pct),
                "impact_time_seconds": recovery,
                "net_impact_time_seconds": net,
                "severity": row.get("severity") or _severity(pct),
                "leader_name": row.get("leader_name"),
            }
        )

    shifts = _agent_group_map(qs, "journey_shift")
    location_pareto = _build_supervisao_pareto(agents)
    shift_breakdown = _build_supervisao_shift_breakdown(agents, shifts)
    peak_hour = _supervisao_peak_impact_hour(qs)
    location_insights = _build_supervisao_location_insights(
        location_breakdown,
        location_kpis,
        agents,
        peak_hour=peak_hour,
        shift_breakdown=shift_breakdown,
    )

    return {
        "location_breakdown": location_breakdown,
        "location_kpis": location_kpis,
        "location_scatter": location_scatter,
        "location_critical_agents": critical_agents,
        "location_pareto": location_pareto,
        "shift_breakdown": shift_breakdown,
        "peak_impact_hour": peak_hour,
        "location_insights": location_insights,
    }


def _hourly_overview_from_sums(
    hourly_sums: dict[tuple[str, object], float],
    threshold_map: dict[tuple[str, object], float | None] | None = None,
) -> list[dict]:
    per_hour: dict[object, list[float]] = defaultdict(list)
    per_hour_thresholds: dict[object, list[float]] = defaultdict(list)
    for (matricula, hour), total in hourly_sums.items():
        per_hour[hour].append(total)
        if threshold_map is not None:
            thr = threshold_map.get((matricula, hour))
            if thr is not None:
                per_hour_thresholds[hour].append(thr)

    results = []
    for hour in sorted(per_hour.keys()):
        pcts = per_hour[hour]
        avg_pct = round(sum(pcts) / len(pcts), 2) if pcts else None
        thresholds = per_hour_thresholds.get(hour, [])
        if thresholds:
            avg_threshold = round(sum(thresholds) / len(thresholds), 2)
        else:
            avg_threshold = HOURLY_THRESHOLD
        label = hour.isoformat() if hasattr(hour, "isoformat") else str(hour)
        below = (
            avg_pct is not None
            and avg_threshold is not None
            and avg_pct < avg_threshold
        )
        results.append(
            {
                "hour": label,
                "period": label,
                "productivity_pct": avg_pct,
                "agents_count": len(pcts),
                "hourly_forecast_pct": avg_threshold if thresholds else HOURLY_THRESHOLD,
                "below_threshold": below,
            }
        )
    return results


def build_por_hora(qs: QuerySet) -> list[dict]:
    hourly_sums = _agent_hourly_pct_sums(qs)
    threshold_map = build_hourly_threshold_map_for_qs(qs, hourly_sums)
    return _hourly_overview_from_sums(hourly_sums, threshold_map)


def _civil_datetime_from_jornada_hour(data_jornada: date, hour: int) -> datetime:
    """Converte (data_jornada, clock hour) → datetime civil em Brasília.

    Jornada D cobre D 05:15 → D+1 05:14. Logo horas 0–5 pertencem ao dia civil D+1.
    """
    tz = timezone.get_current_timezone()
    clock = int(hour) % 24
    civil_day = data_jornada + timedelta(days=1) if clock <= 5 else data_jornada
    return timezone.make_aware(
        datetime(civil_day.year, civil_day.month, civil_day.day, clock, 0, 0),
        tz,
    )


def _logged_sums_by_agent_hour(qs: QuerySet) -> dict[tuple[str, int], float]:
    """Soma segundos logados (monitor) por (matrícula, clock hour) no período civil."""
    lookup = build_monitor_hourly_logado_lookup(qs)
    sums: dict[tuple[str, int], float] = defaultdict(float)
    for (matricula, day, hour), seconds in lookup.items():
        clock = int(hour) if hour is not None else None
        if clock is None or clock < 0 or clock > 23:
            continue
        if not _civil_slot_in_filter(qs, day, clock):
            continue
        mat = str(matricula or "").strip().lower()
        if not mat:
            continue
        sums[(mat, clock)] += float(seconds or 0.0)
    return dict(sums)


def _logged_detail_sums(qs: QuerySet) -> dict[tuple[str, date, int], float]:
    """Soma segundos logados por (matrícula, data_jornada, hora). Preserva dia para overview."""
    lookup = build_monitor_hourly_logado_lookup(qs)
    sums: dict[tuple[str, date, int], float] = defaultdict(float)
    for (matricula, day, hour), seconds in lookup.items():
        clock = int(hour) if hour is not None else None
        if clock is None or clock < 0 or clock > 23:
            continue
        if not _civil_slot_in_filter(qs, day, clock):
            continue
        mat = str(matricula or "").strip().lower()
        if not mat:
            continue
        sums[(mat, day, clock)] += float(seconds or 0.0)
    return dict(sums)


def _logged_overview_from_detail(
    detail_sums: dict[tuple[str, date, int], float],
) -> list[dict]:
    """Overview logado por datetime civil (Brasília), alinhado aos labels de produção."""
    per_slot: dict[datetime, list[float]] = defaultdict(list)
    for (_matricula, day, hour), seconds in detail_sums.items():
        slot_dt = _civil_datetime_from_jornada_hour(day, hour)
        per_slot[slot_dt].append(float(seconds))
    results: list[dict] = []
    for slot_dt in sorted(per_slot.keys()):
        vals = per_slot[slot_dt]
        results.append(
            {
                "hour": slot_dt.isoformat(),
                "logged_seconds_avg": (
                    round(sum(vals) / len(vals), 2) if vals else None
                ),
                "agents_count": len(vals),
            }
        )
    return results


def build_por_hora_page(qs: QuerySet) -> dict:
    names, _, _ = _agent_metadata_maps(qs)
    agent_sums = _agent_period_pct_count(qs)
    hourly_sums = _agent_hourly_pct_sums(qs)
    threshold_map = build_hourly_threshold_map_for_qs(qs, hourly_sums)
    hourly_overview = _hourly_overview_from_sums(hourly_sums, threshold_map)
    logged_sums = _logged_sums_by_agent_hour(qs)
    logged_detail = _logged_detail_sums(qs)
    logged_agent_totals: dict[str, float] = defaultdict(float)
    for (matricula, _hour), seconds in logged_sums.items():
        logged_agent_totals[matricula] += float(seconds)
    for matricula in logged_agent_totals:
        names.setdefault(matricula, matricula)
    return {
        "hourly_threshold": HOURLY_THRESHOLD,
        "results": hourly_overview,
        "hourly_heatmap": _compute_hourly_heatmap(
            hourly_sums, names, agent_sums, threshold_map
        ),
        "logged_heatmap": _compute_hourly_heatmap(
            logged_sums,
            names,
            dict(logged_agent_totals),
            threshold_map=None,
        ),
        "logged_overview": _logged_overview_from_detail(logged_detail),
        "intraday_series": _compute_intraday_series(hourly_overview),
    }


def build_dashboard(qs: QuerySet, top_n: int | None = None) -> dict:
    agent_normalized = _agent_count_normalized(qs)
    agent_sums = _agent_period_pct_count(qs)
    names, teams, leaders = _agent_metadata_maps(qs)
    hourly_sums = _agent_hourly_pct_sums(qs)
    threshold_map = build_hourly_threshold_map_for_qs(qs, hourly_sums)
    hourly_overview = _hourly_overview_from_sums(hourly_sums, threshold_map)
    rate_map = _agent_rate_maps(qs, adjust_goal=False)
    logado_totals = _agent_monitor_logado_totals(qs)
    last_activity = _last_activity_map(qs)
    groups_raw = _iter_shift_groups(qs, adjust_goal=False)
    rate_gap_data = _rate_gap(qs, groups=groups_raw)
    productivity_breakdown = _agent_productivity_breakdown_maps(qs)
    goal_totals = _agent_stage_goal_totals(qs)
    avg_pct = (
        round(sum(agent_normalized.values()) / len(agent_normalized), 2)
        if agent_normalized
        else None
    )

    agents_below_daily = []
    ctx = context_meta(qs)
    metas = agent_meta_map(qs)
    for matricula, volume_pct in sorted(agent_sums.items(), key=lambda item: item[1]):
        thr = _agent_threshold(metas, matricula, ctx)
        if not _agent_below_daily(volume_pct, thr):
            continue
        agents_below_daily.append(
            {
                "matricula": matricula,
                "nome": names.get(matricula, matricula),
                "team": teams.get(matricula, ""),
                "productivity_pct": volume_pct,
                "productivity_pct_sum": volume_pct,
                "productivity_pct_normalized": agent_normalized.get(matricula),
                "gap": _gap_pp_to_threshold(volume_pct, thr),
                "applied_meta": metas.get(matricula, ctx).applied_meta,
            }
        )

    per_agent_hours: dict[str, list[dict]] = defaultdict(list)
    for (matricula, hour), total in hourly_sums.items():
        thr = threshold_map.get((matricula, hour), HOURLY_THRESHOLD)
        if thr is None:
            continue
        if total >= thr:
            continue
        label = hour.isoformat() if hasattr(hour, "isoformat") else str(hour)
        per_agent_hours[matricula].append(
            {
                "hour": label,
                "productivity_pct": round(total, 2),
                "hourly_forecast_pct": thr,
                "gap": round(thr - total, 2),
            }
        )

    agents_hourly_alerts = []
    for matricula, hours_below in per_agent_hours.items():
        hours_below.sort(key=lambda item: item["productivity_pct"])
        worst = hours_below[0]
        agents_hourly_alerts.append(
            {
                "matricula": matricula,
                "nome": names.get(matricula, matricula),
                "team": teams.get(matricula, ""),
                "hours_below_count": len(hours_below),
                "hours_below": hours_below[:12],
                "worst_hour": worst["hour"],
                "worst_pct": worst["productivity_pct"],
            }
        )
    agents_hourly_alerts.sort(key=lambda item: (item["hours_below_count"], item["worst_pct"]))

    hours_below_count = sum(1 for row in hourly_overview if row.get("below_threshold"))
    impact_totals, top_etapa_map, surplus_totals = _agent_etapa_impact_detail(qs)
    pace_map = build_pace_map_for_qs(qs)
    priority_agents = _build_priority_agents(
        qs,
        agent_normalized,
        agent_sums,
        names,
        teams,
        leaders,
        last_activity,
        rate_map,
        impact_totals,
        logado_totals,
        top_n=top_n,
        productivity_breakdown=productivity_breakdown,
        goal_totals=goal_totals,
        pace_map=pace_map,
        top_etapa_map=top_etapa_map,
        surplus_totals=surplus_totals,
        ctx_meta=ctx,
        agent_metas=metas,
    )

    heatmap = _compute_hourly_heatmap(
        hourly_sums, names, agent_sums, threshold_map
    )
    hourly = ctx.hourly_threshold if ctx.hourly_threshold is not None else HOURLY_THRESHOLD
    avg_sum = round(sum(agent_sums.values()) / len(agent_sums), 2) if agent_sums else None

    return {
        "attainment": _build_attainment(
            qs,
            hourly_overview,
            agent_normalized=agent_normalized,
            agent_sums=agent_sums,
            rate_gap_data=rate_gap_data,
            pace_map=pace_map,
            last_activity=last_activity,
            ctx_meta=ctx,
            agent_metas=metas,
        ),
        "priority_agents": priority_agents,
        "heatmap_insights": heatmap["insights"],
        "intraday_series": _compute_intraday_series(hourly_overview),
        "avg_productivity_pct": avg_pct,
        "daily_threshold": ctx.applied_meta,
        "hourly_threshold": hourly,
        "agents_monitored": len(agent_normalized),
        "agents_below_daily_count": len(agents_below_daily),
        "hours_below_threshold_count": hours_below_count,
        "agents_below_daily": agents_below_daily[:100],
        "hourly_overview": hourly_overview,
        "agents_hourly_alerts": agents_hourly_alerts[:100],
        **_meta_payload(ctx, avg_sum),
    }


def build_evolucao(qs: QuerySet, compare_days: int = 7) -> dict:
    daily_sums = _agent_daily_pct_count_sums(qs)
    ctx = context_meta(qs)
    per_day: dict[date, list[float]] = defaultdict(list)
    for (_, day), total in daily_sums.items():
        per_day[day].append(total)

    sorted_days = sorted(per_day.keys())
    daily_series = []
    window: list[float] = []
    for day in sorted_days:
        agent_day_avgs = per_day[day]
        day_pct = round(sum(agent_day_avgs) / len(agent_day_avgs), 2) if agent_day_avgs else None
        if day_pct is not None:
            window.append(day_pct)
            if len(window) > 3:
                window = window[-3:]
            rolling = round(sum(window) / len(window), 2)
        else:
            rolling = None
        daily_series.append(
            {
                "date": day.isoformat(),
                "productivity_pct": day_pct,
                "rolling_avg_3d": rolling,
            }
        )

    if not daily_series:
        return {
            "daily_series": [],
            "compare_previous": {
                "current_avg": None,
                "previous_avg": None,
                "delta_pp": None,
                "direction": "flat",
            },
            "daily_threshold": ctx.applied_meta,
            "compare_days": compare_days,
            **_meta_payload(ctx),
        }

    n = min(compare_days, len(daily_series))
    current_slice = daily_series[-n:]
    previous_slice = daily_series[-(2 * n) : -n] if len(daily_series) >= 2 * n else []

    current_vals = [d["productivity_pct"] for d in current_slice if d["productivity_pct"] is not None]
    previous_vals = [d["productivity_pct"] for d in previous_slice if d["productivity_pct"] is not None]

    current_avg = round(sum(current_vals) / len(current_vals), 2) if current_vals else None
    previous_avg = round(sum(previous_vals) / len(previous_vals), 2) if previous_vals else None

    delta_pp = None
    direction = "flat"
    if current_avg is not None and previous_avg is not None:
        delta_pp = round(current_avg - previous_avg, 2)
        if delta_pp > 1:
            direction = "up"
        elif delta_pp < -1:
            direction = "down"

    return {
        "daily_series": daily_series,
        "compare_previous": {
            "current_avg": current_avg,
            "previous_avg": previous_avg,
            "delta_pp": delta_pp,
            "direction": direction,
        },
        "daily_threshold": ctx.applied_meta,
        "compare_days": compare_days,
        **_meta_payload(ctx, current_avg),
    }


def serialize_record(
    record: ProductivityRecord,
    ociosidade_lookup: dict[tuple[str, date], int] | None = None,
    discount_lookup: dict[tuple[str, date], float] | None = None,
    logado_lookup: dict[tuple[str, date], int] | None = None,
) -> dict:
    seconds = record.analysis_seconds
    count = record.analysis_count
    raw_goal = float(record.stage_goal) if record.stage_goal is not None else 0.0
    jornada = jornada_from_recorded_at(record.recorded_at) if record.recorded_at else None
    if ociosidade_lookup is None:
        ociosidade_lookup = {}
    if discount_lookup is None:
        discount_lookup = {}
    if logado_lookup is None:
        logado_lookup = {}
    ociosidade = (
        ociosidade_for_goal_adjustment(ociosidade_lookup, record.matricula_norm, jornada)
        if jornada
        else 0
    )
    discount_hours = (
        discount_for_agent_day(discount_lookup, record.matricula_norm, jornada)
        if jornada
        else 0.0
    )
    mat_key = (record.matricula_norm or "").strip().lower()
    logado_seconds = logado_lookup.get((mat_key, jornada), 0) if jornada else 0
    adjusted_goal = adjust_stage_goal_full(
        raw_goal,
        discount_hours,
        ociosidade,
        logado_seconds=logado_seconds,
    )
    row_metrics_pure = _compute_rate_metrics(count, seconds, raw_goal, META_SHIFT_SECONDS)
    row_metrics_adj = _compute_rate_metrics(count, seconds, adjusted_goal, META_SHIFT_SECONDS)
    pcd_goal = adjust_stage_goal(raw_goal, discount_hours)
    row_metrics_pcd = _compute_rate_metrics(count, seconds, pcd_goal, META_SHIFT_SECONDS)
    pct_raw = productivity_pct_count(seconds, raw_goal, count)
    pct_pcd = productivity_pct_count(seconds, pcd_goal, count)
    pct_adj = productivity_pct_count(seconds, adjusted_goal, count)
    pct_abatement = None
    pct_abatement_pcd = None
    pct_abatement_idle = None
    if pct_raw is not None and pct_adj is not None:
        pcd_v = float(pct_pcd if pct_pcd is not None else pct_raw)
        pct_abatement_pcd = round(max(0.0, pcd_v - float(pct_raw)), 2)
        pct_abatement_idle = round(max(0.0, float(pct_adj) - pcd_v), 2)
        pct_abatement = round(pct_abatement_pcd + pct_abatement_idle, 2)
    return {
        "id": record.id,
        "matricula": record.matricula_norm,
        "agent_name": record.agent_name,
        "etapa": record.etapa,
        "analysis_seconds": seconds,
        "analysis_count": count,
        "stage_goal": float(record.stage_goal) if record.stage_goal is not None else None,
        "stage_goal_adjusted": adjusted_goal if raw_goal > 0 else None,
        "productivity_discount_hours": discount_hours if discount_hours > 0 else 0,
        "tempo_ocioso_dia": ociosidade if ociosidade > 0 else 0,
        "meta_seconds": META_SHIFT_SECONDS,
        "goal_hms": _format_hms(META_SHIFT_SECONDS),
        "analysis_hms": _format_hms(seconds),
        "productivity_pct": productivity_pct_time(seconds, raw_goal, count),
        "productivity_pct_adjusted": productivity_pct_time(seconds, adjusted_goal, count),
        "productivity_pct_count": pct_raw,
        "productivity_pct_count_adjusted": pct_adj,
        "productivity_pct_abatement": pct_abatement,
        "productivity_pct_abatement_pcd": pct_abatement_pcd,
        "productivity_pct_abatement_idle": pct_abatement_idle,
        "recorded_at": (
            timezone.localtime(record.recorded_at).isoformat()
            if timezone.is_aware(record.recorded_at)
            else record.recorded_at.isoformat()
        ),
        "team": record.team,
        "location": record.location,
        "journey_shift": record.journey_shift,
        "leader_name": record.leader_name,
        "linked_agent": record.agent_id is not None,
        **{k: row_metrics_pure[k] for k in (
            "agent_sec_per_prot", "meta_sec_per_prot", "gap_sec_per_prot",
            "agent_pph", "meta_pph", "impact_pph",
        )},
        "meta_sec_per_prot_pcd": row_metrics_pcd.get("meta_sec_per_prot"),
        "gap_sec_per_prot_pcd": row_metrics_pcd.get("gap_sec_per_prot"),
        **_pcd_impact_fields(row_metrics_pcd),
        "meta_sec_per_prot_adjusted": row_metrics_adj.get("meta_sec_per_prot"),
        "gap_sec_per_prot_adjusted": row_metrics_adj.get("gap_sec_per_prot"),
    }


def _agent_etapa_metrics_map(qs: QuerySet) -> dict[tuple[str, str], dict]:
    """Métricas agregadas por (matricula, etapa) no queryset."""
    buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "seconds": 0, "goal": 0.0, "meta_seconds": 0}
    )
    for (matricula, _day, etapa), totals in _iter_shift_groups(qs).items():
        key = (matricula, etapa)
        bucket = buckets[key]
        bucket["count"] += totals["count"]
        bucket["seconds"] += totals["seconds"]
        bucket["goal"] += totals["goal"]
        bucket["meta_seconds"] += META_SHIFT_SECONDS
    result: dict[tuple[str, str], dict] = {}
    for key, totals in buckets.items():
        metrics = _compute_rate_metrics(
            totals["count"],
            totals["seconds"],
            totals["goal"],
            totals["meta_seconds"],
        )
        result[key] = {**metrics, "count": totals["count"]}
    return result


def _build_etapa_team_benchmarks(qs: QuerySet, team: str) -> dict[str, dict]:
    """Benchmark por etapa entre agentes do mesmo time (mesmo recorte de qs)."""
    team_qs = qs.filter(team=team) if team else qs
    agent_etapa = _agent_etapa_metrics_map(team_qs)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for (_mat, etapa), metrics in agent_etapa.items():
        grouped[etapa].append(metrics)

    benchmarks: dict[str, dict] = {}
    for etapa, agents in grouped.items():
        gaps = [float(m.get("gap_sec_per_prot") or 0) for m in agents]
        counts = [int(m.get("count") or 0) for m in agents]
        with_gap = sum(1 for gap in gaps if gap > 0)
        total = len(agents)
        benchmarks[etapa] = {
            "agents_total": total,
            "agents_with_gap": with_gap,
            "pct_agents_with_gap": round((with_gap / total) * 100, 1) if total else 0.0,
            "team_median_gap_spp": round(statistics.median(gaps), 2) if gaps else 0.0,
            "team_median_protocols": round(statistics.median(counts), 1) if counts else 0.0,
        }
    return benchmarks


def _estimate_impact_split(
    cause: str,
    impact: int,
    agent_gap: float,
    team_median_gap: float,
    pct_agents_with_gap: float,
) -> tuple[int, int]:
    if impact <= 0:
        return 0, 0
    if cause == CAUSE_AMBIENTE:
        return impact, 0
    if cause == CAUSE_EXECUCAO:
        return 0, impact
    if cause == CAUSE_MISTO:
        if agent_gap > 0:
            team_share = min(1.0, max(0.0, team_median_gap / agent_gap))
            if pct_agents_with_gap >= CAUSE_ENV_PCT:
                ambiente = int(round(impact * team_share))
                return ambiente, impact - ambiente
        return impact, 0
    return 0, 0


def _classify_etapa_cause(agent_row: dict, benchmark: dict) -> dict:
    agents_total = benchmark.get("agents_total") or 0
    agent_count = int(agent_row.get("count") or 0)
    pct_gap = float(benchmark.get("pct_agents_with_gap") or 0)
    team_median_gap = float(benchmark.get("team_median_gap_spp") or 0)
    team_median_protocols = float(benchmark.get("team_median_protocols") or 0)
    agent_gap = float(agent_row.get("gap_sec_per_prot") or 0)
    agent_gap_vs_team = round(agent_gap - team_median_gap, 2)
    impact = int(agent_row.get("impact_time_seconds") or 0)

    if agents_total < CAUSE_MIN_AGENTS or agent_count < CAUSE_MIN_PROTOCOLS:
        cause = CAUSE_INCONCLUSIVO
    elif pct_gap >= CAUSE_ENV_PCT:
        if agent_gap > team_median_gap + CAUSE_GAP_DELTA_SPP:
            cause = CAUSE_MISTO
        else:
            cause = CAUSE_AMBIENTE
    elif pct_gap <= CAUSE_EXEC_PCT and agent_gap > 0:
        cause = CAUSE_EXECUCAO
    elif agent_gap > team_median_gap + CAUSE_GAP_DELTA_SPP:
        cause = CAUSE_EXECUCAO
    else:
        cause = CAUSE_INCONCLUSIVO

    impact_ambiente_est, impact_execucao_est = _estimate_impact_split(
        cause, impact, agent_gap, team_median_gap, pct_gap
    )
    volume_atypical = (
        team_median_protocols > 0 and agent_count > team_median_protocols * 2
    )

    return {
        "cause": cause,
        "cause_label": CAUSE_LABELS[cause],
        "team_pct_with_gap": pct_gap,
        "team_agents_total": agents_total,
        "agent_gap_vs_team": agent_gap_vs_team,
        "impact_ambiente_est": impact_ambiente_est,
        "impact_execucao_est": impact_execucao_est,
        "volume_atypical": volume_atypical,
    }


def _enrich_etapa_rows_with_cause(rows: list[dict], benchmarks: dict[str, dict]) -> list[dict]:
    enriched = []
    for row in rows:
        etapa = row.get("etapa") or ""
        cause_fields = _classify_etapa_cause(row, benchmarks.get(etapa, {}))
        enriched.append({**row, **cause_fields})
    enriched.sort(key=lambda item: item.get("impact_time_seconds") or 0, reverse=True)
    return enriched


def _build_agent_by_etapa(qs: QuerySet) -> list[dict]:
    per_etapa: dict[str, dict] = defaultdict(
        lambda: {
            "count": 0,
            "seconds": 0,
            "goal": 0.0,
            "goal_pcd": 0.0,
            "goal_adjusted": 0.0,
            "meta_seconds": 0,
            "pct_sum": 0.0,
            "pct_sum_pcd": 0.0,
            "pct_sum_raw": 0.0,
            "groups": 0,
        }
    )
    for (_mat, _day, etapa), totals in _iter_shift_groups(qs, adjust_goal=False).items():
        bucket = per_etapa[etapa]
        bucket["count"] += totals["count"]
        bucket["seconds"] += totals["seconds"]
        bucket["goal"] += totals["goal"]
        bucket["meta_seconds"] += META_SHIFT_SECONDS
        bucket["groups"] += 1
        group_metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        pct_count = group_metrics.get("productivity_pct_count")
        if pct_count is not None:
            bucket["pct_sum_raw"] += pct_count
    for (_mat, _day, etapa), totals in _iter_shift_groups(
        qs, adjust_goal=True, pcd_only=True
    ).items():
        bucket = per_etapa[etapa]
        bucket["goal_pcd"] += totals["goal"]
        group_metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        pct_count = group_metrics.get("productivity_pct_count")
        if pct_count is not None:
            bucket["pct_sum_pcd"] += pct_count
    for (_mat, _day, etapa), totals in _iter_shift_groups(qs, adjust_goal=True).items():
        bucket = per_etapa[etapa]
        bucket["goal_adjusted"] += totals["goal"]
        group_metrics = _shift_group_metrics(totals["count"], totals["seconds"], totals["goal"])
        pct_count = group_metrics.get("productivity_pct_count")
        if pct_count is not None:
            bucket["pct_sum"] += pct_count
    results = []
    for etapa in sorted(per_etapa.keys(), key=lambda x: x.lower()):
        t = per_etapa[etapa]
        metrics = _compute_rate_metrics(t["count"], t["seconds"], t["goal"], t["meta_seconds"])
        metrics_pcd = _compute_rate_metrics(
            t["count"],
            t["seconds"],
            t["goal_pcd"],
            t["meta_seconds"],
        )
        metrics_adj = _compute_rate_metrics(
            t["count"],
            t["seconds"],
            t["goal_adjusted"],
            t["meta_seconds"],
        )
        pct_sum = round(t["pct_sum"], 2)
        pct_sum_pcd = round(t["pct_sum_pcd"], 2)
        pct_sum_raw = round(t["pct_sum_raw"], 2)
        pcd_pp = round(max(0.0, pct_sum_pcd - pct_sum_raw), 2)
        idle_pp = round(max(0.0, pct_sum - pct_sum_pcd), 2)
        normalized = round(pct_sum / t["groups"], 2) if t["groups"] > 0 else None
        results.append(
            {
                "etapa": etapa,
                "count": t["count"],
                "productivity_pct_sum": pct_sum,
                "productivity_pct_count": pct_sum,
                "productivity_pct_count_raw": pct_sum_raw,
                "productivity_pct_abatement": round(pcd_pp + idle_pp, 2),
                "productivity_pct_abatement_pcd": pcd_pp,
                "productivity_pct_abatement_idle": idle_pp,
                "productivity_pct_normalized": normalized,
                "severity": _severity(pct_sum),
                **metrics,
                "meta_sec_per_prot_pcd": metrics_pcd.get("meta_sec_per_prot"),
                "gap_sec_per_prot_pcd": metrics_pcd.get("gap_sec_per_prot"),
                **_pcd_impact_fields(metrics_pcd),
                "meta_sec_per_prot_adjusted": metrics_adj.get("meta_sec_per_prot"),
                "gap_sec_per_prot_adjusted": metrics_adj.get("gap_sec_per_prot"),
            }
        )
    results.sort(
        key=lambda item: item.get("impact_time_seconds_pcd")
        or item.get("impact_time_seconds")
        or 0,
        reverse=True,
    )
    return results


def _build_agent_by_hour(qs: QuerySet) -> list[dict]:
    hourly_pct_sums: dict[datetime, float] = defaultdict(float)
    for (_mat, hour), total in _agent_hourly_pct_sums(qs).items():
        if isinstance(hour, datetime):
            hourly_pct_sums[hour] += total

    matricula = qs.values_list("matricula_norm", flat=True).first()
    schedule_lookup: dict = {}
    if matricula:
        dates = {
            timezone.localtime(dt).date() if timezone.is_aware(dt) else dt.date()
            for dt in qs.values_list("recorded_at", flat=True)
            if dt
        }
        schedule_lookup = build_schedule_context_lookup({matricula}, dates)

    per_hour: dict[datetime, dict] = defaultdict(
        lambda: {
            "count": 0,
            "seconds": 0,
            "goal": 0.0,
            "goal_pcd": 0.0,
            "goal_adjusted": 0.0,
            "etapa_goals": set(),
            "pct_raw_sum": 0.0,
            "pct_pcd_sum": 0.0,
            "pct_adj_sum": 0.0,
        }
    )
    per_hour_etapa: dict[tuple[datetime, str], dict] = defaultdict(
        lambda: {
            "count": 0,
            "seconds": 0,
            "goal": 0.0,
            "goal_pcd": 0.0,
            "goal_adjusted": 0.0,
            "goal_added": False,
            "pct_raw_sum": 0.0,
            "pct_pcd_sum": 0.0,
            "pct_adj_sum": 0.0,
        }
    )
    discount_lookup = _cached_discount_lookup(qs)
    ociosidade_lookup = _cached_ociosidade_lookup(qs)
    logado_lookup = _cached_logado_lookup(qs)
    for row in qs.filter(stage_goal__gt=0).values(
        "recorded_at",
        "analysis_seconds",
        "analysis_count",
        "stage_goal",
        "matricula_norm",
        "etapa",
        "source",
    ):
        recorded_at = row["recorded_at"]
        if not recorded_at:
            continue
        day = recorded_at.date() if hasattr(recorded_at, "date") else recorded_at
        jornada = jornada_from_recorded_at(recorded_at) or day
        raw_goal = float(row["stage_goal"] or 0)
        discount_hours = discount_for_agent_day(
            discount_lookup, row["matricula_norm"], jornada
        )
        pcd_goal = adjust_stage_goal(raw_goal, discount_hours)
        mat_key = (row["matricula_norm"] or "").strip().lower()
        adjusted_goal = adjust_stage_goal_full(
            raw_goal,
            discount_hours,
            ociosidade_for_goal_adjustment(
                ociosidade_lookup,
                row["matricula_norm"],
                jornada,
            ),
            logado_seconds=logado_lookup.get((mat_key, jornada), 0),
        )
        hour = recorded_at.replace(minute=0, second=0, microsecond=0)
        bucket = per_hour[hour]
        bucket["count"] += row["analysis_count"] or 0
        bucket["seconds"] += row["analysis_seconds"] or 0
        etapa_key = (day, row["etapa"] or "")
        if etapa_key not in bucket["etapa_goals"]:
            bucket["etapa_goals"].add(etapa_key)
            bucket["goal"] += raw_goal
            bucket["goal_pcd"] += pcd_goal
            bucket["goal_adjusted"] += adjusted_goal
        analysis_seconds = row["analysis_seconds"] or 0
        analysis_count = row["analysis_count"] or 0
        pct_raw = productivity_pct_count(analysis_seconds, raw_goal, analysis_count)
        if pct_raw is not None:
            bucket["pct_raw_sum"] += pct_raw
        pct_pcd = productivity_pct_count(analysis_seconds, pcd_goal, analysis_count)
        if pct_pcd is not None:
            bucket["pct_pcd_sum"] += pct_pcd
        pct_adj = productivity_pct_count(analysis_seconds, adjusted_goal, analysis_count)
        if pct_adj is not None:
            bucket["pct_adj_sum"] += pct_adj
        etapa_name = row["etapa"] or ""
        etapa_bucket = per_hour_etapa[(hour, etapa_name)]
        etapa_bucket["count"] += analysis_count
        etapa_bucket["seconds"] += analysis_seconds
        if not etapa_bucket["goal_added"]:
            etapa_bucket["goal"] = raw_goal
            etapa_bucket["goal_pcd"] = pcd_goal
            etapa_bucket["goal_adjusted"] = adjusted_goal
            etapa_bucket["goal_added"] = True
        if pct_raw is not None:
            etapa_bucket["pct_raw_sum"] += pct_raw
        if pct_pcd is not None:
            etapa_bucket["pct_pcd_sum"] += pct_pcd
        if pct_adj is not None:
            etapa_bucket["pct_adj_sum"] += pct_adj
    results = []
    for hour in sorted(per_hour.keys()):
        t = per_hour[hour]
        groups = len(t["etapa_goals"]) or 1
        meta_seconds = META_SHIFT_SECONDS * groups
        metrics = _compute_rate_metrics(t["count"], t["seconds"], t["goal"], meta_seconds)
        metrics_pcd = _compute_rate_metrics(
            t["count"],
            t["seconds"],
            t["goal_pcd"],
            meta_seconds,
        )
        metrics_adj = _compute_rate_metrics(
            t["count"],
            t["seconds"],
            t["goal_adjusted"],
            meta_seconds,
        )
        # Volume por hora: soma qntd/meta dos registros (não count/Σmeta diária do blend).
        pct_sum_raw = round(float(t["pct_raw_sum"] or 0.0), 2)
        pct_sum_pcd = round(float(t["pct_pcd_sum"] or 0.0), 2)
        pct_sum = round(float(t["pct_adj_sum"] or 0.0), 2)
        if pct_sum <= 0 and hourly_pct_sums.get(hour):
            pct_sum = round(float(hourly_pct_sums.get(hour) or 0.0), 2)
        hour_pcd_pp = round(max(0.0, pct_sum_pcd - pct_sum_raw), 2)
        hour_idle_pp = round(max(0.0, pct_sum - pct_sum_pcd), 2)
        label = hour.isoformat()
        count = t["count"]
        etapas = []
        for (h, etapa), eb in per_hour_etapa.items():
            if h != hour:
                continue
            eb_count = eb["count"]
            eb_seconds = eb["seconds"]
            eb_goal = eb["goal"]
            eb_goal_pcd = eb["goal_pcd"] if eb["goal_pcd"] > 0 else eb_goal
            eb_metrics = _compute_rate_metrics(eb_count, eb_seconds, eb_goal, META_SHIFT_SECONDS)
            # Potencial = tempo ÷ meta s/prot (PCD no principal; puro no tooltip).
            eb_bal_pure = _potential_balance_fields(
                eb_count, eb_seconds, eb_goal, META_SHIFT_SECONDS
            )
            eb_bal = _potential_balance_fields(
                eb_count, eb_seconds, eb_goal_pcd, META_SHIFT_SECONDS
            )
            eb_raw = round(eb["pct_raw_sum"], 2)
            eb_pcd = round(eb["pct_pcd_sum"], 2)
            eb_adj = round(eb["pct_adj_sum"], 2)
            eb_pcd_pp = round(max(0.0, eb_pcd - eb_raw), 2)
            eb_idle_pp = round(max(0.0, eb_adj - eb_pcd), 2)
            etapas.append(
                {
                    "etapa": etapa,
                    "count": eb_count,
                    "total_seconds": eb_seconds,
                    "potential_protocols": eb_bal.get("potential_protocols"),
                    "potential_protocols_pure": eb_bal_pure.get("potential_protocols"),
                    "charged_seconds": eb_bal.get("charged_seconds"),
                    "time_balance_seconds": eb_bal.get("time_balance_seconds"),
                    "charged_hms": eb_bal.get("charged_hms"),
                    "time_balance_hms": eb_bal.get("time_balance_hms"),
                    "productivity_pct_count_raw": eb_raw,
                    "productivity_pct_count": eb_adj,
                    "productivity_pct_abatement": round(eb_pcd_pp + eb_idle_pp, 2),
                    "productivity_pct_abatement_pcd": eb_pcd_pp,
                    "productivity_pct_abatement_idle": eb_idle_pp,
                    "agent_sec_per_prot": eb_metrics.get("agent_sec_per_prot"),
                    "actual_seconds": eb_metrics.get("actual_seconds"),
                    "actual_hms": eb_metrics.get("actual_hms"),
                }
            )
        etapas.sort(key=lambda item: item["count"], reverse=True)
        # Potencial da hora = soma dos potenciais por etapa (metas distintas, sem média).
        etapa_pots = [
            float(e["potential_protocols"])
            for e in etapas
            if e.get("potential_protocols") is not None
        ]
        etapa_pots_pure = [
            float(e["potential_protocols_pure"])
            for e in etapas
            if e.get("potential_protocols_pure") is not None
        ]
        etapa_charged = [
            float(e["charged_seconds"])
            for e in etapas
            if e.get("charged_seconds") is not None
        ]
        etapa_balance = [
            float(e["time_balance_seconds"])
            for e in etapas
            if e.get("time_balance_seconds") is not None
        ]
        potential_protocols = round(sum(etapa_pots), 4) if etapa_pots else None
        potential_protocols_pure = (
            round(sum(etapa_pots_pure), 4) if etapa_pots_pure else None
        )
        charged_seconds = round(sum(etapa_charged), 4) if etapa_charged else None
        time_balance_seconds = round(sum(etapa_balance), 4) if etapa_balance else None
        if not etapas:
            bal_fallback = _potential_balance_fields(
                count,
                t["seconds"],
                float(t["goal_pcd"] or t["goal"] or 0) / max(groups, 1),
                META_SHIFT_SECONDS,
            )
            bal_pure_fallback = _potential_balance_fields(
                count,
                t["seconds"],
                float(t["goal"] or 0) / max(groups, 1),
                META_SHIFT_SECONDS,
            )
            potential_protocols = bal_fallback.get("potential_protocols")
            potential_protocols_pure = bal_pure_fallback.get("potential_protocols")
            charged_seconds = bal_fallback.get("charged_seconds")
            time_balance_seconds = bal_fallback.get("time_balance_seconds")
        # Campos de ritmo da hora (sem productivity_pct_count = count/Σmetas do blend).
        rate_fields = {
            k: metrics[k]
            for k in (
                "agent_sec_per_prot",
                "meta_sec_per_prot",
                "gap_sec_per_prot",
                "impact_time_seconds",
                "impact_time_hms",
                "surplus_time_seconds",
                "surplus_time_hms",
                "net_impact_time_seconds",
                "net_impact_time_hms",
                "agent_pph",
                "meta_pph",
                "productivity_pct",
                "impact_pph",
                "actual_seconds",
                "meta_seconds",
                "actual_hms",
                "meta_hms",
            )
            if k in metrics
        }
        results.append(
            {
                "hour": label,
                "period": label,
                "count": count,
                "total_seconds": t["seconds"],
                **rate_fields,
                # Volume: soma qntd/meta dos registros da hora (mesmo eixo do KPI 98,7%).
                "productivity_pct_sum": pct_sum,
                "productivity_pct_count": pct_sum,
                "productivity_pct_count_raw": pct_sum_raw,
                "productivity_pct_abatement": round(hour_pcd_pp + hour_idle_pp, 2),
                "productivity_pct_abatement_pcd": hour_pcd_pp,
                "productivity_pct_abatement_idle": hour_idle_pp,
                "stage_goal_raw_total": round(t["goal"], 2),
                "stage_goal_adjusted_total": round(t["goal_adjusted"], 2),
                "potential_protocols": potential_protocols if count > 0 else None,
                "potential_protocols_pure": potential_protocols_pure if count > 0 else None,
                "charged_seconds": charged_seconds if count > 0 else None,
                "time_balance_seconds": time_balance_seconds if count > 0 else None,
                "charged_hms": (
                    _format_hms(charged_seconds)
                    if count > 0 and charged_seconds is not None
                    else None
                ),
                "time_balance_hms": (
                    _format_signed_hms(time_balance_seconds)
                    if count > 0 and time_balance_seconds is not None
                    else None
                ),
                "hourly_forecast_pct": (
                    threshold_for_agent_hour(matricula, hour, schedule_lookup)
                    if matricula
                    else HOURLY_THRESHOLD
                ),
                "etapas": etapas,
                "meta_sec_per_prot_pcd": metrics_pcd.get("meta_sec_per_prot"),
                "gap_sec_per_prot_pcd": metrics_pcd.get("gap_sec_per_prot"),
                **_pcd_impact_fields(metrics_pcd),
                "meta_sec_per_prot_adjusted": metrics_adj.get("meta_sec_per_prot"),
                "gap_sec_per_prot_adjusted": metrics_adj.get("gap_sec_per_prot"),
            }
        )
    return results


def build_agent_detail(
    matricula: str,
    qs: QuerySet,
    etapa: str | None = None,
    hour: str | None = None,
) -> dict | None:
    agent_qs = qs.filter(matricula_norm=matricula.strip().lower())
    monitor_context_attrs = (
        "_pplid_civil_start",
        "_pplid_civil_end",
        "_pplid_jornada_start",
        "_pplid_jornada_end",
    )
    for attr in monitor_context_attrs:
        if hasattr(qs, attr):
            setattr(agent_qs, attr, getattr(qs, attr))
    if not agent_qs.exists():
        return None

    filter_options = {
        "etapas": sorted(agent_qs.values_list("etapa", flat=True).distinct()),
        "hours": [],
    }
    hour_labels = set()
    for recorded_at in agent_qs.values_list("recorded_at", flat=True):
        if recorded_at:
            h = recorded_at.replace(minute=0, second=0, microsecond=0)
            hour_labels.add(h.isoformat())
    filter_options["hours"] = sorted(hour_labels)

    filtered_qs = agent_qs
    if etapa:
        filtered_qs = filtered_qs.filter(etapa=etapa)
    if hour:
        try:
            hour_dt = datetime.fromisoformat(hour.replace("Z", "+00:00"))
            if timezone.is_naive(hour_dt):
                hour_dt = timezone.make_aware(hour_dt, timezone.get_current_timezone())
            filtered_qs = filtered_qs.filter(
                recorded_at__gte=hour_dt,
                recorded_at__lt=hour_dt + timedelta(hours=1),
            )
        except (TypeError, ValueError):
            pass
    for attr in monitor_context_attrs:
        if hasattr(agent_qs, attr):
            setattr(filtered_qs, attr, getattr(agent_qs, attr))

    first = agent_qs.first()
    summary_agg = filtered_qs.aggregate(
        total_seconds=Sum("analysis_seconds"),
        total_count=Sum("analysis_count"),
        total_goal=Sum("stage_goal"),
        count=Count("id"),
    )
    mat_key = matricula.strip().lower()
    pct_sum = _agent_period_pct_count(filtered_qs).get(mat_key)
    pct_normalized = _agent_count_normalized(filtered_qs).get(mat_key)
    pct_rate = _agent_period_pct(filtered_qs).get(mat_key)
    raw_pct_map, _, abatement_map, abatement_pcd_map, abatement_idle_map = (
        _agent_productivity_breakdown_maps(filtered_qs)
    )
    goal_totals = _agent_stage_goal_totals(filtered_qs).get(mat_key, {})
    rate_data = _agent_rate_maps(filtered_qs, adjust_goal=False).get(mat_key, {})
    rate_data_pcd = _agent_rate_maps(filtered_qs, adjust_goal=True, pcd_only=True).get(
        mat_key, {}
    )
    rate_data_adj = _agent_rate_maps(filtered_qs, adjust_goal=True).get(mat_key, {})
    tma_data = _agent_tma_maps(filtered_qs).get(mat_key, {})

    team = first.team if first else ""
    team_qs = qs.filter(team=team) if team else qs
    etapa_benchmarks = _build_etapa_team_benchmarks(team_qs, team)
    by_etapa = _enrich_etapa_rows_with_cause(_build_agent_by_etapa(filtered_qs), etapa_benchmarks)
    impact_ambiente_est = sum(row.get("impact_ambiente_est") or 0 for row in by_etapa)
    impact_execucao_est = sum(row.get("impact_execucao_est") or 0 for row in by_etapa)
    impact_bruto = sum(int(row.get("impact_time_seconds") or 0) for row in by_etapa)
    surplus_bruto = sum(int(row.get("surplus_time_seconds") or 0) for row in by_etapa)
    liquid = _liquid_impact_fields(impact_bruto, surplus_bruto)
    impact_bruto_pcd = sum(
        int(row.get("impact_time_seconds_pcd") or 0) for row in by_etapa
    )
    surplus_bruto_pcd = sum(
        int(row.get("surplus_time_seconds_pcd") or 0) for row in by_etapa
    )
    liquid_pcd = _pcd_impact_fields(
        {
            "impact_time_seconds": impact_bruto_pcd,
            "surplus_time_seconds": surplus_bruto_pcd,
            "net_impact_time_seconds": impact_bruto_pcd - surplus_bruto_pcd,
        }
    )

    # O tempo logado pertence à matrícula no período civil selecionado, não a
    # uma etapa/hora produtiva. Assim o total não muda ao abrir ou filtrar o modal.
    logado_totals = _agent_monitor_logado_totals(agent_qs)
    pace_snapshot = build_pace_for_agent(mat_key, filtered_qs, include_by_day=True)

    agent_eval = resolve_meta_from_teams(
        [t for t in filtered_qs.values_list("team", flat=True).distinct() if (t or "").strip()]
    )
    thr = agent_eval.applied_meta

    records = [
        serialize_record(
            r,
            _cached_ociosidade_lookup(filtered_qs),
            _cached_discount_lookup(filtered_qs),
            _cached_logado_lookup(filtered_qs),
        )
        for r in filtered_qs.order_by("-recorded_at")[:500]
    ]
    monitor_fields = _monitor_time_fields(
        mat_key,
        int(rate_data.get("actual_seconds") or 0),
        logado_totals,
    )
    pace_fields = public_pace_fields(pace_snapshot, include_by_day=True)
    op_fields = _enrich_with_operational_status(
        applied_meta=agent_eval.applied_meta,
        pace_fields=pace_fields,
        tempo_logado_seconds=monitor_fields.get("tempo_logado_seconds"),
        productivity_pct=pct_sum,
    )
    return {
        "matricula": matricula,
        "nome": first.agent_name if first else matricula,
        "team": team,
        "leader_name": first.leader_name if first else "",
        "location": first.location if first else "",
        "journey_shift": first.journey_shift if first else "",
        "filters_applied": {"etapa": etapa or "", "hour": hour or ""},
        "filter_options": filter_options,
        "summary": {
            "count": summary_agg["count"],
            "total_seconds": summary_agg["total_seconds"],
            "total_goal": float(summary_agg["total_goal"]) if summary_agg["total_goal"] else None,
            "total_protocols": summary_agg["total_count"] or 0,
            **rate_data,
            **liquid,
            "meta_sec_per_prot_pcd": rate_data_pcd.get("meta_sec_per_prot"),
            "gap_sec_per_prot_pcd": rate_data_pcd.get("gap_sec_per_prot"),
            **_potential_display_fields(rate_data, rate_data_pcd),
            **liquid_pcd,
            "meta_sec_per_prot_adjusted": rate_data_adj.get("meta_sec_per_prot"),
            "gap_sec_per_prot_adjusted": rate_data_adj.get("gap_sec_per_prot"),
            "productivity_pct_sum": pct_sum,
            "productivity_pct_count": pct_sum,
            "productivity_pct_count_raw": raw_pct_map.get(mat_key),
            **_productivity_abatement_payload(
                mat_key, abatement_map, abatement_pcd_map, abatement_idle_map
            ),
            "stage_goal_raw_total": round(goal_totals.get("goal_raw", 0), 2) if goal_totals else None,
            "stage_goal_adjusted_total": round(goal_totals.get("goal_adjusted", 0), 2) if goal_totals else None,
            "productivity_pct_normalized": pct_normalized,
            "productivity_pct_rate": pct_rate,
            "productivity_pct": pct_rate,
            "severity": _severity(pct_sum, thr),
            "below_daily_threshold": _agent_below_daily(pct_sum, thr),
            "below_operational_threshold": (
                False
                if op_fields.get("operational_status")
                in ("warming_up", "data_delayed", "not_started")
                else (
                    None
                    if op_fields.get("operational_status") == "unknown"
                    or op_fields.get("status_phase") == "unavailable"
                    else is_priority_negative(op_fields.get("operational_status"))
                )
            ),
            "applied_meta": agent_eval.applied_meta,
            "evaluation_type": agent_eval.evaluation_type,
            **_meta_payload(agent_eval, pct_sum),
            "impact_ambiente_est": impact_ambiente_est,
            "impact_execucao_est": impact_execucao_est,
            **monitor_fields,
            **pace_fields,
            **op_fields,
            "projected_closing_pct": agent_projected_closing_pct(
                {**pace_fields, **op_fields}, agent_eval.applied_meta
            ),
            **tma_data,
        },
        "by_etapa": by_etapa,
        "by_hour": _build_agent_by_hour(filtered_qs),
        "records": records,
    }
