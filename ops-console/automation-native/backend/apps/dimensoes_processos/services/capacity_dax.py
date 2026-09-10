"""Capacity alinhado ao modelo DAX (Cliente + Workflow + Etapa + Data + Hora)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from django.db.models import Avg, Max, Q, Sum

from apps.dimensoes_processos.models import DerivacaoEtapaDiaria, DimWorkflow, MetaEtapa
from apps.dimensoes_processos.services.capacity_familia import extract_familia_normalized
from apps.dimensoes_processos.services.capacity_scenarios import (
    PLANEJAMENTO_PRODUTO_TIPO,
    normalize_scenario_id,
)
from apps.dimensoes_processos.services.capacity_volume_esperado import build_volume_hora_contrato
from apps.monitoramento_sla.models import SlaUtilConsolidado, SlaUtilDetalhe

ZERO = Decimal("0")
HUNDRED = Decimal("100")
META_HOURS = Decimal("5.5")
DERIVATION_LOOKBACK_DAYS = 60
HOURS = range(24)

ComboKey = tuple[int, int, int]  # cliente, workflow, etapa
WorkflowKey = tuple[int, int]


def _decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _serialize_decimal(value: Decimal | None, places: str = "0.0001") -> str | None:
    if value is None:
        return None
    return str(value.quantize(Decimal(places)))


def _meta_hora(meta_dia: Decimal) -> Decimal | None:
    if meta_dia <= ZERO:
        return None
    return meta_dia / META_HOURS


def _load_combo_keys(on_date: date, *, documentoscopia_only: bool = False) -> set[ComboKey]:
    lookback_start = on_date - timedelta(days=DERIVATION_LOOKBACK_DAYS)
    queryset = DerivacaoEtapaDiaria.objects.filter(
        data__gte=lookback_start,
        data__lte=on_date,
    )
    if documentoscopia_only:
        queryset = queryset.filter(workflow__produto__tipo_produto=PLANEJAMENTO_PRODUTO_TIPO)
    rows = queryset.values_list("cliente_id", "workflow_id", "etapa_id").distinct()
    return {(int(c), int(w), int(e)) for c, w, e in rows}


def _load_derivation_averages(on_date: date, *, documentoscopia_only: bool = False) -> dict[ComboKey, Decimal | None]:
    lookback_start = on_date - timedelta(days=DERIVATION_LOOKBACK_DAYS)
    queryset = DerivacaoEtapaDiaria.objects.filter(
        data__gte=lookback_start,
        data__lt=on_date,
    )
    if documentoscopia_only:
        queryset = queryset.filter(workflow__produto__tipo_produto=PLANEJAMENTO_PRODUTO_TIPO)
    rows = queryset.values("cliente_id", "workflow_id", "etapa_id").annotate(avg_pct=Avg("percentual"))
    out: dict[ComboKey, Decimal | None] = {}
    for row in rows:
        key = (int(row["cliente_id"]), int(row["workflow_id"]), int(row["etapa_id"]))
        out[key] = _decimal(row["avg_pct"]) if row["avg_pct"] is not None else None
    return out


def _load_derivation_medians(on_date: date, *, documentoscopia_only: bool = False) -> dict[ComboKey, Decimal | None]:
    lookback_start = on_date - timedelta(days=DERIVATION_LOOKBACK_DAYS)
    queryset = DerivacaoEtapaDiaria.objects.filter(
        data__gte=lookback_start,
        data__lt=on_date,
    )
    if documentoscopia_only:
        queryset = queryset.filter(workflow__produto__tipo_produto=PLANEJAMENTO_PRODUTO_TIPO)
    percentuals_by_combo: dict[ComboKey, list[Decimal]] = defaultdict(list)
    for row in queryset.values("cliente_id", "workflow_id", "etapa_id", "percentual"):
        key = (int(row["cliente_id"]), int(row["workflow_id"]), int(row["etapa_id"]))
        percentuals_by_combo[key].append(_decimal(row["percentual"]))
    return {
        key: _decimal(median([float(value) for value in values]))
        for key, values in percentuals_by_combo.items()
        if values
    }


def _documentoscopia_workflow_keys(workflow_keys: set[WorkflowKey]) -> set[WorkflowKey]:
    if not workflow_keys:
        return set()
    workflow_ids = {key[1] for key in workflow_keys}
    allowed = set(
        DimWorkflow.objects.filter(
            id_workflow__in=workflow_ids,
            produto__tipo_produto=PLANEJAMENTO_PRODUTO_TIPO,
        ).values_list("id_workflow", flat=True)
    )
    return {key for key in workflow_keys if key[1] in allowed}


def _load_derivation_day(on_date: date) -> dict[ComboKey, Decimal | None]:
    rows = (
        DerivacaoEtapaDiaria.objects.filter(data=on_date)
        .values("cliente_id", "workflow_id", "etapa_id")
        .annotate(day_pct=Max("percentual"))
    )
    out: dict[ComboKey, Decimal | None] = {}
    for row in rows:
        key = (int(row["cliente_id"]), int(row["workflow_id"]), int(row["etapa_id"]))
        out[key] = _decimal(row["day_pct"]) if row["day_pct"] is not None else None
    return out


def _load_stage_meta(on_date: date) -> dict[int, tuple[Decimal | None, str]]:
    rows = list(
        MetaEtapa.objects.filter(data_inicio__lte=on_date)
        .filter(Q(data_fim__isnull=True) | Q(data_fim__gte=on_date))
        .select_related("etapa")
        .order_by("etapa_id", "-data_inicio", "pk")
    )
    by_stage: dict[int, list[MetaEtapa]] = defaultdict(list)
    for row in rows:
        by_stage[row.etapa_id].append(row)

    out: dict[int, tuple[Decimal | None, str]] = {}
    for etapa_id, candidates in by_stage.items():
        nome = candidates[0].etapa.nome if candidates[0].etapa else ""
        if len(candidates) != 1:
            out[etapa_id] = (None, nome)
            continue
        meta = _decimal(candidates[0].meta_dia)
        out[etapa_id] = (meta if meta > ZERO else None, nome)
    return out


def _load_received_volumes(on_date: date) -> dict[tuple[int, int, int], Decimal]:
    """Combine (count) + Consolidado (sum quantidade) por cliente, workflow, hora."""
    totals: dict[tuple[int, int, int], Decimal] = defaultdict(lambda: ZERO)

    detalhe_rows = SlaUtilDetalhe.objects.filter(data_cadastro=on_date).exclude(
        hora_cadastro__isnull=True
    )
    for row in detalhe_rows.only("id_cliente", "id_workflow", "hora_cadastro").iterator():
        if row.id_cliente is None or row.id_workflow is None or row.hora_cadastro is None:
            continue
        hour = row.hora_cadastro.hour
        key = (int(row.id_cliente), int(row.id_workflow), hour)
        totals[key] += Decimal("1")

    consolidado_rows = (
        SlaUtilConsolidado.objects.filter(data_cadastro=on_date)
        .exclude(hora_cadastro__isnull=True)
        .values("id_cliente", "id_workflow", "hora_cadastro")
        .annotate(total=Sum("quantidade"))
    )
    for row in consolidado_rows:
        if row["id_cliente"] is None or row["id_workflow"] is None:
            continue
        hour = row["hora_cadastro"].hour
        key = (int(row["id_cliente"]), int(row["id_workflow"]), hour)
        totals[key] += _decimal(row["total"])

    return dict(totals)


def _capacity_value(
    volume: Decimal,
    percentual: Decimal | None,
    meta_hora: Decimal | None,
) -> Decimal | None:
    if meta_hora is None or percentual is None or volume <= ZERO:
        return None
    return (volume * percentual / HUNDRED) / meta_hora


def compute_capacity_grain(
    on_date: date,
    *,
    combo_keys: set[ComboKey] | None = None,
    scenario_id: str = "planejamento",
) -> list[dict]:
    """Calcula capacidade esperada e recebida no grão C+WF+Etapa+Hora."""
    scenario = normalize_scenario_id(scenario_id)
    documentoscopia_only = scenario == "planejamento"
    use_median = scenario == "planejamento"

    if combo_keys is None:
        combo_keys = _load_combo_keys(on_date, documentoscopia_only=documentoscopia_only)

    if not combo_keys:
        return []

    workflow_keys = {(c, w) for c, w, _e in combo_keys}
    if documentoscopia_only:
        workflow_keys = _documentoscopia_workflow_keys(workflow_keys)
        combo_keys = {
            (cliente_id, workflow_id, etapa_id)
            for cliente_id, workflow_id, etapa_id in combo_keys
            if (cliente_id, workflow_id) in workflow_keys
        }
        if not combo_keys:
            return []

    if use_median:
        pct_esperado = _load_derivation_medians(on_date, documentoscopia_only=documentoscopia_only)
    else:
        pct_esperado = _load_derivation_averages(on_date, documentoscopia_only=documentoscopia_only)
    day_pct = _load_derivation_day(on_date)
    stage_meta = _load_stage_meta(on_date)
    volume_esperado = build_volume_hora_contrato(on_date, workflow_keys=workflow_keys)
    volume_recebido = _load_received_volumes(on_date)

    rows: list[dict] = []
    for cliente_id, workflow_id, etapa_id in sorted(combo_keys):
        meta_dia, etapa_nome = stage_meta.get(etapa_id, (None, ""))
        meta_hora = _meta_hora(meta_dia) if meta_dia is not None else None
        familia = extract_familia_normalized(etapa_nome)
        combo = (cliente_id, workflow_id, etapa_id)
        pct_avg = pct_esperado.get(combo)
        pct_day = day_pct.get(combo)

        for hour in HOURS:
            vol_esp = volume_esperado.get((cliente_id, workflow_id, hour), ZERO)
            vol_rec = volume_recebido.get((cliente_id, workflow_id, hour), ZERO)
            cap_esp = _capacity_value(vol_esp, pct_avg, meta_hora)
            cap_rec = _capacity_value(vol_rec, pct_day, meta_hora)

            if cap_esp is None and cap_rec is None and vol_esp <= ZERO and vol_rec <= ZERO:
                continue

            row_payload = {
                "id_cliente": cliente_id,
                "id_workflow": workflow_id,
                "id_etapa": etapa_id,
                "hour": hour,
                "etapa_nome": etapa_nome,
                "familia": familia,
                "meta_dia": _serialize_decimal(meta_dia, "0.01") if meta_dia else None,
                "meta_hora": _serialize_decimal(meta_hora) if meta_hora else None,
                "percentual_dia": _serialize_decimal(pct_day, "0.01")
                if pct_day is not None
                else None,
                "volume_esperado": _serialize_decimal(vol_esp, "0.01"),
                "volume_recebido": _serialize_decimal(vol_rec, "0.01"),
                "capacity_esperada": _serialize_decimal(cap_esp),
                "capacity_recebida": _serialize_decimal(cap_rec),
            }
            if use_median:
                row_payload["percentual_mediana_60d"] = (
                    _serialize_decimal(pct_avg, "0.01") if pct_avg is not None else None
                )
            else:
                row_payload["percentual_medio_60d"] = (
                    _serialize_decimal(pct_avg, "0.01") if pct_avg is not None else None
                )
            rows.append(row_payload)

    return rows


def aggregate_capacity_rows(grain_rows: list[dict]) -> dict:
    """Agrega grão fino para etapas, famílias, clientes, workflows e horas."""
    by_stage: dict[int, dict] = {}
    by_familia: dict[str, dict] = defaultdict(
        lambda: {
            "familia": "",
            "stage_ids": set(),
            "capacity_esperada": ZERO,
            "capacity_recebida": ZERO,
        }
    )
    by_client: dict[int, dict] = defaultdict(
        lambda: {
            "capacity_esperada": ZERO,
            "capacity_recebida": ZERO,
            "workflow_keys": set(),
        }
    )
    by_workflow: dict[WorkflowKey, dict] = defaultdict(
        lambda: {
            "capacity_esperada": ZERO,
            "capacity_recebida": ZERO,
            "stage_ids": set(),
        }
    )
    by_hour: dict[int, dict] = {
        hour: {"capacity_esperada": ZERO, "capacity_recebida": ZERO} for hour in HOURS
    }

    total_esperada = ZERO
    total_recebida = ZERO

    for row in grain_rows:
        cap_esp = _decimal(row["capacity_esperada"]) if row["capacity_esperada"] else ZERO
        cap_rec = _decimal(row["capacity_recebida"]) if row["capacity_recebida"] else ZERO
        etapa_id = row["id_etapa"]
        familia = row["familia"] or ""
        wf_key = (row["id_cliente"], row["id_workflow"])
        hour = row["hour"]

        total_esperada += cap_esp
        total_recebida += cap_rec

        stage = by_stage.setdefault(
            etapa_id,
            {
                "id_etapa": etapa_id,
                "etapa_nome": row["etapa_nome"],
                "familia": familia,
                "meta_dia": row["meta_dia"],
                "meta_hora": row["meta_hora"],
                "capacity_esperada": ZERO,
                "capacity_recebida": ZERO,
                "status": "dimensionada" if row["meta_dia"] else "sem_meta",
            },
        )
        stage["capacity_esperada"] += cap_esp
        stage["capacity_recebida"] += cap_rec

        fam = by_familia[familia]
        fam["familia"] = familia
        fam["stage_ids"].add(etapa_id)
        fam["capacity_esperada"] += cap_esp
        fam["capacity_recebida"] += cap_rec

        client = by_client[row["id_cliente"]]
        client["capacity_esperada"] += cap_esp
        client["capacity_recebida"] += cap_rec
        client["workflow_keys"].add(wf_key)

        wf = by_workflow[wf_key]
        wf["id_cliente"] = row["id_cliente"]
        wf["id_workflow"] = row["id_workflow"]
        wf["capacity_esperada"] += cap_esp
        wf["capacity_recebida"] += cap_rec
        wf["stage_ids"].add(etapa_id)

        by_hour[hour]["capacity_esperada"] += cap_esp
        by_hour[hour]["capacity_recebida"] += cap_rec

    peak_hour_num = None
    peak_capacity = ZERO
    for hour, item in by_hour.items():
        if item["capacity_esperada"] > peak_capacity:
            peak_capacity = item["capacity_esperada"]
            peak_hour_num = hour

    return {
        "total_capacity_esperada": total_esperada,
        "total_capacity_recebida": total_recebida,
        "peak_hour_esperada": peak_hour_num,
        "peak_capacity_esperada": peak_capacity,
        "by_stage": by_stage,
        "by_familia": by_familia,
        "by_client": by_client,
        "by_workflow": by_workflow,
        "by_hour": by_hour,
    }
