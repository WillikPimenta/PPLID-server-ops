"""Capacity diário: Projeção SLA -> derivação -> Meta Etapa -> pessoas."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_CEILING
from statistics import median

from django.db.models import Q
from django.utils import timezone

from apps.controle_sla.services.sla_eval import expand_dias_token
from apps.dimensoes_processos.models import (
    CapacityDailySnapshot,
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    MetaEtapa,
    ProjecaoSla,
)
from apps.dimensoes_processos.services.capacity_familia import extract_familia_normalized
from apps.dimensoes_processos.services.capacity_fingerprint import (
    current_capacity_source_fingerprint,
)
from apps.dimensoes_processos.services.capacity_hourly import (
    build_hourly_capacity,
    build_workflow_hourly_profiles,
    default_profile_window,
)
from apps.dimensoes_processos.services.capacity_quarterly import (
    build_quarterly_weekday_reference,
)
from apps.dimensoes_processos.services.capacity_volume_planejamento import (
    apply_planejamento_volume_guard,
)
from apps.dimensoes_processos.services.capacity_scenarios import (
    DERIVATION_MEDIAN_LOOKBACK_DAYS,
    PLANEJAMENTO_OPERACIONAL_MAX_RATIO,
    PLANEJAMENTO_PRODUTO_TIPO,
    _synthetic_derivation_row,
    filter_documentoscopia_workflows,
    manual_meta_by_stage,
    merge_workflow_meta_from_derivation,
    operational_meta_by_stage,
    resolve_derivation,
    resolve_workflow_volumes,
    scenario_config,
    scenario_public_payload,
)
from apps.dimensoes_processos.services.capacity_dax_response import (
    build_capacity_comparative,
    build_dax_capacity_response,
)


ZERO = Decimal("0")
HUNDRED = Decimal("100")
BREAKDOWN_LIMIT = 500
CAPACITY_PERIOD_METRIC_VERSION = "simultaneous-fte-v3"


from apps.dimensoes_processos.services.capacity_errors import CapacityUnavailableError

def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _serialize_decimal(value: Decimal, places: str = "0.01") -> str:
    return str(value.quantize(Decimal(places)))


def _cache_value(cache: dict | None, key: tuple, factory):
    """Cache estritamente local a uma requisicao composta de Capacity."""

    if cache is None:
        return factory()
    if key not in cache:
        cache[key] = factory()
    return cache[key]


def _top_familias_by_agents(
    familia_agents: dict[str, Decimal],
    total_agents: Decimal,
    *,
    limit: int = 3,
) -> list[dict]:
    ranked = sorted(familia_agents.items(), key=lambda item: (-item[1], item[0].casefold()))
    top: list[dict] = []
    for familia, agents in ranked[:limit]:
        share_pct = (agents / total_agents * HUNDRED) if total_agents > ZERO else ZERO
        top.append(
            {
                "familia": familia,
                "agents": _serialize_decimal(agents, "0.0001"),
                "share_pct": _serialize_decimal(share_pct),
            }
        )
    return top


def _top_familias_by_fte(
    familia_fte: dict[str, Decimal],
    total_fte: Decimal,
    *,
    limit: int = 3,
) -> list[dict]:
    ranked = sorted(familia_fte.items(), key=lambda item: (-item[1], item[0].casefold()))
    top: list[dict] = []
    for familia, fte in ranked[:limit]:
        share_pct = (fte / total_fte * HUNDRED) if total_fte > ZERO else ZERO
        top.append(
            {
                "familia": familia,
                "fte": _serialize_decimal(fte, "0.0001"),
                "share_pct": _serialize_decimal(share_pct),
            }
        )
    return top


def _reconcile_serialized_total(
    rows: list[dict],
    field: str,
    target: Decimal,
    *,
    places: str = "0.0001",
) -> None:
    """Fecha residuos de serializacao sem introduzir arredondamento em FTE."""

    if not rows:
        return
    serialized_target = target.quantize(Decimal(places))
    serialized_total = sum((_decimal(row.get(field)) for row in rows), ZERO)
    delta = serialized_target - serialized_total
    if delta:
        rows[0][field] = _serialize_decimal(_decimal(rows[0][field]) + delta, places)


def _build_capacity_aggregations(
    *,
    results: list[dict],
    demand_by_stage: dict[int, Decimal],
    demand_by_stage_wf: dict[int, dict[tuple[int, int], Decimal]],
    workflow_totals: dict[tuple[int, int], Decimal],
    workflow_meta: dict[tuple[int, int], dict],
    total_exact_fte: Decimal,
    exact_fte_by_stage: dict[int, Decimal],
) -> tuple[list[dict], list[dict], list[dict], list[dict], bool]:
    stage_by_id = {row["id_etapa"]: row for row in results}

    agents_by_wf: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    familia_agents_by_wf: dict[tuple[int, int], dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )
    fte_by_wf: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    familia_fte_by_wf: dict[tuple[int, int], dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )
    demand_by_wf: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    breakdown_by_wf: dict[tuple[int, int], list[dict]] = defaultdict(list)

    for etapa_id, wf_demands in demand_by_stage_wf.items():
        stage = stage_by_id[etapa_id]
        total_stage_demand = demand_by_stage[etapa_id]
        familia = stage.get("familia") or extract_familia_normalized(stage["etapa_nome"])
        for wf_key, stage_demand in wf_demands.items():
            demand_by_wf[wf_key] += stage_demand
            if stage["status"] != "dimensionada" or total_stage_demand <= ZERO:
                continue
            agents = _decimal(stage["agentes_necessarios"])
            share = agents * (stage_demand / total_stage_demand)
            agents_by_wf[wf_key] += share
            familia_agents_by_wf[wf_key][familia] += share
            stage_fte = exact_fte_by_stage.get(etapa_id, _decimal(stage["fte"]))
            fte_share = stage_fte * (stage_demand / total_stage_demand)
            fte_by_wf[wf_key] += fte_share
            familia_fte_by_wf[wf_key][familia] += fte_share

    by_familia_map: dict[str, dict] = {}
    for row in results:
        familia = row.get("familia") or extract_familia_normalized(row["etapa_nome"])
        agg = by_familia_map.setdefault(
            familia,
            {
                "familia": familia,
                "stage_count": 0,
                "demand": ZERO,
                "agents": 0,
                "fte": ZERO,
                "automatic_demand": ZERO,
                "slack": ZERO,
            },
        )
        agg["stage_count"] += 1 if row["status"] != "automatica" else 0
        demand = _decimal(row["analises_previstas"])
        agg["demand"] += demand
        if row["status"] == "dimensionada":
            agg["agents"] += row["agentes_necessarios"] or 0
            agg["fte"] += _decimal(row["fte"])
            agg["slack"] += _decimal(row["folga_capacidade"])
        elif row["status"] == "automatica":
            agg["automatic_demand"] += demand

    by_familia = []
    for agg in by_familia_map.values():
        by_familia.append(
            {
                "familia": agg["familia"],
                "stage_count": agg["stage_count"],
                "demand": _serialize_decimal(agg["demand"]),
                "agents": agg["agents"],
                "fte": _serialize_decimal(agg["fte"], "0.0001"),
                "automatic_demand": _serialize_decimal(agg["automatic_demand"]),
                "share_pct": _serialize_decimal(
                    agg["fte"] / total_exact_fte * HUNDRED
                    if total_exact_fte > ZERO
                    else ZERO
                ),
                "slack": _serialize_decimal(agg["slack"]),
            }
        )
    by_familia.sort(key=lambda item: (-_decimal(item["fte"]), item["familia"].casefold()))
    _reconcile_serialized_total(by_familia, "fte", total_exact_fte)

    by_client_map: dict[int, dict] = {}
    for wf_key, volume in workflow_totals.items():
        if volume <= ZERO:
            continue
        cliente_id, _workflow_id = wf_key
        meta = workflow_meta[wf_key]
        agg = by_client_map.setdefault(
            cliente_id,
            {
                "id_cliente": cliente_id,
                "cliente_nome": meta["cliente_nome"],
                "workflow_count": 0,
                "volume": ZERO,
                "demand": ZERO,
                "agents_attributed": ZERO,
                "familia_agents": defaultdict(lambda: ZERO),
                "fte_attributed": ZERO,
                "familia_fte": defaultdict(lambda: ZERO),
            },
        )
        agg["workflow_count"] += 1
        agg["volume"] += volume
        agg["demand"] += demand_by_wf.get(wf_key, ZERO)
        agg["agents_attributed"] += agents_by_wf.get(wf_key, ZERO)
        agg["fte_attributed"] += fte_by_wf.get(wf_key, ZERO)
        for familia, agents in familia_agents_by_wf.get(wf_key, {}).items():
            agg["familia_agents"][familia] += agents
        for familia, fte in familia_fte_by_wf.get(wf_key, {}).items():
            agg["familia_fte"][familia] += fte

    by_client = []
    for agg in by_client_map.values():
        total_client_agents = _decimal(agg["agents_attributed"])
        by_client.append(
            {
                "id_cliente": agg["id_cliente"],
                "cliente_nome": agg["cliente_nome"],
                "workflow_count": agg["workflow_count"],
                "volume": _serialize_decimal(agg["volume"]),
                "demand": _serialize_decimal(agg["demand"]),
                "agents_attributed": _serialize_decimal(total_client_agents, "0.0001"),
                "fte_attributed": _serialize_decimal(agg["fte_attributed"], "0.0001"),
                "top_familias": _top_familias_by_agents(agg["familia_agents"], total_client_agents),
                "top_familias_fte": _top_familias_by_fte(
                    agg["familia_fte"], _decimal(agg["fte_attributed"])
                ),
            }
        )
    by_client.sort(key=lambda item: (-_decimal(item["fte_attributed"]), item["cliente_nome"].casefold()))
    _reconcile_serialized_total(by_client, "fte_attributed", total_exact_fte)

    by_workflow = []
    for wf_key, volume in workflow_totals.items():
        if volume <= ZERO:
            continue
        cliente_id, workflow_id = wf_key
        meta = workflow_meta[wf_key]
        total_wf_agents = agents_by_wf.get(wf_key, ZERO)
        by_workflow.append(
            {
                "id_cliente": cliente_id,
                "cliente_nome": meta["cliente_nome"],
                "id_workflow": workflow_id,
                "workflow_nome": meta["workflow_nome"],
                "volume": _serialize_decimal(volume),
                "demand": _serialize_decimal(demand_by_wf.get(wf_key, ZERO)),
                "agents_attributed": _serialize_decimal(total_wf_agents, "0.0001"),
                "fte_attributed": _serialize_decimal(fte_by_wf.get(wf_key, ZERO), "0.0001"),
                "top_familias": _top_familias_by_agents(
                    familia_agents_by_wf.get(wf_key, {}),
                    total_wf_agents,
                ),
                "top_familias_fte": _top_familias_by_fte(
                    familia_fte_by_wf.get(wf_key, {}),
                    fte_by_wf.get(wf_key, ZERO),
                ),
            }
        )
    by_workflow.sort(
        key=lambda item: (-_decimal(item["fte_attributed"]), item["workflow_nome"].casefold())
    )
    _reconcile_serialized_total(by_workflow, "fte_attributed", total_exact_fte)

    for etapa_id, wf_demands in sorted(
        demand_by_stage_wf.items(),
        key=lambda item: stage_by_id[item[0]]["etapa_nome"].casefold(),
    ):
        stage = stage_by_id[etapa_id]
        total_stage_demand = demand_by_stage[etapa_id]
        familia = stage.get("familia") or extract_familia_normalized(stage["etapa_nome"])
        for wf_key, stage_demand in wf_demands.items():
            cliente_id, workflow_id = wf_key
            meta = workflow_meta.get(wf_key, {})
            agents_share = ZERO
            fte_share = ZERO
            if stage["status"] == "dimensionada" and total_stage_demand > ZERO:
                agents = _decimal(stage["agentes_necessarios"])
                agents_share = agents * (stage_demand / total_stage_demand)
                fte_share = exact_fte_by_stage.get(
                    etapa_id, _decimal(stage["fte"])
                ) * (stage_demand / total_stage_demand)
            breakdown_by_wf[wf_key].append(
                {
                    "id_cliente": cliente_id,
                    "cliente_nome": meta.get("cliente_nome"),
                    "id_workflow": workflow_id,
                    "workflow_nome": meta.get("workflow_nome"),
                    "id_etapa": etapa_id,
                    "familia": familia,
                    "etapa_nome": stage["etapa_nome"],
                    "demand": _serialize_decimal(stage_demand),
                    "agents_share": _serialize_decimal(agents_share, "0.0001"),
                    "fte_share": _serialize_decimal(fte_share, "0.0001"),
                    "status": stage["status"],
                }
            )

    for wf_key, stages in breakdown_by_wf.items():
        stages.sort(
            key=lambda row: (
                -_decimal(row["fte_share"]),
                row["etapa_nome"].casefold(),
            )
        )

    for item in by_workflow:
        wf_key = (item["id_cliente"], item["id_workflow"])
        item["stages"] = breakdown_by_wf.get(wf_key, [])
        item["stage_count"] = len(item["stages"])

    all_breakdown_rows: list[dict] = []
    for wf_key in sorted(breakdown_by_wf.keys(), key=lambda key: workflow_meta.get(key, {}).get("workflow_nome", "").casefold()):
        all_breakdown_rows.extend(breakdown_by_wf[wf_key])

    breakdown_truncated = len(all_breakdown_rows) > BREAKDOWN_LIMIT
    breakdown = all_breakdown_rows[:BREAKDOWN_LIMIT]

    return by_familia, by_client, by_workflow, breakdown, breakdown_truncated


def _approved_import_run(on_date: date) -> DerivacaoEtapaImportRun:
    run = (
        DerivacaoEtapaImportRun.objects.filter(
            run_kind=DerivacaoEtapaImportRun.KIND_IMPORT,
            status=DerivacaoEtapaImportRun.STATUS_OK,
            reviewed_scan__isnull=False,
            reviewed_scan__run_kind=DerivacaoEtapaImportRun.KIND_SCAN,
            reviewed_scan__status=DerivacaoEtapaImportRun.STATUS_OK,
            period_from__lte=on_date,
            period_to__gte=on_date,
            finished_at__isnull=False,
        )
        .select_related("reviewed_scan", "triggered_by")
        .order_by("-finished_at", "-started_at", "-pk")
        .first()
    )
    if run is None:
        raise CapacityUnavailableError(
            "Não existe uma análise de derivação válida e aprovada anterior ou igual à data selecionada."
        )
    return run


def _optional_import_run(on_date: date) -> DerivacaoEtapaImportRun | None:
    try:
        return _approved_import_run(on_date)
    except CapacityUnavailableError:
        return None


def _reference_derivation_rows(
    run: DerivacaoEtapaImportRun,
    on_date: date,
) -> tuple[date, list[DerivacaoEtapaDiaria]]:
    base = DerivacaoEtapaDiaria.objects.filter(import_run=run, data__lte=on_date)
    reference_date = base.order_by("-data").values_list("data", flat=True).first()
    if reference_date is None:
        raise CapacityUnavailableError(
            "A última análise aprovada não possui dados de derivação disponíveis para o cálculo."
        )
    rows = list(
        base.filter(data=reference_date).select_related(
            "cliente", "workflow", "etapa"
        )
    )
    return reference_date, rows


def _median_derivation_rows(
    run: DerivacaoEtapaImportRun,
    on_date: date,
) -> tuple[date, list, dict]:
    window_from = on_date - timedelta(days=DERIVATION_MEDIAN_LOOKBACK_DAYS)
    window_to = on_date - timedelta(days=1)
    if window_to < window_from:
        raise CapacityUnavailableError(
            "Não há janela suficiente para calcular a mediana de derivação."
        )

    rows_qs = (
        DerivacaoEtapaDiaria.objects.filter(
            import_run=run,
            data__gte=window_from,
            data__lt=on_date,
            workflow__produto__tipo_produto=PLANEJAMENTO_PRODUTO_TIPO,
        )
        .select_related("cliente", "workflow", "workflow__produto", "etapa")
        .order_by("cliente_id", "workflow_id", "etapa_id", "data")
    )

    percentuals_by_combo: dict[tuple[int, int, int], list[Decimal]] = defaultdict(list)
    sample_meta: dict[tuple[int, int, int], dict] = {}
    sample_days: set[date] = set()

    for row in rows_qs:
        key = (row.cliente_id, row.workflow_id, row.etapa_id)
        percentuals_by_combo[key].append(_decimal(row.percentual))
        sample_days.add(row.data)
        if key not in sample_meta:
            sample_meta[key] = {
                "cliente_nome": row.cliente.nome if row.cliente else "",
                "workflow_nome": row.workflow.nome if row.workflow else "",
                "etapa_nome": row.etapa.nome if row.etapa else "",
            }

    if not percentuals_by_combo:
        raise CapacityUnavailableError(
            "Não existem linhas de derivação Documentoscopia na janela de 60 dias."
        )

    synthetic_rows = []
    for (cliente_id, workflow_id, etapa_id), percentuals in percentuals_by_combo.items():
        meta = sample_meta[(cliente_id, workflow_id, etapa_id)]
        synthetic_rows.append(
            _synthetic_derivation_row(
                cliente_id=cliente_id,
                workflow_id=workflow_id,
                etapa_id=etapa_id,
                percentual=Decimal(str(median([float(value) for value in percentuals]))),
                cliente_nome=meta["cliente_nome"],
                workflow_nome=meta["workflow_nome"],
                etapa_nome=meta["etapa_nome"],
            )
        )

    analysis_extra = {
        "derivation_method": "median_60d",
        "derivation_window_from": window_from.isoformat(),
        "derivation_window_to": window_to.isoformat(),
        "derivation_sample_days": len(sample_days),
    }
    return window_to, synthetic_rows, analysis_extra


def _projection_rows_for_range(
    date_from: date,
    date_to: date,
    *,
    documentoscopia_only: bool = False,
) -> list[dict]:
    queryset = ProjecaoSla.objects.filter(
        data_inicio__lte=date_to,
        workflow__ind_considerar=True,
        cliente__operations=True,
    ).filter(Q(data_fim__isnull=True) | Q(data_fim__gte=date_from))
    if documentoscopia_only:
        queryset = queryset.filter(workflow__produto__tipo_produto=PLANEJAMENTO_PRODUTO_TIPO)
    return list(
        queryset.values(
            "cliente_id",
            "cliente__nome",
            "workflow_id",
            "workflow__nome",
            "nivel_hierarquico_id",
            "nivel_hierarquico__nome",
            "data_inicio",
            "data_fim",
            "dias_semana",
            "volume",
        )
    )


def _projection_by_workflow(on_date: date, *, prepared_rows: list[dict] | None = None):
    rows = prepared_rows
    if rows is None:
        rows = _projection_rows_for_range(on_date, on_date)
    candidates_by_nh = defaultdict(list)
    for row in rows:
        if row["data_inicio"] > on_date:
            continue
        if row["data_fim"] is not None and row["data_fim"] < on_date:
            continue
        days = expand_dias_token(row["dias_semana"])
        if days and on_date.weekday() in days:
            key = (row["cliente_id"], row["workflow_id"], row["nivel_hierarquico_id"])
            candidates_by_nh[key].append(row)

    workflow_totals = defaultdict(lambda: ZERO)
    workflow_meta: dict[tuple[int, int], dict] = {}
    duplicate_nhs: list[dict] = []
    null_volume_nhs: list[dict] = []

    for (cliente_id, workflow_id, nh_id), candidates in candidates_by_nh.items():
        sample = candidates[0]
        if len(candidates) > 1:
            duplicate_nhs.append(
                {
                    "id_cliente": cliente_id,
                    "cliente_nome": sample["cliente__nome"],
                    "id_workflow": workflow_id,
                    "workflow_nome": sample["workflow__nome"],
                    "id_nh": nh_id,
                    "nh_nome": sample["nivel_hierarquico__nome"],
                }
            )
            continue
        if sample["volume"] is None:
            null_volume_nhs.append(
                {
                    "id_cliente": cliente_id,
                    "cliente_nome": sample["cliente__nome"],
                    "id_workflow": workflow_id,
                    "workflow_nome": sample["workflow__nome"],
                    "id_nh": nh_id,
                    "nh_nome": sample["nivel_hierarquico__nome"],
                }
            )
            continue
        workflow_key = (cliente_id, workflow_id)
        workflow_totals[workflow_key] += _decimal(sample["volume"])
        meta = workflow_meta.setdefault(
            workflow_key,
            {
                "id_cliente": cliente_id,
                "cliente_nome": sample["cliente__nome"],
                "id_workflow": workflow_id,
                "workflow_nome": sample["workflow__nome"],
                "nh_count": 0,
            },
        )
        meta["nh_count"] += 1

    return workflow_totals, workflow_meta, duplicate_nhs, null_volume_nhs


def _active_meta_rows_for_range(date_from: date, date_to: date) -> list[MetaEtapa]:
    return list(
        MetaEtapa.objects.filter(data_inicio__lte=date_to)
        .filter(Q(data_fim__isnull=True) | Q(data_fim__gte=date_from))
        .select_related("etapa", "servico")
        .order_by("etapa_id", "-data_inicio", "pk")
    )


def _active_metas(
    on_date: date,
    stage_ids: set[int] | None = None,
    *,
    prepared_rows: list[MetaEtapa] | None = None,
):
    if prepared_rows is not None:
        by_stage = defaultdict(list)
        for row in prepared_rows:
            if stage_ids is not None and row.etapa_id not in stage_ids:
                continue
            if row.data_inicio > on_date:
                continue
            if row.data_fim is not None and row.data_fim < on_date:
                continue
            by_stage[row.etapa_id].append(row)
        return by_stage

    queryset = MetaEtapa.objects.filter(data_inicio__lte=on_date)
    if stage_ids is not None:
        if not stage_ids:
            return {}
        queryset = queryset.filter(etapa_id__in=stage_ids)
    rows = list(
        queryset
        .filter(Q(data_fim__isnull=True) | Q(data_fim__gte=on_date))
        .select_related("etapa", "servico")
        .order_by("etapa_id", "-data_inicio", "pk")
    )
    by_stage = defaultdict(list)
    for row in rows:
        by_stage[row.etapa_id].append(row)
    return by_stage


def calculate_daily_capacity(
    on_date: date,
    *,
    profile_from: date | None = None,
    profile_to: date | None = None,
    include_details: bool = True,
    include_hourly: bool = True,
    include_dax: bool = True,
    hourly_profile_cache: dict | None = None,
    include_quarterly: bool = False,
    quarterly_volume_cache: dict | None = None,
    _projection_override: tuple[dict, dict] | None = None,
    _prepared_derivation: tuple | None = None,
    scenario_id: str = "planejamento",
    manual_overrides: dict | None = None,
    calculation_cache: dict | None = None,
) -> dict:
    """Calcula a necessidade diária, arredondando somente após consolidar por etapa."""

    scenario = scenario_config(scenario_id)
    operational_metas = (
        _cache_value(
            calculation_cache,
            ("operational_metas", on_date),
            lambda: operational_meta_by_stage(on_date),
        )
        if scenario.meta_mode == "operacao_avg"
        else {}
    )
    manual_metas = (
        manual_meta_by_stage(manual_overrides)
        if scenario.meta_mode == "manual"
        else {}
    )

    if _prepared_derivation is None:
        derivation_analysis_extra: dict = {}
        if scenario.derivation_mode == "median_60d":
            run, reference_date, derivation_rows, derivation_analysis_extra = _cache_value(
                calculation_cache,
                ("median_derivation", on_date),
                lambda: (
                    lambda approved: (
                        approved,
                        *(_median_derivation_rows(approved, on_date)),
                    )
                )(_approved_import_run(on_date)),
            )
        elif scenario.derivation_mode == "reference":
            run, reference_date, derivation_rows = _cache_value(
                calculation_cache,
                ("reference_derivation", on_date),
                lambda: (
                    lambda approved: (
                        approved,
                        *_reference_derivation_rows(approved, on_date),
                    )
                )(_approved_import_run(on_date)),
            )
        else:
            derivation_analysis_extra = {}
            run = _cache_value(
                calculation_cache,
                ("optional_import_run", on_date),
                lambda: _optional_import_run(on_date),
            )

            def _reference_resolver(target_date: date):
                _approved, resolved_date, resolved_rows = _cache_value(
                    calculation_cache,
                    ("reference_derivation", target_date),
                    lambda: (
                        lambda approved: (
                            approved,
                            *_reference_derivation_rows(approved, target_date),
                        )
                    )(
                        run
                        if run is not None and target_date == on_date
                        else _approved_import_run(target_date)
                    ),
                )
                return resolved_date, resolved_rows

            reference_date, derivation_rows = resolve_derivation(
                on_date,
                scenario,
                reference_resolver=_reference_resolver,
                manual_overrides=manual_overrides,
            )
    else:
        run, reference_date, derivation_rows = _prepared_derivation
        derivation_analysis_extra = {}
    restrict_documentoscopia = scenario.id in ("planejamento", "planejamento_operacional")
    if _projection_override is None:
        period_range = (
            calculation_cache.get("period_date_range")
            if calculation_cache is not None
            else None
        )
        prepared_projection_rows = (
            _cache_value(
                calculation_cache,
                ("projection_rows", *period_range, restrict_documentoscopia),
                lambda: _projection_rows_for_range(
                    *period_range,
                    documentoscopia_only=restrict_documentoscopia,
                ),
            )
            if period_range is not None
            else None
        )
        projection_resolver = lambda target_date: _cache_value(
            calculation_cache,
            ("projection", target_date, restrict_documentoscopia),
            lambda: _projection_by_workflow(
                target_date,
                prepared_rows=prepared_projection_rows
                if prepared_projection_rows is not None
                else _projection_rows_for_range(
                    target_date,
                    target_date,
                    documentoscopia_only=restrict_documentoscopia,
                ),
            ),
        )
        if scenario.volume_mode == "projecao_sla":
            (
                workflow_totals,
                workflow_meta,
                duplicate_nhs,
                null_volume_nhs,
            ) = projection_resolver(on_date)
        else:
            workflow_totals, workflow_meta, duplicate_nhs, null_volume_nhs = (
                resolve_workflow_volumes(
                    on_date,
                    scenario,
                    projection_resolver=projection_resolver,
                    manual_overrides=manual_overrides,
                )
            )
    else:
        workflow_totals, workflow_meta = _projection_override
        duplicate_nhs, null_volume_nhs = [], []

    merge_workflow_meta_from_derivation(workflow_meta, derivation_rows)

    if restrict_documentoscopia:
        workflow_totals, workflow_meta = filter_documentoscopia_workflows(
            workflow_totals,
            workflow_meta,
        )
        if scenario.id == "planejamento_operacional":
            workflow_totals, volume_adjustments = apply_planejamento_volume_guard(
                workflow_totals,
                workflow_meta,
                on_date,
                max_ratio=PLANEJAMENTO_OPERACIONAL_MAX_RATIO,
            )
        else:
            volume_adjustments = []
    else:
        volume_adjustments = []

    blockers: list[dict] = []
    if duplicate_nhs:
        blockers.append(
            {
                "code": "projecao_duplicada",
                "message": "Existem projeções SLA duplicadas para o mesmo cliente, workflow e NH.",
                "count": len(duplicate_nhs),
            }
        )
    if null_volume_nhs:
        blockers.append(
            {
                "code": "projecao_sem_volume",
                "message": "Existem projeções SLA vigentes sem volume informado.",
                "count": len(null_volume_nhs),
            }
        )

    scenario_notes: list[str] = []
    if scenario.meta_mode == "operacao_avg" and not operational_metas:
        scenario_notes.append(
            "Média da operação indisponível para o trimestre; usando meta cadastrada vigente."
        )
    if scenario.volume_mode == "received_day":
        scenario_notes.append(
            "Volume baseado no consolidado do dia, com detalhe como fallback, não na projeção SLA."
        )
        if scenario.id == "operacao":
            scenario_notes.append(
                "Volume recebido restrito a workflows produto Documentoscopia."
            )
    if restrict_documentoscopia:
        scenario_notes.append(
            "Escopo restrito a workflows produto Documentoscopia."
        )
        if scenario.id == "planejamento":
            scenario_notes.append(
                "Volume contratual (Projeção SLA) sem ajuste operacional — referência de compromisso Megazord."
            )
        if scenario.id == "planejamento_operacional" and volume_adjustments:
            scenario_notes.append(
                f"{len(volume_adjustments)} workflow(s) com Projeção SLA acima de 2× a mediana "
                f"de recebido (60d) — volume substituído pela mediana diária."
            )

    demand_by_stage = defaultdict(lambda: ZERO)
    demand_by_stage_wf: dict[int, dict[tuple[int, int], Decimal]] = defaultdict(
        lambda: defaultdict(lambda: ZERO)
    )
    stage_meta: dict[int, dict] = {}
    workflows_by_stage = defaultdict(set)
    derivation_workflows = set()

    for row in derivation_rows:
        if not row.workflow.ind_considerar or not row.cliente.operations:
            continue
        workflow_key = (row.cliente_id, row.workflow_id)
        derivation_workflows.add(workflow_key)
        volume = workflow_totals.get(workflow_key, ZERO)
        if volume <= ZERO:
            continue
        demand = volume * _decimal(row.percentual) / HUNDRED
        demand_by_stage[row.etapa_id] += demand
        demand_by_stage_wf[row.etapa_id][workflow_key] += demand
        workflows_by_stage[row.etapa_id].add(workflow_key)
        stage_meta[row.etapa_id] = {
            "id_etapa": row.etapa_id,
            "etapa_nome": row.etapa.nome,
            "familia": extract_familia_normalized(row.etapa.nome),
        }

    uncovered_workflows = []
    for workflow_key, volume in workflow_totals.items():
        if volume <= ZERO or workflow_key in derivation_workflows:
            continue
        meta = workflow_meta[workflow_key]
        uncovered_workflows.append(
            {
                **meta,
                "volume": _serialize_decimal(volume),
            }
        )
    if uncovered_workflows:
        blockers.append(
            {
                "code": "workflow_sem_derivacao",
                "message": "Existem workflows projetados sem derivação na análise de referência.",
                "count": len(uncovered_workflows),
            }
        )

    demanded_stage_ids = set(demand_by_stage)
    period_range = (
        calculation_cache.get("period_date_range")
        if calculation_cache is not None
        else None
    )
    prepared_meta_rows = (
        _cache_value(
            calculation_cache,
            ("active_meta_rows", *period_range),
            lambda: _active_meta_rows_for_range(*period_range),
        )
        if period_range is not None
        else None
    )
    metas_by_stage = _cache_value(
        calculation_cache,
        ("active_metas", on_date, frozenset(demanded_stage_ids)),
        lambda: _active_metas(
            on_date,
            demanded_stage_ids,
            prepared_rows=prepared_meta_rows,
        ),
    )
    results = []
    total_agents = 0
    total_exact_fte = ZERO
    total_human_demand = ZERO
    total_automatic_demand = ZERO
    ambiguous_meta_count = 0
    exact_fte_by_stage: dict[int, Decimal] = {}

    for etapa_id, demand in demand_by_stage.items():
        info = stage_meta[etapa_id]
        candidates = metas_by_stage.get(etapa_id, [])
        base = {
            **info,
            "analises_previstas": _serialize_decimal(demand),
            "workflow_count": len(workflows_by_stage[etapa_id]),
        }

        manual_meta = manual_metas.get(etapa_id) if scenario.meta_mode == "manual" else None

        if not candidates and manual_meta is None:
            total_automatic_demand += demand
            results.append(
                {
                    **base,
                    "status": "automatica",
                    "meta_dia": None,
                    "fte": None,
                    "agentes_necessarios": 0,
                    "capacidade_planejada": None,
                    "folga_capacidade": None,
                }
            )
            continue

        if len(candidates) > 1 and manual_meta is None:
            ambiguous_meta_count += 1
            results.append(
                {
                    **base,
                    "status": "meta_ambigua",
                    "meta_dia": None,
                    "fte": None,
                    "agentes_necessarios": None,
                    "capacidade_planejada": None,
                    "folga_capacidade": None,
                    "meta_candidates": [
                        {
                            "id": item.pk,
                            "meta_dia": _serialize_decimal(_decimal(item.meta_dia)),
                            "id_servico": item.servico_id,
                            "servico_nome": item.servico.nome if item.servico else None,
                        }
                        for item in candidates
                    ],
                }
            )
            continue

        if manual_meta is not None:
            meta = manual_meta
        elif scenario.meta_mode == "operacao_avg" and etapa_id in operational_metas:
            meta = operational_metas[etapa_id]
        else:
            meta = _decimal(candidates[0].meta_dia)
        if meta <= ZERO:
            ambiguous_meta_count += 1
            results.append(
                {
                    **base,
                    "status": "meta_invalida",
                    "meta_dia": _serialize_decimal(meta),
                    "fte": None,
                    "agentes_necessarios": None,
                    "capacidade_planejada": None,
                    "folga_capacidade": None,
                }
            )
            continue

        fte = demand / meta
        agents = int(fte.to_integral_value(rounding=ROUND_CEILING))
        capacity = meta * agents
        slack = capacity - demand
        total_agents += agents
        total_exact_fte += fte
        exact_fte_by_stage[etapa_id] = fte
        total_human_demand += demand
        results.append(
            {
                **base,
                "status": "dimensionada",
                "meta_dia": _serialize_decimal(meta),
                "fte": _serialize_decimal(fte, "0.0001"),
                "agentes_necessarios": agents,
                "capacidade_planejada": _serialize_decimal(capacity),
                "folga_capacidade": _serialize_decimal(slack),
            }
        )

    if ambiguous_meta_count:
        blockers.append(
            {
                "code": "meta_ambigua_ou_invalida",
                "message": "Existem etapas com mais de uma meta vigente ou com meta inválida.",
                "count": ambiguous_meta_count,
            }
        )

    results.sort(
        key=lambda item: (
            item["status"] != "dimensionada",
            -(item["agentes_necessarios"] or 0),
            item["etapa_nome"].casefold(),
        )
    )
    dimensioned_results = [row for row in results if row["status"] == "dimensionada"]
    _reconcile_serialized_total(dimensioned_results, "fte", total_exact_fte)
    projection_volume = sum(workflow_totals.values(), ZERO)
    fte_by_familia: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for row in results:
        if row["status"] == "dimensionada":
            fte_by_familia[row["familia"]] += _decimal(row["fte"])
    family_pooled_headcount = sum(
        int(value.to_integral_value(rounding=ROUND_CEILING))
        for value in fte_by_familia.values()
        if value > ZERO
    )
    uncovered_projection_volume = sum(
        (_decimal(item["volume"]) for item in uncovered_workflows),
        ZERO,
    )
    covered_projection_volume = projection_volume - uncovered_projection_volume
    import_user = run.triggered_by if run else None

    if include_details:
        by_familia, by_client, by_workflow, breakdown, breakdown_truncated = _build_capacity_aggregations(
            results=results,
            demand_by_stage=demand_by_stage,
            demand_by_stage_wf=demand_by_stage_wf,
            workflow_totals=workflow_totals,
            workflow_meta=workflow_meta,
            total_exact_fte=total_exact_fte,
            exact_fte_by_stage=exact_fte_by_stage,
        )
    else:
        by_familia, by_client, by_workflow, breakdown = [], [], [], []
        breakdown_truncated = False

    hourly = None
    if include_hourly:
        if profile_from is None or profile_to is None:
            profile_from, profile_to = default_profile_window(on_date)
        workflow_keys = {key for key, volume in workflow_totals.items() if volume > ZERO}
        profile_cache_key = (frozenset(workflow_keys), profile_from, profile_to)
        cached_profile = (
            hourly_profile_cache.get(profile_cache_key)
            if hourly_profile_cache is not None
            else None
        )
        if cached_profile is None:
            cached_profile = build_workflow_hourly_profiles(
                workflow_keys,
                on_date=on_date,
                profile_from=profile_from,
                profile_to=profile_to,
            )
            if hourly_profile_cache is not None:
                hourly_profile_cache[profile_cache_key] = cached_profile
        profiles, missing_profile_keys, profile_quality = cached_profile
        hourly = build_hourly_capacity(
            on_date=on_date,
            workflow_totals=workflow_totals,
            workflow_meta=workflow_meta,
            demand_by_stage=demand_by_stage,
            demand_by_stage_wf=demand_by_stage_wf,
            stage_results=results,
            profiles=profiles,
            profile_from=profile_from,
            profile_to=profile_to,
            workflows_without_profile=missing_profile_keys,
            profile_quality=profile_quality,
        )

    legacy_payload = {
        "ok": True,
        "ready": not blockers,
        "calculation_date": on_date.isoformat(),
        "scenario": {
            **scenario_public_payload(scenario),
            "notes": scenario_notes,
        },
        "analysis": {
            "import_run_id": run.pk if run else None,
            "scan_run_id": run.reviewed_scan_id if run else None,
            "period_from": run.period_from.isoformat() if run and run.period_from else None,
            "period_to": run.period_to.isoformat() if run and run.period_to else None,
            "reference_date": reference_date.isoformat(),
            "derivation_age_days": (on_date - reference_date).days,
            "derivation_reused": reference_date < on_date,
            "approved_at": run.finished_at.isoformat() if run and run.finished_at else None,
            "approved_by": import_user.get_username() if import_user else None,
            **derivation_analysis_extra,
        },
        "summary": {
            "projected_workflow_volume": _serialize_decimal(projection_volume),
            "workflow_count": len(workflow_totals),
            "covered_workflow_count": len(workflow_totals) - len(uncovered_workflows),
            "uncovered_workflow_count": len(uncovered_workflows),
            "covered_projected_volume": _serialize_decimal(covered_projection_volume),
            "uncovered_projected_volume": _serialize_decimal(uncovered_projection_volume),
            "derivation_coverage_pct": _serialize_decimal(
                covered_projection_volume / projection_volume * HUNDRED
                if projection_volume > ZERO
                else ZERO
            ),
            "stage_count": len(results),
            "dimensioned_stage_count": sum(1 for row in results if row["status"] == "dimensionada"),
            "automatic_stage_count": sum(1 for row in results if row["status"] == "automatica"),
            "human_analyses": _serialize_decimal(total_human_demand),
            "automatic_analyses": _serialize_decimal(total_automatic_demand),
            "agents_required": total_agents,
            "exact_fte_before_rounding": _serialize_decimal(total_exact_fte, "0.0001"),
            "rounding_addition_headcount": _serialize_decimal(
                Decimal(total_agents) - total_exact_fte,
                "0.0001",
            ),
            "fully_pooled_headcount": (
                int(total_exact_fte.to_integral_value(rounding=ROUND_CEILING))
                if total_exact_fte > ZERO
                else 0
            ),
            "family_pooled_headcount": family_pooled_headcount,
            "stages_below_one_fte": sum(
                1
                for row in results
                if row["status"] == "dimensionada" and ZERO < _decimal(row["fte"]) < 1
            ),
            "stages_rounded_to_one": sum(
                1
                for row in results
                if row["status"] == "dimensionada"
                and row["agentes_necessarios"] == 1
                and ZERO < _decimal(row["fte"]) < 1
            ),
            "dimensioned_analyses": _serialize_decimal(total_human_demand),
        },
        "units": {
            "projected_workflow_volume": "cases_per_day",
            "agents_required": "people_per_day",
            "hourly_peak_capacity": "fractional_people",
        },
        "results": results,
        "derivations": [
            {
                "id_cliente": int(row.cliente_id),
                "cliente_nome": row.cliente.nome,
                "id_workflow": int(row.workflow_id),
                "workflow_nome": row.workflow.nome,
                "id_etapa": int(row.etapa_id),
                "etapa_nome": row.etapa.nome,
                "familia": extract_familia_normalized(row.etapa.nome),
                "percentual": _serialize_decimal(_decimal(row.percentual), "0.0001"),
            }
            for row in derivation_rows
            if row.workflow.ind_considerar and row.cliente.operations
        ] if include_details else [],
        "by_familia": by_familia,
        "by_client": by_client,
        "by_workflow": by_workflow,
        "breakdown": breakdown,
        "breakdown_truncated": breakdown_truncated,
        "hourly": hourly,
        "blockers": blockers,
        "uncovered_workflows": uncovered_workflows,
        "volume_adjustments": volume_adjustments if include_details else [],
        "duplicate_projection_nhs": duplicate_nhs,
        "null_volume_nhs": null_volume_nhs,
        "explainability": {
            "headcount_formula": "sum_by_stage(ceil(stage_demand / stage_daily_goal))",
            "rounding_grain": "stage",
            "top_capacity_drivers": [
                {
                    "id_etapa": row["id_etapa"],
                    "etapa_nome": row["etapa_nome"],
                    "analises_previstas": row["analises_previstas"],
                    "meta_dia": row["meta_dia"],
                    "fte": row["fte"],
                    "agentes_necessarios": row["agentes_necessarios"],
                }
                for row in results
                if row["status"] == "dimensionada"
            ][:10],
        },
    }

    if include_quarterly and _projection_override is None:
        derivation_keys = {
            (row.cliente_id, row.workflow_id)
            for row in derivation_rows
            if row.workflow.ind_considerar and row.cliente.operations
        }
        quarterly_base = build_quarterly_weekday_reference(
            on_date,
            cache=quarterly_volume_cache,
        )
        if not quarterly_base.get("available"):
            legacy_payload["quarterly_reference"] = {
                "availability": "unavailable",
                "scenario": "quarterly_weekday_reference",
                "official_projection_replaced": False,
                "reason": quarterly_base.get("reason"),
                "projected_workflow_volume": None,
                "weekly_projection_volume": None,
                "agents_required": None,
                "exact_fte_before_rounding": None,
                "coverage_pct": None,
                "historical_coverage_pct": None,
                "ready": False,
            }
            if not include_dax:
                return legacy_payload
            # O DAX continua independente da referencia trimestral operacional.
        else:
            quarterly_totals = quarterly_base["workflow_totals"]
            derivation_meta = {
                (row.cliente_id, row.workflow_id): {
                    "id_cliente": row.cliente_id,
                    "cliente_nome": row.cliente.nome,
                    "id_workflow": row.workflow_id,
                    "workflow_nome": row.workflow.nome,
                    "nh_count": 0,
                }
                for row in derivation_rows
            }
            quarterly_source_meta = quarterly_base["workflow_meta"]
            quarterly_meta = {
                key: workflow_meta.get(key)
                or derivation_meta.get(key)
                or quarterly_source_meta[key]
                for key in quarterly_totals
            }
            quarterly_daily = calculate_daily_capacity(
                on_date,
                include_details=False,
                include_dax=False,
                include_quarterly=False,
                hourly_profile_cache=hourly_profile_cache,
                _projection_override=(quarterly_totals, quarterly_meta),
                _prepared_derivation=(run, reference_date, derivation_rows),
            )
            legacy_payload["quarterly_reference"] = {
                "availability": "available",
                "scenario": "quarterly_weekday_reference",
                "official_projection_replaced": False,
                "volume_basis": "weekly_projection_sla_redistributed_by_historical_weekday_share",
                "history_source": "sla_util_consolidado",
                "weekday": on_date.weekday(),
                "window_from": quarterly_base["window_from"].isoformat(),
                "window_to": quarterly_base["window_to"].isoformat(),
                "eligible_dates": quarterly_base["eligible_dates"],
                "observed_date_coverage_pct": _serialize_decimal(
                    quarterly_base["coverage_pct"]
                ),
                "historical_grain_count": quarterly_base["historical_grain_count"],
                "workflow_fallback_grain_count": quarterly_base[
                    "workflow_fallback_grain_count"
                ],
                "fallback_grain_count": quarterly_base["fallback_grain_count"],
                "exact_nh_coverage_pct": _serialize_decimal(
                    quarterly_base["exact_nh_coverage_pct"]
                ),
                "workflow_fallback_coverage_pct": _serialize_decimal(
                    quarterly_base["workflow_fallback_coverage_pct"]
                ),
                "historical_coverage_pct": _serialize_decimal(
                    quarterly_base["historical_coverage_pct"]
                ),
                "coverage_pct": _serialize_decimal(
                    quarterly_base["historical_coverage_pct"]
                ),
                "projection_fallback_pct": _serialize_decimal(
                    quarterly_base["projection_fallback_pct"]
                ),
                "fallback": "official_daily_projection_sla",
                "projected_workflow_volume": _serialize_decimal(
                    sum(quarterly_totals.values(), ZERO), "0.0001"
                ),
                "weekly_projection_volume": _serialize_decimal(
                    quarterly_base["weekly_projection_volume"], "0.0001"
                ),
                "agents_required": quarterly_daily["summary"]["agents_required"],
                "exact_fte_before_rounding": quarterly_daily["summary"][
                    "exact_fte_before_rounding"
                ],
                "rounding_addition_headcount": quarterly_daily["summary"][
                    "rounding_addition_headcount"
                ],
                "derivation_coverage_pct": quarterly_daily["summary"][
                    "derivation_coverage_pct"
                ],
                "ready": quarterly_daily["ready"]
                and quarterly_base["fallback_grain_count"] == 0,
                "blockers": quarterly_daily["blockers"],
            }

    if not include_dax:
        return legacy_payload

    try:
        dax_payload = build_dax_capacity_response(on_date, scenario_id=scenario_id)
        comparative = build_capacity_comparative(legacy_payload=legacy_payload, dax_payload=dax_payload)
    except Exception as exc:  # noqa: BLE001 — DAX não deve derrubar o legado
        logging.getLogger(__name__).exception("Falha ao calcular Capacity DAX para %s", on_date)
        dax_payload = {
            "model": "dax_pbi",
            "label": "DAX / Power BI (capacity fracionada)",
            "availability": "unavailable",
            "error": str(exc),
            "units": {
                "capacity_esperada_total": "agent_hours_equivalent",
                "capacity_recebida_total": "agent_hours_equivalent",
                "peak_capacity_esperada": "fractional_people",
            },
            "summary": {
                "capacity_esperada_total": None,
                "capacity_recebida_total": None,
                "peak_hour_esperada": None,
                "peak_capacity_esperada": None,
                "stage_count": None,
                "grain_row_count": None,
                "derivation_lookback_days": 60,
                "meta_hours_divisor": "5.5",
                "volume_source": "Volume Hora Contrato (curva 3 meses consolidado SLA)",
            },
            "results": [],
            "by_familia": [],
            "by_hour": [],
        }
        comparative = None

    if hourly and dax_payload and not dax_payload.get("error"):
        dax_by_hour = dax_payload.get("by_hour") or []
        peak_recebida = ZERO
        peak_recebida_hour = None
        for row in dax_by_hour:
            cap = Decimal(str(row.get("capacity_recebida") or 0))
            if cap > peak_recebida:
                peak_recebida = cap
                peak_recebida_hour = row["hour"]
        hourly["dax"] = {
            "scope": "dimensionada",
            "by_hour": dax_by_hour,
            "peak_capacity_esperada": dax_payload["summary"]["peak_capacity_esperada"],
            "peak_hour_esperada": dax_payload["summary"]["peak_hour_esperada"],
            "peak_capacity_recebida": str(peak_recebida.quantize(Decimal("0.0001"))),
            "peak_hour_recebida": peak_recebida_hour,
        }

    return {
        **legacy_payload,
        "legacy_model": {
            "label": "Portal legado",
            "summary": legacy_payload["summary"],
            "hourly": hourly,
        },
        "dax": dax_payload,
        "comparative": comparative,
    }


def _peak_at(on_date: date, hour: int | None) -> str | None:
    if hour is None:
        return None
    value = datetime.combine(on_date, time(hour=int(hour)))
    return timezone.make_aware(value, timezone.get_current_timezone()).isoformat()


def calculate_capacity_period_day(
    on_date: date,
    *,
    hourly_profile_cache: dict | None = None,
    quarterly_volume_cache: dict | None = None,
    include_quarterly: bool = False,
    scenario_id: str = "planejamento",
    manual_overrides: dict | None = None,
    calculation_cache: dict | None = None,
) -> dict:
    """Calcula um item materializavel do periodo pela regra diaria canonica."""

    try:
        daily = calculate_daily_capacity(
            on_date,
            include_details=False,
            include_dax=False,
            hourly_profile_cache=hourly_profile_cache,
            include_quarterly=include_quarterly,
            quarterly_volume_cache=quarterly_volume_cache,
            scenario_id=scenario_id,
            manual_overrides=manual_overrides,
            calculation_cache=calculation_cache,
        )
    except CapacityUnavailableError as exc:
        blocker = {
            "date": on_date.isoformat(),
            "code": "capacity_unavailable",
            "message": str(exc),
            "count": 1,
        }
        return {
            "series": {
                "date": on_date.isoformat(),
                "metric_version": CAPACITY_PERIOD_METRIC_VERSION,
                "ready": False,
                "daily_headcount": None,
                "fully_pooled_headcount": None,
                "family_pooled_headcount": None,
                "exact_fte_before_rounding": None,
                "projected_volume": None,
                "covered_projected_volume": None,
                "uncovered_projected_volume": None,
                "derivation_coverage_pct": None,
                "peak_concurrent_fte": None,
                "peak_hour": None,
                "blockers_count": 1,
                "warnings_count": 0,
                "quarterly_reference_volume": None,
                "quarterly_reference_headcount": None,
                "quarterly_reference_exact_fte": None,
                "quarterly_reference_coverage_pct": None,
            },
            "blockers": [blocker],
            "warnings": [],
        }

    blockers = daily.get("blockers") or []
    quality_blockers = [{"date": on_date.isoformat(), **item} for item in blockers]
    hourly = daily.get("hourly") or {}
    missing_profiles = int(hourly.get("workflows_without_profile_count") or 0)
    quality_warnings = []
    if missing_profiles:
        quality_warnings.append(
            {
                "date": on_date.isoformat(),
                "code": "perfil_horario_ausente",
                "message": "Workflows sem historico horario usam distribuicao uniforme.",
                "count": missing_profiles,
            }
        )
    if not include_quarterly:
        quality_warnings.append(
            {
                "date": on_date.isoformat(),
                "code": "quarterly_snapshot_missing",
                "message": "Referencia trimestral depende do snapshot pre-calculado.",
                "count": 1,
            }
        )
    elif (daily.get("quarterly_reference") or {}).get("availability") == "unavailable":
        quality_warnings.append(
            {
                "date": on_date.isoformat(),
                "code": "quarterly_history_unavailable",
                "message": "Nenhum trimestre calendario completo foi encontrado no monitoramento SLA.",
                "count": 1,
            }
        )

    # `ready` indica confiabilidade para decisao; nao indica existencia do
    # resultado. Blockers de qualidade nao podem apagar numeros calculados.
    decision_ready = bool(daily.get("ready")) and missing_profiles == 0
    quarterly = daily.get("quarterly_reference") or {}
    return {
        "series": {
            "date": on_date.isoformat(),
            "metric_version": CAPACITY_PERIOD_METRIC_VERSION,
            "ready": decision_ready,
            "daily_headcount": daily["summary"]["agents_required"],
            "fully_pooled_headcount": daily["summary"]["fully_pooled_headcount"],
            "family_pooled_headcount": daily["summary"]["family_pooled_headcount"],
            "projected_volume": daily["summary"]["projected_workflow_volume"],
            "covered_projected_volume": daily["summary"]["covered_projected_volume"],
            "uncovered_projected_volume": daily["summary"]["uncovered_projected_volume"],
            "derivation_coverage_pct": daily["summary"]["derivation_coverage_pct"],
            "peak_concurrent_fte": hourly.get("peak_capacity_hora"),
            "peak_hour": hourly.get("peak_hour"),
            "blockers_count": len(blockers),
            "warnings_count": len(quality_warnings),
            "exact_fte_before_rounding": daily["summary"]["exact_fte_before_rounding"],
            "rounding_addition_headcount": daily["summary"]["rounding_addition_headcount"],
            "dimensioned_stage_count": daily["summary"]["dimensioned_stage_count"],
            "dimensioned_analyses": daily["summary"]["dimensioned_analyses"],
            "derivation_reference_date": daily["analysis"]["reference_date"],
            "derivation_age_days": daily["analysis"]["derivation_age_days"],
            "derivation_reused": daily["analysis"]["derivation_reused"],
            "quarterly_reference_volume": quarterly.get("projected_workflow_volume"),
            "quarterly_reference_headcount": quarterly.get("agents_required"),
            "quarterly_reference_exact_fte": quarterly.get("exact_fte_before_rounding"),
            "quarterly_reference_coverage_pct": quarterly.get("coverage_pct"),
            "quarterly_reference": quarterly or None,
        },
        "blockers": quality_blockers,
        "warnings": quality_warnings,
    }


def _summarize_capacity_period(
    date_from: date,
    date_to: date,
    day_payloads: list[dict],
    *,
    snapshot_meta: dict,
    scenario_id: str = "planejamento",
) -> dict:
    day_count = (date_to - date_from).days + 1
    series = [item["series"] for item in day_payloads]
    quality_blockers = [row for item in day_payloads for row in item.get("blockers", [])]
    quality_warnings = [row for item in day_payloads for row in item.get("warnings", [])]
    calculated_rows = [row for row in series if row["daily_headcount"] is not None]
    decision_ready_rows = [row for row in series if row["ready"]]
    peak_row = max(
        calculated_rows,
        key=lambda row: _decimal(row["peak_concurrent_fte"]),
        default=None,
    )
    daily_headcounts = [int(row["daily_headcount"]) for row in calculated_rows]
    exact_ftes = [
        _decimal(row["exact_fte_before_rounding"])
        for row in calculated_rows
        if row.get("exact_fte_before_rounding") is not None
    ]
    fully_pooled_headcounts = [
        int(row["fully_pooled_headcount"])
        for row in calculated_rows
        if row.get("fully_pooled_headcount") is not None
    ]
    family_pooled_headcounts = [
        int(row["family_pooled_headcount"])
        for row in calculated_rows
        if row.get("family_pooled_headcount") is not None
    ]
    projected_total = sum(
        (_decimal(row["projected_volume"]) for row in calculated_rows),
        ZERO,
    )
    covered_projected_total = sum(
        (_decimal(row.get("covered_projected_volume")) for row in calculated_rows),
        ZERO,
    )
    uncovered_projected_total = sum(
        (_decimal(row.get("uncovered_projected_volume")) for row in calculated_rows),
        ZERO,
    )
    average = (
        Decimal(sum(daily_headcounts)) / Decimal(len(daily_headcounts))
        if daily_headcounts
        else None
    )
    quarterly_rows = [
        row
        for row in calculated_rows
        if row.get("quarterly_reference_headcount") is not None
    ]
    quarterly_headcounts = [int(row["quarterly_reference_headcount"]) for row in quarterly_rows]
    quarterly_volume_total = sum(
        (_decimal(row["quarterly_reference_volume"]) for row in quarterly_rows), ZERO
    )
    quarterly_fte_total = sum(
        (_decimal(row["quarterly_reference_exact_fte"]) for row in quarterly_rows), ZERO
    )
    return {
        "ok": True,
        "ready": len(decision_ready_rows) == day_count,
        "meta": {
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "day_count": day_count,
            "timezone": timezone.get_current_timezone_name(),
            "units": {
                "daily_headcount": "people_per_day",
                "average_daily_headcount": "people_per_day",
                "exact_fte_before_rounding": "full_time_equivalent_per_day",
                "fully_pooled_headcount": "people_per_day",
                "family_pooled_headcount": "people_per_day",
                "peak_concurrent_fte": "fractional_people",
                "projected_volume": "cases",
                "covered_projected_volume": "cases",
                "uncovered_projected_volume": "cases",
                "derivation_coverage_pct": "percent",
            },
            "snapshot": snapshot_meta,
            "scenario": scenario_public_payload(scenario_config(scenario_id)),
        },
        "summary": {
            "max_daily_headcount": max(daily_headcounts) if daily_headcounts else None,
            "average_daily_headcount": (
                _serialize_decimal(average) if average is not None else None
            ),
            "max_daily_exact_fte": (
                _serialize_decimal(max(exact_ftes), "0.0001") if exact_ftes else None
            ),
            "average_daily_exact_fte": (
                _serialize_decimal(
                    sum(exact_ftes, ZERO) / Decimal(len(exact_ftes)), "0.0001"
                )
                if exact_ftes
                else None
            ),
            "max_fully_pooled_headcount": (
                max(fully_pooled_headcounts) if fully_pooled_headcounts else None
            ),
            "average_fully_pooled_headcount": (
                _serialize_decimal(
                    Decimal(sum(fully_pooled_headcounts))
                    / Decimal(len(fully_pooled_headcounts))
                )
                if fully_pooled_headcounts
                else None
            ),
            "max_family_pooled_headcount": (
                max(family_pooled_headcounts) if family_pooled_headcounts else None
            ),
            "average_family_pooled_headcount": (
                _serialize_decimal(
                    Decimal(sum(family_pooled_headcounts))
                    / Decimal(len(family_pooled_headcounts))
                )
                if family_pooled_headcounts
                else None
            ),
            "peak_concurrent_fte": peak_row["peak_concurrent_fte"] if peak_row else None,
            "peak_at": (
                _peak_at(date.fromisoformat(peak_row["date"]), peak_row["peak_hour"])
                if peak_row
                else None
            ),
            "projected_volume_total": (
                _serialize_decimal(projected_total) if calculated_rows else None
            ),
            "covered_projected_volume_total": (
                _serialize_decimal(covered_projected_total) if calculated_rows else None
            ),
            "uncovered_projected_volume_total": (
                _serialize_decimal(uncovered_projected_total) if calculated_rows else None
            ),
            "derivation_coverage_pct": (
                _serialize_decimal(
                    covered_projected_total / projected_total * HUNDRED
                    if projected_total > ZERO
                    else ZERO
                )
                if calculated_rows
                else None
            ),
            "ready_days": len(decision_ready_rows),
            "issue_days": day_count - len(decision_ready_rows),
            "quarterly_reference_max_headcount": (
                max(quarterly_headcounts) if quarterly_headcounts else None
            ),
            "quarterly_reference_average_headcount": (
                _serialize_decimal(
                    Decimal(sum(quarterly_headcounts)) / Decimal(len(quarterly_headcounts))
                )
                if quarterly_headcounts
                else None
            ),
            "quarterly_reference_volume_total": (
                _serialize_decimal(quarterly_volume_total) if quarterly_rows else None
            ),
            "quarterly_reference_exact_fte_total": (
                _serialize_decimal(quarterly_fte_total, "0.0001")
                if quarterly_rows
                else None
            ),
        },
        "series": series,
        "quality": {"blockers": quality_blockers, "warnings": quality_warnings},
    }


def calculate_capacity_period(
    date_from: date,
    date_to: date,
    *,
    scenario_id: str = "planejamento",
    manual_overrides: dict | None = None,
    force_live: bool = False,
    calculation_cache: dict | None = None,
    expected_source_fingerprint: str | None = None,
) -> dict:
    """Monta ate 31 dias por snapshots diarios, com fallback canonico seguro.

    Somente snapshots vigentes sao usados. Datas nunca materializadas ou com
    snapshot vencido usam o calculo ao vivo e ficam explicitas como
    `fallback_live`; isso garante que periodo e detalhe partam da mesma analise
    aprovada. O comando de sync deve ser agendado para eliminar esse caminho da
    abertura normal.
    """

    day_count = (date_to - date_from).days + 1
    if day_count < 1 or day_count > 31:
        raise ValueError("O periodo deve conter entre 1 e 31 dias.")

    now = timezone.now()
    use_snapshot = not force_live and not (scenario_id == "manual" and manual_overrides)
    snapshot_scenario_id = (
        "planejamento"
        if scenario_id == "manual" and not manual_overrides
        else scenario_id
    )
    expected_source_fingerprint = (
        expected_source_fingerprint
        or current_capacity_source_fingerprint(
            date_from,
            date_to,
            scenario_id=snapshot_scenario_id,
        )
        if use_snapshot
        else None
    )
    snapshot_rows = list(
        CapacityDailySnapshot.objects.filter(
            calculation_date__range=(date_from, date_to),
            scenario_id=snapshot_scenario_id,
            **(
                {"source_fingerprint": expected_source_fingerprint}
                if expected_source_fingerprint is not None
                else {}
            ),
        ).order_by("-generated_at", "calculation_date")
    )
    compatible_by_generation: dict[str, dict[date, CapacityDailySnapshot]] = defaultdict(dict)
    generation_generated_at: dict[str, datetime] = {}
    for row in snapshot_rows:
        if (
            row.valid_until > now
            and row.metric_version == CAPACITY_PERIOD_METRIC_VERSION
            and (row.payload.get("series") or {}).get("metric_version")
            == CAPACITY_PERIOD_METRIC_VERSION
        ):
            generation_key = str(row.generation_id)
            compatible_by_generation[generation_key][row.calculation_date] = row
            generation_generated_at[generation_key] = max(
                generation_generated_at.get(generation_key, row.generated_at),
                row.generated_at,
            )
    required_dates = {
        date_from + timedelta(days=offset)
        for offset in range(day_count)
    }
    complete_generations = [
        generation_key
        for generation_key, rows_by_date in compatible_by_generation.items()
        if required_dates.issubset(rows_by_date)
    ]
    selected_generation = (
        max(complete_generations, key=lambda key: generation_generated_at[key])
        if complete_generations
        else None
    )
    snapshots = (
        compatible_by_generation[selected_generation]
        if selected_generation is not None
        else {}
    )
    calculation_cache = calculation_cache if calculation_cache is not None else {}
    calculation_cache.setdefault("period_date_range", (date_from, date_to))
    profile_cache: dict = calculation_cache.setdefault("hourly_profile_cache", {})
    quarterly_cache: dict = calculation_cache.setdefault("quarterly_volume_cache", {})
    day_payloads: list[dict] = []
    fresh_dates: list[str] = []
    stale_dates: list[str] = []
    fallback_dates: list[str] = []
    generation_ids: set[str] = set()
    generated_at_values: list[datetime] = []
    valid_until_values: list[datetime] = []

    # O Cenário 3 é um overlay do Planejamento: sem ajustes, ambos precisam
    # compartilhar exatamente o mesmo baseline materializado.
    for offset in range(day_count):
        on_date = date_from + timedelta(days=offset)
        snapshot = snapshots.get(on_date)
        if use_snapshot and snapshot is not None:
            generation_ids.add(str(snapshot.generation_id))
            generated_at_values.append(snapshot.generated_at)
            valid_until_values.append(snapshot.valid_until)
            fresh_dates.append(on_date.isoformat())
            day_payloads.append(snapshot.payload)
            continue
        if use_snapshot and any(row.calculation_date == on_date for row in snapshot_rows):
            stale_dates.append(on_date.isoformat())

        fallback_dates.append(on_date.isoformat())
        day_payloads.append(
            calculate_capacity_period_day(
                on_date,
                hourly_profile_cache=profile_cache,
                quarterly_volume_cache=quarterly_cache,
                scenario_id=scenario_id,
                manual_overrides=manual_overrides,
                calculation_cache=calculation_cache,
            )
        )

    state = "fallback_live" if fallback_dates else "fresh"
    snapshot_meta = {
        "state": state,
        "fresh_days": len(fresh_dates),
        "stale_days": len(stale_dates),
        "fallback_live_days": len(fallback_dates),
        "stale_dates": stale_dates,
        "fallback_live_dates": fallback_dates,
        "generation_ids": sorted(generation_ids),
        "scenario_id": snapshot_scenario_id,
        "metric_version": CAPACITY_PERIOD_METRIC_VERSION,
        "source_fingerprint": (
            next(iter({row.source_fingerprint for row in snapshots.values()}), None)
            if snapshots
            else None
        ),
        "generated_at": (
            max(generated_at_values).isoformat() if generated_at_values else None
        ),
        "valid_until": (
            min(valid_until_values).isoformat() if valid_until_values else None
        ),
    }
    return _summarize_capacity_period(
        date_from,
        date_to,
        day_payloads,
        snapshot_meta=snapshot_meta,
        scenario_id=scenario_id,
    )
