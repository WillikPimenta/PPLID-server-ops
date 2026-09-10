"""Drill-down sob demanda da distribuicao do Capacity oficial."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_CEILING

from django.db.models import Sum
from django.db.models.functions import ExtractHour

from apps.dimensoes_processos.services.capacity import _active_metas, calculate_daily_capacity
from apps.dimensoes_processos.services.capacity_hourly import (
    META_HOURS,
    build_workflow_hourly_profiles,
    default_profile_window,
)
from apps.monitoramento_sla.models import SlaUtilConsolidado


ZERO = Decimal("0")
HOURS = range(24)
MAX_PAGE_SIZE = 100
DRILLDOWN_LEVELS = {"family", "workflow", "stage"}


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _serialize(value: Decimal, places: str = "0.01") -> str:
    return str(value.quantize(Decimal(places)))


def _hourly_metric_contract() -> dict:
    return {
        "version": "2026-08-24-simultaneous-fte-v3",
        "status": "simultaneous_fte",
        "primary_metric": "expected_hourly_fte",
        "rounding_scope": "none_for_fte",
        "integer_reference": None,
        "unique_people_supported": False,
        "workflow_sharing_within_family": "not_assumed",
        "definitions": {
            "expected_hourly_fte": "sum(expected_derived_volume / goal_per_hour)",
            "received_hourly_fte": "sum(received_derived_volume / goal_per_hour)",
        },
        "notes": [
            "FTE horario e a necessidade simultanea na faixa selecionada.",
            "Meta por hora = meta diaria / 5,5 horas produtivas; nao ha arredondamento para pessoas.",
            "Um workflow pode aparecer em mais de uma familia por suas etapas; isso nao prova compartilhamento de pessoas.",
        ],
    }


def _actual_received_by_workflow_hour(
    workflow_keys: set[tuple[int, int]], on_date: date, hour: int
) -> dict[tuple[int, int], Decimal]:
    if not workflow_keys:
        return {}
    cliente_ids = {key[0] for key in workflow_keys}
    workflow_ids = {key[1] for key in workflow_keys}
    rows = (
        SlaUtilConsolidado.objects.filter(
            data_cadastro=on_date,
            id_cliente__in=cliente_ids,
            id_workflow__in=workflow_ids,
            hora_cadastro_fonte=SlaUtilConsolidado.HORA_FONTE_REAL,
        )
        .annotate(hour=ExtractHour("hora_cadastro"))
        .filter(hour=hour)
        .values("id_cliente", "id_workflow")
        .annotate(volume=Sum("quantidade"))
    )
    return {
        (row["id_cliente"], row["id_workflow"]): Decimal(row["volume"] or 0)
        for row in rows
        if (row["id_cliente"], row["id_workflow"]) in workflow_keys
    }


def calculate_hourly_capacity_drilldown(
    on_date: date,
    *,
    hour: int,
    level: str,
    familia: str | None = None,
    id_cliente: int | None = None,
    id_workflow: int | None = None,
    id_etapa: int | None = None,
    profile_from: date | None = None,
    profile_to: date | None = None,
    scenario_id: str = "planejamento",
    manual_overrides: dict | None = None,
) -> dict:
    """Recorte sob demanda de uma hora no caminho familia -> workflow -> etapa."""
    if hour not in HOURS:
        raise ValueError("hour deve estar entre 0 e 23.")
    if level not in DRILLDOWN_LEVELS:
        raise ValueError("level deve ser family, workflow ou stage.")
    if level == "workflow" and not familia:
        raise ValueError("familia e obrigatoria para level=workflow.")
    if level == "stage" and id_workflow is None:
        raise ValueError("id_workflow e obrigatorio para level=stage.")
    if profile_from is None or profile_to is None:
        profile_from, profile_to = default_profile_window(on_date)
    if profile_from > profile_to:
        raise ValueError("profile_from nao pode ser posterior a profile_to.")

    daily = calculate_daily_capacity(
        on_date,
        include_details=True,
        include_hourly=False,
        include_dax=False,
        include_quarterly=False,
        scenario_id=scenario_id,
        manual_overrides=manual_overrides,
    )
    stage_by_id = {row["id_etapa"]: row for row in daily["results"]}
    combinations = [
        {**stage, "workflow_volume": workflow["volume"]}
        for workflow in daily["by_workflow"]
        for stage in workflow.get("stages", [])
        if (familia is None or stage.get("familia") == familia)
        and (id_cliente is None or stage["id_cliente"] == id_cliente)
        and (id_workflow is None or stage["id_workflow"] == id_workflow)
        and (id_etapa is None or stage["id_etapa"] == id_etapa)
    ]
    workflow_keys = {(row["id_cliente"], row["id_workflow"]) for row in combinations}
    profiles, missing_profiles, profile_quality = build_workflow_hourly_profiles(
        workflow_keys,
        on_date=on_date,
        profile_from=profile_from,
        profile_to=profile_to,
    )
    received_by_workflow = _actual_received_by_workflow_hour(workflow_keys, on_date, hour)
    profile_meta = profile_quality.get("by_workflow", {})
    metas = _active_metas(on_date)

    enriched = []
    for row in combinations:
        workflow_key = (row["id_cliente"], row["id_workflow"])
        profile = profiles.get(workflow_key)
        if not profile:
            continue
        share = _decimal(profile[hour]["share"])
        expected = _decimal(row["workflow_volume"]) * share
        received = received_by_workflow.get(workflow_key)
        derivation_pct = (
            _decimal(row["demand"]) / _decimal(row["workflow_volume"]) * Decimal("100")
            if _decimal(row["workflow_volume"]) > ZERO
            else ZERO
        )
        expected_derived = expected * derivation_pct / Decimal("100")
        received_derived = (
            received * derivation_pct / Decimal("100") if received is not None else None
        )
        stage = stage_by_id.get(row["id_etapa"], {})
        meta_day = _decimal(stage.get("meta_dia"))
        meta_effective_day = meta_day if meta_day > ZERO else None
        meta_effective_hour = meta_effective_day / META_HOURS if meta_effective_day else None
        expected_fte = (
            expected_derived / meta_effective_hour
            if row.get("status") == "dimensionada" and meta_effective_hour
            else ZERO
        )
        received_fte = (
            received_derived / meta_effective_hour
            if received_derived is not None and row.get("status") == "dimensionada" and meta_effective_hour
            else None
        )
        candidates = metas.get(row["id_etapa"], [])
        meta_candidate = candidates[0] if len(candidates) == 1 else None
        enriched.append({
            **row,
            "expected_volume": expected,
            "received_volume": received,
            "derivation_pct": derivation_pct,
            "expected_derived_volume": expected_derived,
            "received_derived_volume": received_derived,
            "goal_per_day": meta_effective_day,
            "goal_per_hour": meta_effective_hour,
            "expected_hourly_fte": expected_fte,
            "received_hourly_fte": received_fte,
            "expected_simultaneous_fte": expected_fte,
            "received_simultaneous_fte": received_fte,
            "profile_source": profile_meta.get(workflow_key, {"source": "unknown"}),
            "meta_valid_from": meta_candidate.data_inicio.isoformat() if meta_candidate else None,
            "meta_valid_to": meta_candidate.data_fim.isoformat() if meta_candidate and meta_candidate.data_fim else None,
        })

    def group_key(row: dict):
        if level == "family":
            return (row.get("familia"),)
        if level == "workflow":
            return (row["id_cliente"], row["id_workflow"])
        return (row["id_cliente"], row["id_workflow"], row["id_etapa"])

    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in enriched:
        groups[group_key(row)].append(row)

    result_rows = []
    for key, members in groups.items():
        first = members[0]
        expected_by_workflow = {}
        received_workflows = {}
        for member in members:
            workflow_key = (member["id_cliente"], member["id_workflow"])
            expected_by_workflow[workflow_key] = member["expected_volume"]
            received_workflows[workflow_key] = member["received_volume"]
        expected = sum(expected_by_workflow.values(), ZERO)
        received_values = [value for value in received_workflows.values() if value is not None]
        received = sum(received_values, ZERO) if received_values else None
        expected_derived = sum((item["expected_derived_volume"] for item in members), ZERO)
        received_derived_values = [item["received_derived_volume"] for item in members if item["received_derived_volume"] is not None]
        received_derived = sum(received_derived_values, ZERO) if received_derived_values else None
        expected_fte = sum((item["expected_simultaneous_fte"] for item in members), ZERO)
        received_fte_values = [item["received_simultaneous_fte"] for item in members if item["received_simultaneous_fte"] is not None]
        received_fte = sum(received_fte_values, ZERO) if received_fte_values else None
        effective_goal_hour = expected_derived / expected_fte if expected_fte > ZERO else None
        effective_goal_day = effective_goal_hour * META_HOURS if effective_goal_hour else None
        derivation_pct = expected_derived / expected * Decimal("100") if expected > ZERO else ZERO
        sources = sorted({item["profile_source"].get("source", "unknown") for item in members})
        fallback = any(source != "recent_workflow" for source in sources)
        goal_periods = sorted({
            (item["meta_valid_from"], item["meta_valid_to"])
            for item in members
            if item["meta_valid_from"]
        })
        label = (
            first.get("familia") or "Sem familia" if level == "family"
            else first.get("workflow_nome") or str(first["id_workflow"]) if level == "workflow"
            else first.get("etapa_nome") or str(first["id_etapa"])
        )
        result_rows.append({
            "key": ":".join(str(value) for value in key),
            "label": label,
            "familia": label if level == "family" else first.get("familia"),
            "id_cliente": first.get("id_cliente") if level != "family" else None,
            "cliente_nome": first.get("cliente_nome") if level != "family" else None,
            "id_workflow": first.get("id_workflow") if level in {"workflow", "stage"} else None,
            "workflow_nome": first.get("workflow_nome") if level in {"workflow", "stage"} else None,
            "id_etapa": first.get("id_etapa") if level == "stage" else None,
            "etapa_nome": first.get("etapa_nome") if level == "stage" else None,
            "expected_volume": _serialize(expected),
            "received_volume": _serialize(received) if received is not None else None,
            "received_volume_reason": None if received is not None else "no_trusted_actual_for_selected_hour",
            "derivation_pct": _serialize(derivation_pct),
            "expected_derived_volume": _serialize(expected_derived),
            "received_derived_volume": _serialize(received_derived) if received_derived is not None else None,
            "goal_per_day": _serialize(effective_goal_day) if effective_goal_day else None,
            "goal_per_hour": _serialize(effective_goal_hour) if effective_goal_hour else None,
            "expected_hourly_fte": _serialize(expected_fte, "0.0001"),
            "received_hourly_fte": _serialize(received_fte, "0.0001") if received_fte is not None else None,
            "expected_simultaneous_fte": _serialize(expected_fte, "0.0001"),
            "received_simultaneous_fte": _serialize(received_fte, "0.0001") if received_fte is not None else None,
            "people_reference": int(expected_fte.to_integral_value(rounding=ROUND_CEILING)) if expected_fte > ZERO else 0,
            "estimated": fallback,
            "fallback": fallback,
            "sources": {
                "expected_volume": "projecao_sla_active_on_calculation_date",
                "received_volume": "sla_util_consolidado_trusted_real_hour" if received is not None else None,
                "derivation": "approved_derivacao_import",
                "hourly_profile": sources,
                "goal": "meta_etapa_active_on_calculation_date",
            },
            "validity": {
                "calculation_date": on_date.isoformat(),
                "derivation_reference_date": daily["analysis"]["reference_date"],
                "profile_from": profile_from.isoformat(),
                "profile_to": profile_to.isoformat(),
                "goal_from": goal_periods[0][0] if len(goal_periods) == 1 else None,
                "goal_to": goal_periods[0][1] if len(goal_periods) == 1 else None,
                "goal_periods": [
                    {"from": period_from, "to": period_to}
                    for period_from, period_to in goal_periods
                ],
            },
            "calculation_memory": {
                "expression": "expected_hourly_fte = expected_volume * derivation_pct / 100 / goal_per_hour",
                "expected_volume": _serialize(expected),
                "derivation_pct": _serialize(derivation_pct),
                "expected_derived_volume": _serialize(expected_derived),
                "goal_per_day": _serialize(effective_goal_day) if effective_goal_day else None,
                "goal_per_hour": _serialize(effective_goal_hour) if effective_goal_hour else None,
                "result": _serialize(expected_fte, "0.0001"),
            },
        })
    result_rows.sort(key=lambda row: (-_decimal(row["expected_simultaneous_fte"]), row["label"].casefold()))
    expected_fte_total = sum((_decimal(row["expected_simultaneous_fte"]) for row in result_rows), ZERO)
    received_fte_rows = [row for row in result_rows if row["received_simultaneous_fte"] is not None]
    received_fte_total = sum((_decimal(row["received_simultaneous_fte"]) for row in received_fte_rows), ZERO) if received_fte_rows else None
    return {
        "ok": True,
        "ready": daily["ready"],
        "mode": "hourly_drilldown",
        "calculation_date": on_date.isoformat(),
        "hour": hour,
        "level": level,
        "filters": {"familia": familia, "id_cliente": id_cliente, "id_workflow": id_workflow, "id_etapa": id_etapa},
        "breadcrumb": [item for item in [
            {"level": "hour", "key": str(hour), "label": f"{hour:02d}:00"},
            {"level": "family", "key": familia, "label": familia} if familia else None,
            {"level": "workflow", "key": str(id_workflow), "label": result_rows[0].get("workflow_nome") if result_rows else str(id_workflow)} if id_workflow is not None else None,
        ] if item],
        "metric_contract": _hourly_metric_contract(),
        "units": {
            "expected_volume": "cases_in_hour_at_workflow_ingress",
            "received_volume": "cases_received_in_hour",
            "derivation_pct": "percent",
            "expected_derived_volume": "stage_executions_in_hour",
            "received_derived_volume": "stage_executions_in_hour",
            "goal_per_day": "stage_executions_per_fte_day",
            "expected_hourly_fte": "simultaneous_fte_in_hour",
            "received_hourly_fte": "simultaneous_fte_in_hour",
            "expected_simultaneous_fte": "simultaneous_fte_in_hour",
            "received_simultaneous_fte": "simultaneous_fte_in_hour",
            "people_reference": "integer_scale_reference_not_unique_people",
        },
        "summary": {
            "expected_hourly_fte": _serialize(expected_fte_total, "0.0001"),
            "received_hourly_fte": _serialize(received_fte_total, "0.0001") if received_fte_total is not None else None,
            "expected_simultaneous_fte": _serialize(expected_fte_total, "0.0001"),
            "received_simultaneous_fte": _serialize(received_fte_total, "0.0001") if received_fte_total is not None else None,
            "people_reference": int(expected_fte_total.to_integral_value(rounding=ROUND_CEILING)) if expected_fte_total > ZERO else 0,
            "row_count": len(result_rows),
        },
        "reconciliation": {
            "additive_metrics": ["expected_simultaneous_fte", "received_simultaneous_fte"],
            "expected": {"parent": _serialize(expected_fte_total, "0.0001"), "children_sum": _serialize(expected_fte_total, "0.0001"), "delta": "0.0000", "reconciled": True},
            "received": {"parent": _serialize(received_fte_total, "0.0001") if received_fte_total is not None else None, "children_sum": _serialize(received_fte_total, "0.0001") if received_fte_total is not None else None, "delta": "0.0000" if received_fte_total is not None else None, "reconciled": received_fte_total is not None},
            "people_reference_additive": False,
            "expected_volume_additive_across_families": False,
        },
        "rows": result_rows,
        "quality": {key: value for key, value in profile_quality.items() if key != "by_workflow"},
        "workflows_without_hourly_profile": missing_profiles,
        "blockers": daily["blockers"],
    }


def calculate_capacity_distribution(
    on_date: date,
    *,
    hour: int | None = None,
    level: str | None = None,
    familia: str | None = None,
    id_cliente: int | None = None,
    id_workflow: int | None = None,
    id_etapa: int | None = None,
    search: str = "",
    profile_from: date | None = None,
    profile_to: date | None = None,
    page: int = 1,
    page_size: int = 50,
    scenario_id: str = "planejamento",
    manual_overrides: dict | None = None,
) -> dict:
    """Retorna somente o recorte solicitado, sem materializar um cubo hora x etapa x WF."""
    if hour is not None or level is not None:
        if hour is None:
            raise ValueError("hour e obrigatoria para o drill-down horario.")
        return calculate_hourly_capacity_drilldown(
            on_date,
            hour=hour,
            level=level or "family",
            familia=familia,
            id_cliente=id_cliente,
            id_workflow=id_workflow,
            id_etapa=id_etapa,
            profile_from=profile_from,
            profile_to=profile_to,
            scenario_id=scenario_id,
            manual_overrides=manual_overrides,
        )
    if page < 1:
        raise ValueError("page deve ser maior ou igual a 1.")
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size deve estar entre 1 e {MAX_PAGE_SIZE}.")
    if profile_from is None or profile_to is None:
        profile_from, profile_to = default_profile_window(on_date)
    if profile_from > profile_to:
        raise ValueError("profile_from nao pode ser posterior a profile_to.")

    # A mesma regra oficial monta os totais diarios e a atribuicao do HC. O
    # perfil global e deliberadamente pulado: consultamos somente os workflows
    # presentes no recorte, evitando o custo de carregar toda a curva do dia.
    daily = calculate_daily_capacity(
        on_date,
        include_details=True,
        include_hourly=False,
        include_dax=False,
        include_quarterly=False,
        scenario_id=scenario_id,
        manual_overrides=manual_overrides,
    )
    if id_cliente is None and id_workflow is None and id_etapa is None:
        needle = search.strip().casefold()
        stage_by_id = {row["id_etapa"]: row for row in daily["results"]}
        workflows = []
        for row in daily["by_workflow"]:
            if needle and needle not in (row.get("workflow_nome") or "").casefold() and needle not in (row.get("cliente_nome") or "").casefold():
                continue
            exact_fte = ZERO
            for stage_row in row.get("stages", []):
                stage = stage_by_id.get(stage_row["id_etapa"], {})
                meta_day = _decimal(stage.get("meta_dia"))
                if stage_row.get("status") == "dimensionada" and meta_day > ZERO:
                    exact_fte += _decimal(stage_row["demand"]) / meta_day
            item = {key: value for key, value in row.items() if key != "stages"}
            item["exact_daily_fte"] = _serialize(exact_fte, "0.0001")
            workflows.append(item)
        offset = (page - 1) * page_size
        return {
            "ok": True,
            "ready": daily["ready"],
            "mode": "workflow_list",
            "calculation_date": on_date.isoformat(),
            "filters": {"search": search.strip()},
            "summary": {
                "projected_ingress_volume": daily["summary"]["projected_workflow_volume"],
                "dedicated_stage_headcount": daily["summary"]["agents_required"],
                "workflow_count": len(workflows),
            },
            "units": {
                "projected_ingress_volume": "cases_per_day_at_workflow_ingress",
                "dedicated_stage_headcount": "integer_people_day_rounded_by_stage",
                "agents_attributed": "decimal_share_of_stage_rounded_people_day",
                "exact_daily_fte": "productive_people_day_before_stage_rounding",
            },
            "workflows": workflows[offset : offset + page_size],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": len(workflows),
                "has_next": offset + page_size < len(workflows),
            },
            "hourly_profile": None,
            "blockers": daily["blockers"],
        }
    stage_by_id = {row["id_etapa"]: row for row in daily["results"]}

    # by_workflow contem todas as combinacoes, ao contrario do breakdown
    # legado (limitado a 500 linhas).
    combinations = []
    for workflow in daily["by_workflow"]:
        if id_cliente is not None and workflow["id_cliente"] != id_cliente:
            continue
        if id_workflow is not None and workflow["id_workflow"] != id_workflow:
            continue
        combinations.extend(
            row
            for row in workflow.get("stages", [])
            if id_etapa is None or row["id_etapa"] == id_etapa
        )

    combinations.sort(
        key=lambda row: (
            -_decimal(row["agents_share"]),
            (row.get("workflow_nome") or "").casefold(),
            (row.get("etapa_nome") or "").casefold(),
        )
    )
    enriched_combinations = []
    for row in combinations:
        stage = stage_by_id.get(row["id_etapa"], {})
        meta_day = _decimal(stage.get("meta_dia"))
        row_fte = (
            _decimal(row["demand"]) / meta_day
            if row.get("status") == "dimensionada" and meta_day > ZERO
            else None
        )
        enriched_combinations.append(
            {
                **row,
                "meta_day": stage.get("meta_dia"),
                "exact_fte": _serialize(row_fte, "0.0001") if row_fte is not None else None,
                "dedicated_stage_headcount": stage.get("agentes_necessarios"),
            }
        )
    combinations = enriched_combinations
    combination_keys = {
        (row["id_cliente"], row["id_workflow"], row["id_etapa"])
        for row in combinations
    }
    workflow_keys = {(row[0], row[1]) for row in combination_keys}
    workflows = {
        (row["id_cliente"], row["id_workflow"]): row
        for row in daily["by_workflow"]
        if (row["id_cliente"], row["id_workflow"]) in workflow_keys
    }

    profiles, missing_profiles, profile_quality = build_workflow_hourly_profiles(
        workflow_keys,
        on_date=on_date,
        profile_from=profile_from,
        profile_to=profile_to,
    )
    missing_keys = {
        (row["id_cliente"], row["id_workflow"]) for row in missing_profiles
    }

    projected_volume = sum(
        (_decimal(workflows[key]["volume"]) for key in workflow_keys if key in workflows),
        ZERO,
    )
    demand = sum((_decimal(row["demand"]) for row in combinations), ZERO)
    attributed_hc = sum((_decimal(row["agents_share"]) for row in combinations), ZERO)
    selected_stage_ids = {row["id_etapa"] for row in combinations}
    dedicated_stage_hc = sum(
        int(stage_by_id[stage_id].get("agentes_necessarios") or 0)
        for stage_id in selected_stage_ids
        if stage_id in stage_by_id
        and stage_by_id[stage_id].get("status") == "dimensionada"
    )
    exact_fte = ZERO
    dimensioned_demand = ZERO
    dimensioned_combinations = []
    for row in combinations:
        stage = stage_by_id.get(row["id_etapa"], {})
        meta_day = _decimal(stage.get("meta_dia"))
        if row.get("status") != "dimensionada" or meta_day <= ZERO:
            continue
        row_fte = _decimal(row["demand"]) / meta_day
        exact_fte += row_fte
        dimensioned_demand += _decimal(row["demand"])
        dimensioned_combinations.append((row, meta_day))

    by_hour = []
    for hour in HOURS:
        volume_h = ZERO
        demand_h = ZERO
        simultaneous_fte = ZERO
        for workflow_key in workflow_keys:
            workflow = workflows.get(workflow_key)
            profile = profiles.get(workflow_key)
            if not workflow or not profile:
                continue
            share = _decimal(profile[hour]["share"])
            volume_h += _decimal(workflow["volume"]) * share
        for row, meta_day in dimensioned_combinations:
            workflow_key = (row["id_cliente"], row["id_workflow"])
            profile = profiles.get(workflow_key)
            if not profile:
                continue
            share = _decimal(profile[hour]["share"])
            row_demand_h = _decimal(row["demand"]) * share
            demand_h += row_demand_h
            simultaneous_fte += row_demand_h / (meta_day / META_HOURS)
        by_hour.append(
            {
                "hour": hour,
                "projected_volume": _serialize(volume_h),
                "demand": _serialize(demand_h),
                "fte": _serialize(simultaneous_fte, "0.0001"),
                "simultaneous_fte": _serialize(simultaneous_fte, "0.0001"),
                "agents_fractional": _serialize(simultaneous_fte, "0.0001"),
                "agents_rounded_visual": (
                    int(simultaneous_fte.to_integral_value(rounding=ROUND_CEILING))
                    if simultaneous_fte > ZERO
                    else 0
                ),
            }
        )

    expected_fte_hours = exact_fte * META_HOURS
    serialized_target = expected_fte_hours.quantize(Decimal("0.0001"))
    serialized_total = sum((_decimal(row["fte"]) for row in by_hour), ZERO)
    serialization_delta = serialized_target - serialized_total
    if serialization_delta:
        peak_index = max(range(len(by_hour)), key=lambda index: _decimal(by_hour[index]["fte"]))
        adjusted_peak = _decimal(by_hour[peak_index]["fte"]) + serialization_delta
        adjusted_peak_serialized = _serialize(adjusted_peak, "0.0001")
        by_hour[peak_index]["fte"] = adjusted_peak_serialized
        by_hour[peak_index]["simultaneous_fte"] = adjusted_peak_serialized
        by_hour[peak_index]["agents_fractional"] = adjusted_peak_serialized
        by_hour[peak_index]["agents_rounded_visual"] = (
            int(adjusted_peak.to_integral_value(rounding=ROUND_CEILING))
            if adjusted_peak > ZERO
            else 0
        )

    offset = (page - 1) * page_size
    paged_combinations = combinations[offset : offset + page_size]
    clients = {
        (row["id_cliente"], row.get("cliente_nome")) for row in combinations
    }
    workflow_context = {
        (row["id_workflow"], row.get("workflow_nome")) for row in combinations
    }
    stages = {(row["id_etapa"], row.get("etapa_nome")) for row in combinations}
    peak = max(by_hour, key=lambda row: _decimal(row["simultaneous_fte"]))
    hourly_fte_total = sum((_decimal(row["fte"]) for row in by_hour), ZERO)
    hourly_fte_delta = hourly_fte_total - serialized_target

    return {
        "ok": True,
        "ready": daily["ready"],
        "mode": "distribution_detail",
        "calculation_date": on_date.isoformat(),
        "filters": {
            "id_cliente": id_cliente,
            "id_workflow": id_workflow,
            "id_etapa": id_etapa,
        },
        "context": {
            "clients": [
                {"id_cliente": key, "cliente_nome": name}
                for key, name in sorted(clients, key=lambda item: (item[1] or "").casefold())
            ],
            "workflows": [
                {"id_workflow": key, "workflow_nome": name}
                for key, name in sorted(
                    workflow_context, key=lambda item: (item[1] or "").casefold()
                )
            ],
            "stages": [
                {"id_etapa": key, "etapa_nome": name}
                for key, name in sorted(stages, key=lambda item: (item[1] or "").casefold())
            ],
            "profile_from": profile_from.isoformat(),
            "profile_to": profile_to.isoformat(),
            "profile_days": (profile_to - profile_from).days + 1,
            "derivation_reference_date": daily["analysis"]["reference_date"],
        },
        "summary": {
            "projected_ingress_volume": _serialize(projected_volume),
            "derived_stage_demand": _serialize(demand),
            "dimensioned_stage_demand": _serialize(dimensioned_demand),
            "exact_daily_fte": _serialize(exact_fte, "0.0001"),
            "expected_fte_hours": _serialize(expected_fte_hours, "0.0001"),
            "hourly_fte_total": _serialize(hourly_fte_total, "0.0001"),
            "hourly_fte_delta": _serialize(hourly_fte_delta, "0.0001"),
            "hourly_fte_reconciled": abs(hourly_fte_delta) <= Decimal("0.0001"),
            "dedicated_stage_headcount": dedicated_stage_hc,
            "attributed_dedicated_headcount": _serialize(attributed_hc, "0.0001"),
            "workflow_count": len(workflow_keys),
            "stage_count": len({row["id_etapa"] for row in combinations}),
            "combination_count": len(combinations),
            "workflows_without_hourly_profile": len(workflow_keys & missing_keys),
            "peak_hour": peak["hour"],
            "peak_hourly_fte": peak["fte"],
            "peak_simultaneous_fte": peak["fte"],
        },
        "units": {
            "projected_ingress_volume": "cases_per_day_at_workflow_ingress",
            "derived_stage_demand": "stage_executions_per_day",
            "dimensioned_stage_demand": "dimensioned_stage_executions_per_day",
            "exact_daily_fte": "productive_people_day_before_stage_rounding",
            "dedicated_stage_headcount": "integer_people_day_rounded_by_unique_stage",
            "attributed_dedicated_headcount": "share_of_stage_rounded_people_day",
            "fte": "simultaneous_fte_in_hour",
            "simultaneous_fte": "simultaneous_fte_in_hour",
            "agents_fractional": "compat_alias_for_simultaneous_fte",
        },
        "non_additivity": {
            "projected_ingress_volume": "Nao some o volume entre etapas; o mesmo item pode percorrer varias etapas.",
            "attributed_dedicated_headcount": "A soma e conservativa no dia, mas representa rateio do arredondamento dedicado por etapa, nao pessoas unicas.",
            "dedicated_stage_headcount": "Aditivo apenas entre etapas ou pools distintos; nao atribua o inteiro repetidamente a cada workflow.",
            "hourly": "A soma das 24 faixas e FTE-hora e reconcilia com o FTE diario multiplicado por 5,5.",
        },
        "hourly_profile": {
            "scope": "dimensioned_combinations_only",
            "source": "trusted_sla_hour_profile",
            "fallback": "quarterly_weekday_then_client_then_uniform",
            "quality": {
                key: value
                for key, value in profile_quality.items()
                if key != "by_workflow"
            },
            "selected_profiles": [
                {
                    "id_cliente": key[0],
                    "id_workflow": key[1],
                    **value,
                }
                for key, value in profile_quality.get("by_workflow", {}).items()
                if key in workflow_keys
            ],
            "by_hour": by_hour,
        },
        "combinations": paged_combinations,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": len(combinations),
            "has_next": offset + page_size < len(combinations),
        },
        "blockers": daily["blockers"],
    }
