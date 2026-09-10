"""Capacity por hora: perfil de recebimento (monitoramento SLA) × volume projetado."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING

from django.db.models import Sum
from django.db.models.functions import ExtractHour

from apps.dimensoes_processos.services.capacity_hourly_profiles import (
    load_capacity_hourly_profile_snapshots,
)
from apps.monitoramento_sla.models import SlaUtilConsolidado

ZERO = Decimal("0")
HUNDRED = Decimal("100")
HOURS = range(24)
META_HOURS = Decimal("5.5")
DEFAULT_PROFILE_DAYS = 30
UNIFORM_PCT = Decimal("100") / Decimal("24")


def default_profile_window(on_date: date, *, days: int = DEFAULT_PROFILE_DAYS) -> tuple[date, date]:
    """Últimos ``days`` dias inclusive até ``on_date``."""
    return on_date - timedelta(days=days - 1), on_date


def _serialize_decimal(value: Decimal, places: str = "0.01") -> str:
    return str(value.quantize(Decimal(places)))


def _pct(volume: Decimal, total: Decimal) -> Decimal:
    if total <= ZERO:
        return ZERO
    return volume / total * HUNDRED


def build_workflow_hourly_profiles(
    workflow_keys: set[tuple[int, int]],
    *,
    on_date: date,
    profile_from: date,
    profile_to: date,
) -> tuple[dict[tuple[int, int], list[dict]], list[dict], dict]:
    """Perfil 0–23 confiável com fallback trimestral auditável.

    Horários marcados como indisponíveis nunca entram na curva. A cascata é:
    histórico recente C+WF -> trimestre/weekday C+WF -> cliente -> uniforme.
    """
    if not workflow_keys:
        return {}, [], {
            "status": "unavailable",
            "source_counts": {},
            "unavailable_hour_volume": 0,
            "by_workflow": {},
        }

    cliente_ids = {key[0] for key in workflow_keys}
    workflow_ids = {key[1] for key in workflow_keys}

    recent_volumes: dict[tuple[int, int, int], Decimal] = {}
    unavailable_by_workflow: dict[tuple[int, int], Decimal] = {}
    rows = (
        SlaUtilConsolidado.objects.filter(
            data_cadastro__gte=profile_from,
            data_cadastro__lte=profile_to,
            id_cliente__in=cliente_ids,
            id_workflow__in=workflow_ids,
        )
        .annotate(hour=ExtractHour("hora_cadastro"))
        .values("id_cliente", "id_workflow", "hora_cadastro_fonte", "hour")
        .annotate(volume=Sum("quantidade"))
    )
    for row in rows:
        wf_key = (row["id_cliente"], row["id_workflow"])
        if wf_key not in workflow_keys:
            continue
        volume = Decimal(row["volume"] or 0)
        if (
            row["hora_cadastro_fonte"] != SlaUtilConsolidado.HORA_FONTE_REAL
            or row["hour"] is None
        ):
            unavailable_by_workflow[wf_key] = (
                unavailable_by_workflow.get(wf_key, ZERO) + volume
            )
            continue
        recent_volumes[(wf_key[0], wf_key[1], int(row["hour"]))] = volume

    profiles: dict[tuple[int, int], list[dict]] = {}
    missing: list[dict] = []
    metadata: dict[tuple[int, int], dict] = {}

    def build_profile(hour_volumes: list[Decimal]) -> list[dict]:
        total = sum(hour_volumes, ZERO)
        result = []
        for hour, vol in zip(HOURS, hour_volumes):
            share = vol / total if total > ZERO else UNIFORM_PCT / HUNDRED
            result.append(
                {
                    "hour": hour,
                    "volume": int(vol),
                    "share": str(share),
                    "share_pct": _serialize_decimal(_pct(vol, total) if total > ZERO else UNIFORM_PCT),
                }
            )
        return result

    for wf_key in workflow_keys:
        cliente_id, workflow_id = wf_key
        hour_volumes = [recent_volumes.get((cliente_id, workflow_id, h), ZERO) for h in HOURS]
        total = sum(hour_volumes, ZERO)
        if total > ZERO:
            profiles[wf_key] = build_profile(hour_volumes)
            metadata[wf_key] = {
                "source": "recent_workflow",
                "window_from": profile_from.isoformat(),
                "window_to": profile_to.isoformat(),
                "trusted_volume": int(total),
                "unavailable_hour_volume": int(unavailable_by_workflow.get(wf_key, ZERO)),
            }

    unresolved = workflow_keys - profiles.keys()
    quarter_from = quarter_to = None
    snapshot_profiles, quarter = load_capacity_hourly_profile_snapshots(
        set(unresolved),
        on_date=on_date,
    )
    if quarter:
        quarter_from, quarter_to = quarter
    for wf_key, snapshot in snapshot_profiles.items():
        hours = [Decimal(value) for value in snapshot["hours"]]
        profiles[wf_key] = build_profile(hours)
        metadata[wf_key] = {
            "source": snapshot["source"],
            "window_from": quarter_from.isoformat() if quarter_from else None,
            "window_to": quarter_to.isoformat() if quarter_to else None,
            "trusted_volume": snapshot["trusted_volume"],
            "unavailable_hour_volume": int(unavailable_by_workflow.get(wf_key, ZERO)),
        }
        unresolved.discard(wf_key)

    for wf_key in unresolved:
        cliente_id, workflow_id = wf_key
        profiles[wf_key] = build_profile([ZERO] * 24)
        metadata[wf_key] = {
            "source": "uniform_24h",
            "window_from": None,
            "window_to": None,
            "trusted_volume": 0,
            "unavailable_hour_volume": int(unavailable_by_workflow.get(wf_key, ZERO)),
        }
        missing.append(
            {
                "id_cliente": cliente_id,
                "id_workflow": workflow_id,
                "fallback": "uniform_24h",
            }
        )

    source_counts: dict[str, int] = {}
    for item in metadata.values():
        source = item["source"]
        source_counts[source] = source_counts.get(source, 0) + 1
    fallback_count = sum(
        count for source, count in source_counts.items() if source != "recent_workflow"
    )
    unavailable_volume = sum(unavailable_by_workflow.values(), ZERO)
    return profiles, missing, {
        "status": "estimated" if fallback_count or unavailable_volume > ZERO else "reliable",
        "source_counts": source_counts,
        "fallback_workflow_count": fallback_count,
        "unavailable_hour_volume": int(unavailable_volume),
        "quarter_from": quarter_from.isoformat() if quarter_from else None,
        "quarter_to": quarter_to.isoformat() if quarter_to else None,
        "quarterly_snapshot_missing": bool(unresolved) and quarter is None,
        "by_workflow": metadata,
    }


def build_hourly_capacity(
    *,
    on_date: date,
    workflow_totals: dict[tuple[int, int], Decimal],
    workflow_meta: dict[tuple[int, int], dict],
    demand_by_stage: dict[int, Decimal],
    demand_by_stage_wf: dict[int, dict[tuple[int, int], Decimal]],
    stage_results: list[dict],
    profiles: dict[tuple[int, int], list[dict]],
    profile_from: date,
    profile_to: date,
    workflows_without_profile: list[dict],
    profile_quality: dict | None = None,
) -> dict:
    """Calcula o FTE simultâneo por hora conforme o perfil de entrada."""
    stage_meta = {row["id_etapa"]: row for row in stage_results}

    dimensioned_wf_keys: set[tuple[int, int]] = set()
    for etapa_id, wf_demands in demand_by_stage_wf.items():
        stage = stage_meta.get(etapa_id)
        if not stage or stage.get("status") != "dimensionada":
            continue
        for wf_key, wf_demand in wf_demands.items():
            if wf_demand > ZERO:
                dimensioned_wf_keys.add(wf_key)

    global_volume_by_hour = [ZERO] * 24
    global_demand_by_hour = [ZERO] * 24
    capacity_hora_by_hour = [ZERO] * 24

    stage_meta_dia: dict[int, Decimal] = {}
    demand_by_workflow: dict[tuple[int, int], Decimal] = {}
    fte_by_workflow: dict[tuple[int, int], Decimal] = {}

    for etapa_id, wf_demands in demand_by_stage_wf.items():
        stage = stage_meta.get(etapa_id)
        if not stage or stage.get("status") != "dimensionada":
            continue
        meta = Decimal(str(stage["meta_dia"]))
        if meta <= ZERO:
            continue
        meta_hora = meta / META_HOURS
        stage_meta_dia[etapa_id] = meta
        for wf_key, wf_demand in wf_demands.items():
            if wf_demand <= ZERO:
                continue

            demand_by_workflow[wf_key] = (
                demand_by_workflow.get(wf_key, ZERO) + wf_demand
            )
            fte_by_workflow[wf_key] = (
                fte_by_workflow.get(wf_key, ZERO) + wf_demand / meta_hora
            )

    agents_by_hour = [0] * 24
    # Preserva a ordem de entrada dos volumes para manter a mesma acumulação
    # Decimal do contrato anterior.
    for wf_key, daily_volume in workflow_totals.items():
        if daily_volume <= ZERO or wf_key not in dimensioned_wf_keys:
            continue
        profile = profiles.get(wf_key)
        if not profile:
            continue
        for item in profile:
            hour = item["hour"]
            share = Decimal(item["share"])
            global_volume_by_hour[hour] += daily_volume * share

    for wf_key, workflow_demand in demand_by_workflow.items():
        profile = profiles.get(wf_key)
        if not profile:
            continue
        workflow_fte = fte_by_workflow[wf_key]
        for item in profile:
            hour = item["hour"]
            share = Decimal(item["share"])
            global_demand_by_hour[hour] += workflow_demand * share
            capacity_hora_by_hour[hour] += workflow_fte * share

    for hour in HOURS:
        capacity_h = capacity_hora_by_hour[hour]
        if capacity_h > ZERO:
            agents_by_hour[hour] = int(
                capacity_h.to_integral_value(rounding=ROUND_CEILING)
            )

    total_daily_volume = sum(
        (volume for wf_key, volume in workflow_totals.items() if wf_key in dimensioned_wf_keys),
        ZERO,
    )
    total_daily_agents = sum(row.get("agentes_necessarios") or 0 for row in stage_results if row.get("status") == "dimensionada")
    # Recalcula a partir da demanda e da meta sem usar o FTE serializado por
    # etapa. Assim, o alvo horario e exatamente o mesmo total diario bruto.
    daily_exact_fte = sum(
        (
            demand_by_stage.get(etapa_id, ZERO) / meta
            for etapa_id, meta in stage_meta_dia.items()
        ),
        ZERO,
    )
    expected_fte_hours = daily_exact_fte * META_HOURS
    hourly_fte_total = sum(capacity_hora_by_hour, ZERO)
    hourly_fte_delta = hourly_fte_total - expected_fte_hours
    peak_hour = max(HOURS, key=lambda hour: capacity_hora_by_hour[hour])
    peak_capacity_hora = capacity_hora_by_hour[peak_hour]
    peak_agents_hour = agents_by_hour[peak_hour]

    by_hour = []
    for hour in HOURS:
        vol = global_volume_by_hour[hour]
        share_pct = _pct(vol, total_daily_volume) if total_daily_volume > ZERO else ZERO
        capacity_h = capacity_hora_by_hour[hour]
        by_hour.append(
            {
                "hour": hour,
                "projected_volume": _serialize_decimal(vol),
                "volume_share_pct": _serialize_decimal(share_pct),
                "demand": _serialize_decimal(global_demand_by_hour[hour]),
                "fte": _serialize_decimal(capacity_h, "0.0001"),
                "capacity_hora": _serialize_decimal(capacity_h, "0.0001"),
                "agents_fractional": _serialize_decimal(capacity_h, "0.0001"),
                "agents_required": agents_by_hour[hour],
            }
        )

    # A soma das faixas é FTE-hora: FTE diário × 5,5 horas produtivas.
    # Depois de serializar, concentra o resíduo no pico para fechar esse alvo.
    serialized_target = expected_fte_hours.quantize(Decimal("0.0001"))
    serialized_total = sum((Decimal(row["fte"]) for row in by_hour), ZERO)
    serialization_delta = serialized_target - serialized_total
    if serialization_delta:
        peak_row = by_hour[peak_hour]
        adjusted_peak = Decimal(peak_row["fte"]) + serialization_delta
        adjusted_peak_serialized = _serialize_decimal(adjusted_peak, "0.0001")
        peak_row["fte"] = adjusted_peak_serialized
        peak_row["capacity_hora"] = adjusted_peak_serialized
        peak_row["agents_fractional"] = adjusted_peak_serialized
        peak_row["agents_required"] = (
            int(adjusted_peak.to_integral_value(rounding=ROUND_CEILING))
            if adjusted_peak > ZERO
            else 0
        )
    hourly_fte_total = sum((Decimal(row["fte"]) for row in by_hour), ZERO)
    hourly_fte_delta = hourly_fte_total - serialized_target
    peak_capacity_hora = Decimal(by_hour[peak_hour]["fte"])
    peak_agents_hour = by_hour[peak_hour]["agents_required"]

    enriched_missing = []
    for item in workflows_without_profile:
        wf_key = (item["id_cliente"], item["id_workflow"])
        meta = workflow_meta.get(wf_key, {})
        enriched_missing.append(
            {
                "id_cliente": item["id_cliente"],
                "id_workflow": item["id_workflow"],
                "cliente_nome": meta.get("cliente_nome"),
                "workflow_nome": meta.get("workflow_nome"),
                "fallback": "uniform",
            }
        )

    return {
        "scope": "dimensionada",
        "profile_from": profile_from.isoformat(),
        "profile_to": profile_to.isoformat(),
        "profile_days": (profile_to - profile_from).days + 1,
        "hour_field": "hora_cadastro",
        "workflows_without_profile": enriched_missing,
        "workflows_without_profile_count": len(enriched_missing),
        "profile_quality": {
            key: value
            for key, value in (profile_quality or {}).items()
            if key != "by_workflow"
        },
        "daily_agents_required": total_daily_agents,
        "peak_hour": peak_hour,
        "peak_hourly_fte": _serialize_decimal(peak_capacity_hora, "0.0001"),
        "peak_capacity_hora": _serialize_decimal(peak_capacity_hora, "0.0001"),
        "peak_agents_hour": peak_agents_hour,
        "legacy_peak_hour": peak_hour,
        "legacy_peak_agents": peak_agents_hour,
        "fractional_peak_hour": peak_hour,
        "fractional_peak_agents": _serialize_decimal(peak_capacity_hora, "0.0001"),
        "dimensioned_workflow_count": len(dimensioned_wf_keys),
        "daily_exact_fte": _serialize_decimal(daily_exact_fte, "0.0001"),
        "productive_hours_per_fte": _serialize_decimal(META_HOURS, "0.0"),
        "expected_fte_hours": _serialize_decimal(expected_fte_hours, "0.0001"),
        "hourly_fte_total": _serialize_decimal(hourly_fte_total, "0.0001"),
        "hourly_fte_delta": _serialize_decimal(hourly_fte_delta, "0.0001"),
        "hourly_fte_reconciled": abs(hourly_fte_delta) <= Decimal("0.0001"),
        "by_hour": by_hour,
        "guidance": [
            "Volume e demanda horária consideram apenas workflows das etapas dimensionadas.",
            "meta_hora = meta_dia ÷ 5,5 horas produtivas.",
            "fte_simultâneo_hora = Σ (demanda_etapa_h ÷ meta_hora_etapa), sem arredondamento para pessoas.",
            "A soma das 24 faixas representa FTE-hora e reconcilia com FTE diário × 5,5.",
            "Curva DAX esperada: Volume Hora Contrato × derivação 60d ÷ meta_hora (referência PBI).",
        ],
    }
