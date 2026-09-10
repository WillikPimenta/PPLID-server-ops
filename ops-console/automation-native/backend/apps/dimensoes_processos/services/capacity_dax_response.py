"""Monta resposta API do Capacity DAX + comparativo com legado."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from apps.dimensoes_processos.services.capacity_dax import (
    HOURS,
    aggregate_capacity_rows,
    compute_capacity_grain,
)

ZERO = Decimal("0")


def _serialize_decimal(value: Decimal, places: str = "0.0001") -> str:
    return str(value.quantize(Decimal(places)))


def build_dax_capacity_response(on_date: date, *, scenario_id: str = "planejamento") -> dict:
    scenario = scenario_id.strip().casefold() if scenario_id else "planejamento"
    grain_rows = compute_capacity_grain(on_date, scenario_id=scenario)
    agg = aggregate_capacity_rows(grain_rows)

    total_esperada = agg["total_capacity_esperada"]
    total_recebida = agg["total_capacity_recebida"]
    peak_hour = agg["peak_hour_esperada"]
    peak_capacity = agg["peak_capacity_esperada"]

    results = []
    for etapa_id in sorted(agg["by_stage"].keys(), key=lambda eid: agg["by_stage"][eid]["etapa_nome"].casefold()):
        stage = agg["by_stage"][etapa_id]
        results.append(
            {
                "id_etapa": stage["id_etapa"],
                "etapa_nome": stage["etapa_nome"],
                "familia": stage["familia"],
                "meta_dia": stage["meta_dia"],
                "meta_hora": stage["meta_hora"],
                "status": stage["status"],
                "capacity_esperada": _serialize_decimal(stage["capacity_esperada"]),
                "capacity_recebida": _serialize_decimal(stage["capacity_recebida"]),
            }
        )

    by_familia = []
    for familia, item in sorted(
        agg["by_familia"].items(),
        key=lambda pair: (-pair[1]["capacity_esperada"], pair[0].casefold()),
    ):
        share = (
            item["capacity_esperada"] / total_esperada * Decimal("100")
            if total_esperada > ZERO
            else ZERO
        )
        by_familia.append(
            {
                "familia": familia or "—",
                "stage_count": len(item["stage_ids"]),
                "capacity_esperada": _serialize_decimal(item["capacity_esperada"]),
                "capacity_recebida": _serialize_decimal(item["capacity_recebida"]),
                "share_pct": _serialize_decimal(share, "0.01"),
            }
        )

    by_hour = []
    for hour in HOURS:
        item = agg["by_hour"][hour]
        by_hour.append(
            {
                "hour": hour,
                "capacity_esperada": _serialize_decimal(item["capacity_esperada"]),
                "capacity_recebida": _serialize_decimal(item["capacity_recebida"]),
            }
        )

    derivation_method = "median_60d" if scenario == "planejamento" else "average_60d"
    volume_source = (
        "Volume Hora Contrato (Documentoscopia · curva 3 meses consolidado SLA)"
        if scenario == "planejamento"
        else "Volume Hora Contrato (curva 3 meses consolidado SLA)"
    )

    return {
        "model": "dax_pbi",
        "label": "DAX / Power BI (capacity fracionada)",
        "availability": "available",
        "units": {
            "capacity_esperada_total": "agent_hours_equivalent",
            "capacity_recebida_total": "agent_hours_equivalent",
            "peak_capacity_esperada": "fractional_people",
        },
        "summary": {
            "capacity_esperada_total": _serialize_decimal(total_esperada),
            "capacity_recebida_total": _serialize_decimal(total_recebida),
            "peak_hour_esperada": peak_hour,
            "peak_capacity_esperada": _serialize_decimal(peak_capacity),
            "stage_count": len(results),
            "grain_row_count": len(grain_rows),
            "derivation_lookback_days": 60,
            "derivation_method": derivation_method,
            "meta_hours_divisor": "5.5",
            "volume_source": volume_source,
            "product_scope": "Documentoscopia" if scenario == "planejamento" else None,
        },
        "results": results,
        "by_familia": by_familia,
        "by_hour": by_hour,
    }


def build_capacity_comparative(*, legacy_payload: dict, dax_payload: dict) -> dict:
    legacy_agents = int(legacy_payload["summary"]["agents_required"])
    dax_peak_hour = dax_payload["summary"]["peak_hour_esperada"]
    scenario = (legacy_payload.get("scenario") or {}).get("id") or "planejamento"

    legacy_hourly = legacy_payload.get("hourly") or {}
    legacy_by_hour = legacy_hourly.get("by_hour") or []
    legacy_peak_hour = legacy_hourly.get("peak_hour")
    legacy_peak_agents = int(legacy_hourly.get("peak_agents_hour") or 0)
    if not legacy_peak_agents and legacy_by_hour:
        legacy_peak_agents = max(int(row.get("agents_required") or 0) for row in legacy_by_hour)
        legacy_peak_hour = max(
            legacy_by_hour,
            key=lambda row: int(row.get("agents_required") or 0),
        ).get("hour")
    legacy_hourly_sum = sum(int(row.get("agents_required") or 0) for row in legacy_by_hour)

    return {
        "status": "diagnostic_only",
        "units": {
            "legacy_agents_required": "people_per_day",
            "legacy_peak_hourly_agents": "people_in_hour_rounded",
            "legacy_hourly_agents_sum": "people_hours_rounded",
            "dax_capacity_esperada_total": "agent_hours_equivalent",
            "dax_capacity_recebida_total": "agent_hours_equivalent",
            "dax_peak_capacity_esperada": "fractional_people",
        },
        "legacy": {
            "agents_required": legacy_agents,
            "peak_hour": legacy_peak_hour,
            "peak_hourly_agents": legacy_peak_agents,
            "hourly_agents_sum": legacy_hourly_sum,
            "method": "Σ(demanda_h÷meta_hora) por etapa · ceil 1×/faixa · meta_hora=meta_dia÷5,5",
        },
        "dax": {
            "capacity_esperada_total": dax_payload["summary"]["capacity_esperada_total"],
            "capacity_recebida_total": dax_payload["summary"]["capacity_recebida_total"],
            "peak_hour": dax_peak_hour,
            "peak_capacity_esperada": dax_payload["summary"]["peak_capacity_esperada"],
            "method": (
                "C+WF+Etapa+Hora · meta÷5,5 · derivação mediana 60d · Volume Hora Contrato · Documentoscopia"
                if scenario == "planejamento"
                else "C+WF+Etapa+Hora · meta÷5,5 · derivação média 60d · Volume Hora Contrato"
            ),
        },
        "comparison": {
            "total_delta": None,
            "total_delta_pct": None,
            "comparable": False,
            "reason": (
                "O total DAX soma capacity fracionada nas 24 faixas horarias; "
                "o legado representa headcount diario inteiro. As unidades nao podem ser subtraidas."
            ),
        },
        "guidance": [
            "Total DAX = soma das capacities fracionadas no grão horário (não é headcount inteiro por etapa).",
            "Pico horário DAX é a métrica mais próxima do legado para escala intraday.",
            "Capacity recebida só existe no modelo DAX (combine + consolidado × % do dia).",
            "Use este painel até homologação numérica contra export PBI.",
        ],
    }
