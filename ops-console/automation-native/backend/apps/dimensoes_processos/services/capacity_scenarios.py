"""Presets de visualizacao do Capacity (projecao-equipes)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Avg, Count, Q, Sum

from apps.dimensoes_processos.models import (
    DerivacaoEtapaDiaria,
    DimCliente,
    DimWorkflow,
    MetaEtapa,
)
from apps.dimensoes_processos.services.capacity_errors import CapacityUnavailableError
from apps.dimensoes_processos.services.capacity_quarterly import _select_complete_quarter
from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilDetalhe

ZERO = Decimal("0")
VALID_SCENARIO_IDS = frozenset({"planejamento", "planejamento_operacional", "operacao", "manual"})
PLANEJAMENTO_PRODUTO_TIPO = "Documentoscopia"
DERIVATION_MEDIAN_LOOKBACK_DAYS = 60
PLANEJAMENTO_OPERACIONAL_MAX_RATIO = Decimal("2")


@dataclass(frozen=True)
class CapacityScenarioConfig:
    id: str
    label: str
    volume_mode: str
    meta_mode: str
    derivation_mode: str
    volume_label: str
    meta_label: str
    derivation_label: str


SCENARIO_PRESETS: dict[str, CapacityScenarioConfig] = {
    "planejamento": CapacityScenarioConfig(
        id="planejamento",
        label="Planejamento (Contratual)",
        volume_mode="projecao_sla",
        meta_mode="catalog_100",
        derivation_mode="median_60d",
        volume_label="Projeção SLA contratual (Documentoscopia)",
        meta_label="100% da meta cadastrada",
        derivation_label="Mediana 60d (exclui o dia)",
    ),
    "planejamento_operacional": CapacityScenarioConfig(
        id="planejamento_operacional",
        label="Planejamento (Operacional)",
        volume_mode="projecao_sla",
        meta_mode="catalog_100",
        derivation_mode="median_60d",
        volume_label="Projeção SLA ajustada pela mediana recebido (2×)",
        meta_label="100% da meta cadastrada",
        derivation_label="Mediana 60d (exclui o dia)",
    ),
    "operacao": CapacityScenarioConfig(
        id="operacao",
        label="Operação",
        volume_mode="received_day",
        meta_mode="operacao_avg",
        derivation_mode="day",
        volume_label="Recebido do dia (Documentoscopia)",
        meta_label="Média da operação",
        derivation_label="Do dia; na ausÃªncia, Ãºltima anÃ¡lise aprovada aplicÃ¡vel",
    ),
    "manual": CapacityScenarioConfig(
        id="manual",
        label="Simulação",
        volume_mode="manual",
        meta_mode="manual",
        derivation_mode="manual",
        volume_label="Volume ajustado por workflow",
        meta_label="Meta cadastrada (padrão)",
        derivation_label="Última análise aprovada (padrão)",
    ),
}


def normalize_scenario_id(raw: str | None) -> str:
    value = (raw or "planejamento").strip().casefold()
    aliases = {
        "1": "planejamento",
        "1b": "planejamento_operacional",
        "2": "operacao",
        "3": "manual",
        "planning": "planejamento",
        "planning_operational": "planejamento_operacional",
        "operacional": "planejamento_operacional",
        "operation": "operacao",
        "simulacao": "manual",
        "simulation": "manual",
    }
    return aliases.get(value, value if value in VALID_SCENARIO_IDS else "planejamento")


def scenario_config(scenario_id: str | None) -> CapacityScenarioConfig:
    return SCENARIO_PRESETS[normalize_scenario_id(scenario_id)]


def is_documentoscopia_workflow(workflow) -> bool:
    produto = getattr(workflow, "produto", None)
    if produto is None:
        return False
    return getattr(produto, "tipo_produto", "") == PLANEJAMENTO_PRODUTO_TIPO


def documentoscopia_workflow_ids(workflow_ids: set[int] | list[int]) -> set[int]:
    if not workflow_ids:
        return set()
    return set(
        DimWorkflow.objects.filter(
            id_workflow__in=workflow_ids,
            produto__tipo_produto=PLANEJAMENTO_PRODUTO_TIPO,
        ).values_list("id_workflow", flat=True)
    )


def filter_documentoscopia_workflows(
    workflow_totals: dict[tuple[int, int], Decimal],
    workflow_meta: dict[tuple[int, int], dict],
) -> tuple[dict[tuple[int, int], Decimal], dict[tuple[int, int], dict]]:
    allowed = documentoscopia_workflow_ids({key[1] for key in workflow_totals})
    filtered_totals = {key: value for key, value in workflow_totals.items() if key[1] in allowed}
    filtered_meta = {key: value for key, value in workflow_meta.items() if key[1] in allowed}
    return filtered_totals, filtered_meta


def scenario_public_payload(config: CapacityScenarioConfig) -> dict:
    payload = {
        "id": config.id,
        "label": config.label,
        "volume": {"mode": config.volume_mode, "label": config.volume_label},
        "meta": {"mode": config.meta_mode, "label": config.meta_label},
        "derivation": {"mode": config.derivation_mode, "label": config.derivation_label},
    }
    if config.id == "operacao":
        payload["derivation"].update(
            {
                "fallback_mode": "latest_approved_reference",
                "fallback_label": "Ãšltima anÃ¡lise aprovada aplicÃ¡vel",
            }
        )
    return payload


def _previous_complete_quarter(on_date: date) -> tuple[date, date] | None:
    return _select_complete_quarter(on_date, cache=None)


def enrich_workflow_meta_from_catalog(
    workflow_meta: dict[tuple[int, int], dict],
) -> dict[tuple[int, int], dict]:
    """Preenche cliente_nome/workflow_nome vazios a partir do catálogo Megazord."""
    if not workflow_meta:
        return workflow_meta
    cliente_ids = {key[0] for key in workflow_meta}
    workflow_ids = {key[1] for key in workflow_meta}
    nomes_c = dict(DimCliente.objects.filter(pk__in=cliente_ids).values_list("pk", "nome"))
    nomes_w = dict(DimWorkflow.objects.filter(pk__in=workflow_ids).values_list("pk", "nome"))
    for key, meta in workflow_meta.items():
        if not (meta.get("cliente_nome") or "").strip():
            meta["cliente_nome"] = nomes_c.get(key[0], "")
        if not (meta.get("workflow_nome") or "").strip():
            meta["workflow_nome"] = nomes_w.get(key[1], "")
    return workflow_meta


def merge_workflow_meta_from_derivation(
    workflow_meta: dict[tuple[int, int], dict],
    derivation_rows: list,
) -> dict[tuple[int, int], dict]:
    """Preenche nomes ausentes a partir das linhas de derivação (FK resolvida)."""
    for row in derivation_rows:
        key = (int(row.cliente_id), int(row.workflow_id))
        meta = workflow_meta.setdefault(
            key,
            {
                "id_cliente": key[0],
                "cliente_nome": "",
                "id_workflow": key[1],
                "workflow_nome": "",
                "nh_count": 0,
            },
        )
        if not (meta.get("cliente_nome") or "").strip():
            meta["cliente_nome"] = getattr(getattr(row, "cliente", None), "nome", "") or ""
        if not (meta.get("workflow_nome") or "").strip():
            meta["workflow_nome"] = getattr(getattr(row, "workflow", None), "nome", "") or ""
    return workflow_meta


def _received_by_workflow(on_date: date) -> tuple[dict[tuple[int, int], Decimal], dict[tuple[int, int], dict]]:
    totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    workflow_meta: dict[tuple[int, int], dict] = {}

    consolidado_rows = (
        SlaUtilConsolidado.objects.filter(data_cadastro=on_date)
        .values("id_cliente", "id_workflow", "cliente_nome", "workflow_nome")
        .annotate(total=Sum("quantidade"))
    )
    for row in consolidado_rows:
        if row["id_cliente"] is None or row["id_workflow"] is None:
            continue
        key = (int(row["id_cliente"]), int(row["id_workflow"]))
        totals[key] = Decimal(str(row["total"] or 0))
        workflow_meta[key] = {
            "id_cliente": key[0],
            "cliente_nome": row["cliente_nome"],
            "id_workflow": key[1],
            "workflow_nome": row["workflow_nome"],
            "nh_count": 0,
        }

    detalhe_rows = (
        SlaUtilDetalhe.objects.filter(data_cadastro=on_date)
        .values("id_cliente", "id_workflow", "cliente_nome", "workflow_nome")
        .annotate(total=Count("id"))
    )
    for row in detalhe_rows:
        if row["id_cliente"] is None or row["id_workflow"] is None:
            continue
        key = (int(row["id_cliente"]), int(row["id_workflow"]))
        # O consolidado e a fonte canonica. O detalhe e somente fallback.
        if key in totals:
            continue
        totals[key] = Decimal(str(row["total"] or 0))
        workflow_meta.setdefault(
            key,
            {
                "id_cliente": key[0],
                "cliente_nome": row["cliente_nome"],
                "id_workflow": key[1],
                "workflow_nome": row["workflow_nome"],
                "nh_count": 0,
            },
        )
    return dict(totals), enrich_workflow_meta_from_catalog(workflow_meta)


def _synthetic_derivation_row(
    *,
    cliente_id: int,
    workflow_id: int,
    etapa_id: int,
    percentual: Decimal,
    cliente_nome: str = "",
    workflow_nome: str = "",
    etapa_nome: str = "",
):
    return SimpleNamespace(
        cliente_id=cliente_id,
        workflow_id=workflow_id,
        etapa_id=etapa_id,
        percentual=percentual,
        cliente=SimpleNamespace(id=cliente_id, nome=cliente_nome, operations=True),
        workflow=SimpleNamespace(id=workflow_id, nome=workflow_nome, ind_considerar=True),
        etapa=SimpleNamespace(id=etapa_id, nome=etapa_nome),
    )


def _quarterly_derivation_rows(on_date: date) -> tuple[date, list]:
    window = _previous_complete_quarter(on_date)
    if window is None:
        raise CapacityUnavailableError(
            "Não há trimestre calendário completo para calcular a derivação média trimestral."
        )
    window_from, window_to = window
    grouped = (
        DerivacaoEtapaDiaria.objects.filter(data__gte=window_from, data__lte=window_to)
        .values(
            "cliente_id",
            "workflow_id",
            "etapa_id",
            "cliente__nome",
            "workflow__nome",
            "etapa__nome",
        )
        .annotate(avg_pct=Avg("percentual"))
    )
    rows = []
    for item in grouped:
        if item["avg_pct"] is None:
            continue
        rows.append(
            _synthetic_derivation_row(
                cliente_id=int(item["cliente_id"]),
                workflow_id=int(item["workflow_id"]),
                etapa_id=int(item["etapa_id"]),
                percentual=Decimal(str(item["avg_pct"])),
                cliente_nome=item["cliente__nome"] or "",
                workflow_nome=item["workflow__nome"] or "",
                etapa_nome=item["etapa__nome"] or "",
            )
        )
    if not rows:
        raise CapacityUnavailableError(
            "Não existem linhas de derivação no trimestre de referência."
        )
    return window_to, rows


def _day_derivation_rows(on_date: date) -> tuple[date, list]:
    rows = list(
        DerivacaoEtapaDiaria.objects.filter(data=on_date).select_related(
            "cliente", "workflow", "etapa"
        )
    )
    if not rows:
        raise CapacityUnavailableError(
            "Não existem linhas de derivação para a data selecionada."
        )
    return on_date, rows


def _manual_derivation_rows(
    on_date: date,
    manual_overrides: dict | None,
    *,
    reference_resolver,
) -> tuple[date, list]:
    reference_date, rows = reference_resolver(on_date)

    if not rows:
        raise CapacityUnavailableError(
            "Não há derivação disponível para a simulação."
        )
    by_key = {
        (int(row.cliente_id), int(row.workflow_id), int(row.etapa_id)): row
        for row in rows
    }
    for item in ((manual_overrides or {}).get("derivations") or []):
        key = (int(item["id_cliente"]), int(item["id_workflow"]), int(item["id_etapa"]))
        baseline = by_key.get(key)
        by_key[key] = _synthetic_derivation_row(
            cliente_id=key[0], workflow_id=key[1], etapa_id=key[2],
            percentual=Decimal(str(item["percentual"])),
            cliente_nome=str(item.get("cliente_nome") or getattr(getattr(baseline, "cliente", None), "nome", "")),
            workflow_nome=str(item.get("workflow_nome") or getattr(getattr(baseline, "workflow", None), "nome", "")),
            etapa_nome=str(item.get("etapa_nome") or getattr(getattr(baseline, "etapa", None), "nome", "")),
        )
    return reference_date, list(by_key.values())


def resolve_derivation(
    on_date: date,
    config: CapacityScenarioConfig,
    *,
    reference_resolver,
    manual_overrides: dict | None = None,
) -> tuple[date, list]:
    if config.derivation_mode == "quarterly_avg":
        try:
            return _quarterly_derivation_rows(on_date)
        except CapacityUnavailableError:
            return reference_resolver(on_date)
    if config.derivation_mode == "day":
        try:
            return _day_derivation_rows(on_date)
        except CapacityUnavailableError:
            return reference_resolver(on_date)
    if config.derivation_mode == "manual":
        return _manual_derivation_rows(
            on_date,
            manual_overrides,
            reference_resolver=reference_resolver,
        )
    return reference_resolver(on_date)


def resolve_workflow_volumes(
    on_date: date,
    config: CapacityScenarioConfig,
    *,
    projection_resolver,
    manual_overrides: dict | None = None,
) -> tuple[dict, dict, list, list]:
    if config.volume_mode == "received_day":
        totals, meta = _received_by_workflow(on_date)
        if config.id == "operacao":
            totals, meta = filter_documentoscopia_workflows(totals, meta)
        if not totals:
            raise CapacityUnavailableError(
                "Não há volume recebido registrado para a data selecionada."
            )
        return totals, meta, [], []
    if config.volume_mode == "manual":
        base_totals, base_meta, duplicate_nhs, null_volume_nhs = projection_resolver(on_date)
        payload = (manual_overrides or {}).get("workflows") or []
        totals = dict(base_totals)
        meta = dict(base_meta)
        for item in payload:
            key = (int(item["id_cliente"]), int(item["id_workflow"]))
            if "percentual_variacao" in item:
                factor = Decimal("1") + Decimal(str(item["percentual_variacao"])) / Decimal("100")
                totals[key] = max(ZERO, base_totals.get(key, ZERO) * factor)
            else:
                totals[key] = Decimal(str(item.get("volume", 0)))
            meta[key] = {
                "id_cliente": key[0],
                "cliente_nome": str(
                    item.get("cliente_nome") or base_meta.get(key, {}).get("cliente_nome") or ""
                ),
                "id_workflow": key[1],
                "workflow_nome": str(
                    item.get("workflow_nome") or base_meta.get(key, {}).get("workflow_nome") or ""
                ),
                "nh_count": base_meta.get(key, {}).get("nh_count", 0),
            }
        return totals, meta, duplicate_nhs, null_volume_nhs
    return projection_resolver(on_date)


def operational_meta_by_stage(on_date: date) -> dict[int, Decimal]:
    window = _previous_complete_quarter(on_date)
    if window is None:
        return {}
    window_from, window_to = window
    rows = (
        MetaEtapa.objects.filter(data_inicio__lte=window_to)
        .filter(Q(data_fim__isnull=True) | Q(data_fim__gte=window_from))
        .values("etapa_id")
        .annotate(avg_meta=Avg("meta_dia"))
    )
    return {
        int(row["etapa_id"]): Decimal(str(row["avg_meta"]))
        for row in rows
        if row["avg_meta"] is not None and Decimal(str(row["avg_meta"])) > ZERO
    }


def manual_meta_by_stage(manual_overrides: dict | None) -> dict[int, Decimal]:
    payload = (manual_overrides or {}).get("metas") or []
    out: dict[int, Decimal] = {}
    for item in payload:
        meta = Decimal(str(item.get("meta_dia", 0)))
        if meta > ZERO:
            out[int(item["id_etapa"])] = meta
    return out
