# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from django.conf import settings
from django.core.paginator import Paginator
from django.conf import settings
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Max, Q, QuerySet, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.common.models import BotDataIngestion
from apps.rotina_bruto.models import RotinaProdBrutoRecord
from apps.replicacao_d1.models import (
    ReplicacaoD1Protocolo,
    ReplicacaoD1Reconciliacao,
    ReplicacaoD1Replicado,
    ReplicacaoD1Run,
    ReplicacaoD1SyncLog,
    ReplicacaoD1WorkflowDia,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1EscalaDia,
)
from apps.replicacao_d1.normalization import (
    STATUS_DIVERGENTE,
    STATUS_FALHOU,
    STATUS_PENDENTE,
    STATUS_RECEBIDO,
    STATUS_REPLICADO,
    STATUS_PLANEJADO,
    WORKFLOW_DESTINO_LABELS,
    WORKFLOW_DESTINO_ORDER,
    classify_workflow_destino,
    destino_bucket_for_canal,
    destino_bucket_for_fila,
    destino_bucket_for_projection_canal,
    normalize_key,
    normalize_protocolo,
    workflow_destino_q_for_bucket,
)
from apps.replicacao_d1.feature_flags import rollout_flags_snapshot
from apps.replicacao_d1.config_models import ReplicacaoD1Workflow
from apps.replicacao_d1.services.reconciliation import RECONCILIATION_RULE_VERSION


G_AUDITORIA_PRODUCTIVITY_STAGE = "Análise Visual - G Auditoria"
DEFAULT_DASHBOARD_MAX_PERIOD_DAYS = 31


PROJECTION_CHANNELS = {
    "brflow": {
        "label": "G Auditoria",
        "fila": "G auditoria",
        "auditors_field": "auditores_brflow",
        "quota_field": "meta_produ_diaria",
    },
    "case": {
        "label": "Case",
        "fila": "3.1",
        "auditors_field": "auditores_case",
        "quota_field": "meta_produ_diaria_case",
    },
    "bio": {
        "label": "Bio",
        "fila": "Bio",
        "auditors_field": "auditores_bio",
        "quota_field": "meta_produ_diaria_bio",
    },
    "redoc": {
        "label": "Redoc",
        "fila": "Redoc",
        "auditors_field": "auditores_redoc",
        "quota_field": "meta_produ_diaria_redoc",
    },
}


@dataclass
class DashboardParams:
    data_de: date | None = None
    data_ate: date | None = None
    run_id: str = ""
    cliente: str = ""
    workflow: str = ""
    canal: str = ""
    fila: str = ""
    status: str = ""
    protocolo: str = ""
    page: int = 1
    page_size: int = 50


@dataclass
class WorkflowProjectionParams:
    workflow: str = ""
    cliente: str = ""
    canal: str = ""
    fila: str = ""
    page: int = 1
    page_size: int = 25


def _query_value(raw: dict[str, Any], key: str) -> str:
    val = raw.get(key)
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        val = val[0] if val else ""
    return str(val).strip()


def parse_dashboard_params(raw: dict[str, Any]) -> DashboardParams:
    data_de = _parse_date(_query_value(raw, "data_de"))
    data_ate = _parse_date(_query_value(raw, "data_ate"))
    today = timezone.localdate()
    if data_de is None and data_ate is None:
        data_de = today - timedelta(days=6)
        data_ate = today
    elif data_de is None:
        data_de = data_ate
    elif data_ate is None:
        data_ate = data_de
    if data_de and data_ate and data_de > data_ate:
        data_de, data_ate = data_ate, data_de
    max_period_days = max(
        1,
        int(
            getattr(
                settings,
                "REPLICACAO_D1_DASHBOARD_MAX_PERIOD_DAYS",
                DEFAULT_DASHBOARD_MAX_PERIOD_DAYS,
            )
            or DEFAULT_DASHBOARD_MAX_PERIOD_DAYS
        ),
    )
    period_days = (data_ate - data_de).days + 1
    if period_days > max_period_days:
        from rest_framework.exceptions import ValidationError

        raise ValidationError(
            {
                "detail": (
                    f"O período do dashboard pode ter no máximo {max_period_days} dias. "
                    "Reduza o intervalo entre Data de e Data até."
                )
            }
        )
    try:
        page = max(1, int(_query_value(raw, "page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(500, max(1, int(_query_value(raw, "page_size") or 50)))
    except (TypeError, ValueError):
        page_size = 50
    return DashboardParams(
        data_de=data_de,
        data_ate=data_ate,
        run_id=_query_value(raw, "run_id"),
        cliente=_query_value(raw, "cliente"),
        workflow=_query_value(raw, "workflow_config") or _query_value(raw, "workflow"),
        canal=_query_value(raw, "canal_destino") or _query_value(raw, "canal"),
        fila=_query_value(raw, "fila"),
        status=(
            _query_value(raw, "status_operacional") or _query_value(raw, "status")
        ).lower(),
        protocolo=_query_value(raw, "protocolo"),
        page=page,
        page_size=page_size,
    )


def parse_workflow_projection_params(raw: dict[str, Any]) -> WorkflowProjectionParams:
    try:
        page = max(1, int(_query_value(raw, "page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(100, max(1, int(_query_value(raw, "page_size") or 25)))
    except (TypeError, ValueError):
        page_size = 25
    return WorkflowProjectionParams(
        workflow=_query_value(raw, "workflow"),
        cliente=_query_value(raw, "cliente"),
        canal=_query_value(raw, "canal"),
        fila=_query_value(raw, "fila"),
        page=page,
        page_size=page_size,
    )


def _parse_date(value: str | date | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    parsed = parse_date(text[:10])
    if parsed:
        return parsed
    try:
        return datetime.strptime(text[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def _pct(part: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round((part / total) * 100, 1)


def _filter_protocolos(params: DashboardParams) -> QuerySet[ReplicacaoD1Protocolo]:
    qs = ReplicacaoD1Protocolo.objects.select_related("run")
    if params.run_id:
        qs = qs.filter(run_id=params.run_id)
    if params.data_de:
        qs = qs.filter(run__data_execucao__date__gte=params.data_de)
    if params.data_ate:
        qs = qs.filter(run__data_execucao__date__lte=params.data_ate)
    if params.workflow:
        qs = qs.filter(workflow_config__iexact=params.workflow)
    if params.canal:
        qs = qs.filter(canal_destino__iexact=params.canal)
    if params.protocolo:
        norm = normalize_protocolo(params.protocolo)
        if norm:
            qs = qs.filter(
                Q(protocolo__icontains=params.protocolo)
                | Q(protocolo_normalizado=norm)
            )
        else:
            qs = qs.filter(protocolo__icontains=params.protocolo)
    if params.cliente or params.fila:
        wf_qs = ReplicacaoD1WorkflowDia.objects.all()
        if params.run_id:
            wf_qs = wf_qs.filter(run_id=params.run_id)
        if params.data_de:
            wf_qs = wf_qs.filter(run__data_execucao__date__gte=params.data_de)
        if params.data_ate:
            wf_qs = wf_qs.filter(run__data_execucao__date__lte=params.data_ate)
        if params.cliente:
            wf_qs = wf_qs.filter(cliente__iexact=params.cliente)
        if params.fila:
            wf_qs = wf_qs.filter(fila__iexact=params.fila)
        run_wf = list(wf_qs.values_list("run_id", "workflow_config"))
        if not run_wf:
            return qs.none()
        q = Q()
        for run_id, wf in run_wf:
            q |= Q(run_id=run_id, workflow_config=wf)
        qs = qs.filter(q)
    return qs


def _filter_workflows(params: DashboardParams) -> QuerySet[ReplicacaoD1WorkflowDia]:
    qs = ReplicacaoD1WorkflowDia.objects.select_related("run")
    if params.run_id:
        qs = qs.filter(run_id=params.run_id)
    if params.data_de:
        qs = qs.filter(run__data_execucao__date__gte=params.data_de)
    if params.data_ate:
        qs = qs.filter(run__data_execucao__date__lte=params.data_ate)
    if params.cliente:
        qs = qs.filter(cliente__iexact=params.cliente)
    if params.workflow:
        qs = qs.filter(workflow_config__iexact=params.workflow)
    if params.canal:
        qs = qs.filter(canal_destino__iexact=params.canal)
    if params.fila:
        qs = qs.filter(fila__iexact=params.fila)
    return qs


def _filter_replicados(params: DashboardParams) -> QuerySet[ReplicacaoD1Replicado]:
    """Confirmações do ciclo exibido: execução em D, relatório pareado em D+1 calendário."""
    qs = ReplicacaoD1Replicado.objects.all()
    report_de = params.data_de + timedelta(days=1) if params.data_de else None
    report_ate = params.data_ate + timedelta(days=1) if params.data_ate else None
    if params.run_id:
        execution = (
            ReplicacaoD1Run.objects.filter(run_id=params.run_id)
            .annotate(execution_day=TruncDate("data_execucao"))
            .values_list("execution_day", flat=True)
            .first()
        )
        if execution is None:
            return qs.none()
        report_de = execution + timedelta(days=1)
        report_ate = report_de
    if report_de:
        qs = qs.filter(report_date__gte=report_de)
    if report_ate:
        qs = qs.filter(report_date__lte=report_ate)
    if params.cliente:
        qs = qs.filter(cliente_origem__iexact=params.cliente)
    if params.workflow:
        workflow_names = {params.workflow}
        workflow_names.update(
            ReplicacaoD1WorkflowDia.objects.filter(workflow_config__iexact=params.workflow)
            .exclude(workflow_brflow="")
            .values_list("workflow_brflow", flat=True)
        )
        workflow_filter = Q()
        for name in workflow_names:
            workflow_filter |= Q(workflow_origem__iexact=name)
        qs = qs.filter(workflow_filter)
    if params.protocolo:
        norm = normalize_protocolo(params.protocolo)
        qs = qs.filter(
            Q(protocolo_origem__icontains=params.protocolo)
            | Q(protocolo_origem_normalizado=norm)
        )
    destino_bucket = None
    if params.canal:
        destino_bucket = destino_bucket_for_canal(params.canal)
    elif params.fila:
        destino_bucket = destino_bucket_for_fila(params.fila)
    if destino_bucket:
        qs = qs.filter(workflow_destino_q_for_bucket(destino_bucket))
    elif params.canal or params.fila:
        return qs.none()
    # Nenhum filtro acima introduz JOIN multiplicador. ``distinct()`` fazia
    # subconsultas e agregados carregarem colunas da ordenação padrão.
    return qs.order_by()


def _status_filtered_protocols(
    params: DashboardParams,
    qs: QuerySet[ReplicacaoD1Protocolo],
) -> QuerySet[ReplicacaoD1Protocolo]:
    if not params.status:
        return qs
    unfiltered_params = replace(params, status="")
    paired_qs, _paired_rep_qs, _fast = _paired_querysets(
        qs,
        _filter_replicados(unfiltered_params),
    )
    if params.status == STATUS_REPLICADO:
        return paired_qs
    remaining = qs.exclude(pk__in=paired_qs.order_by().values("pk"))
    if params.status == STATUS_DIVERGENTE:
        return remaining.filter(status_operacional=STATUS_DIVERGENTE)
    return remaining.filter(status_operacional=params.status)


def _paired_links(
    prot_qs: QuerySet[ReplicacaoD1Protocolo],
    rep_qs: QuerySet[ReplicacaoD1Replicado],
) -> dict[int, int]:
    """Restringe o vínculo persistido ao pareamento visual do BI: execução D, confirmação D+1."""
    paired: dict[int, int] = {}
    rows = (
        ReplicacaoD1Reconciliacao.objects.filter(
            status=ReplicacaoD1Reconciliacao.STATUS_MATCHED,
            protocolo__in=prot_qs,
            replicado__in=rep_qs,
            replicado__isnull=False,
        )
        .values_list(
            "protocolo_id",
            "replicado_id",
            "protocolo__run__data_execucao",
            "replicado__report_date",
        )
        .iterator(chunk_size=2000)
    )
    for protocolo_id, replicado_id, executed_at, report_date in rows:
        if executed_at and report_date == executed_at.date() + timedelta(days=1):
            paired[protocolo_id] = replicado_id

    # O legado reconciliava pela data de referência. Para o eixo novo, completa o
    # vínculo de forma determinística por protocolo + D+1, sem alterar o estado operacional.
    rep_index: dict[tuple[date, str], list[int]] = defaultdict(list)
    for rep_id, report_date, normalized, raw in rep_qs.values_list(
        "id", "report_date", "protocolo_origem_normalizado", "protocolo_origem"
    ):
        key = normalized or normalize_protocolo(raw)
        if key:
            rep_index[(report_date, key)].append(rep_id)
    for protocol_id, executed_at, normalized, raw in prot_qs.values_list(
        "id", "run__data_execucao", "protocolo_normalizado", "protocolo"
    ):
        if protocol_id in paired or executed_at is None:
            continue
        key = normalized or normalize_protocolo(raw)
        candidates = rep_index.get((executed_at.date() + timedelta(days=1), key), [])
        if len(candidates) == 1:
            paired[protocol_id] = candidates[0]
    return paired


def _paired_querysets(
    prot_qs: QuerySet[ReplicacaoD1Protocolo],
    rep_qs: QuerySet[ReplicacaoD1Replicado],
) -> tuple[QuerySet[ReplicacaoD1Protocolo], QuerySet[ReplicacaoD1Replicado], bool]:
    """Retorna os pares como subconsultas; usa o legado apenas antes do backfill v2."""
    use_fast = bool(
        getattr(settings, "REPLICACAO_D1_DASHBOARD_FAST_RECONCILIATION", True)
    )
    if use_fast:
        reconciled = ReplicacaoD1Reconciliacao.objects.filter(
            regra_version=RECONCILIATION_RULE_VERSION,
            protocolo__in=prot_qs.order_by().values("pk"),
        ).order_by()
        protocol_count = prot_qs.order_by().count()
        coverage = reconciled.values("protocolo_id").distinct().count()
        if coverage == protocol_count:
            matched = reconciled.filter(
                status=ReplicacaoD1Reconciliacao.STATUS_MATCHED,
                replicado__isnull=False,
                replicado__in=rep_qs.order_by().values("pk"),
            )
            paired_protocols = prot_qs.filter(
                pk__in=matched.values("protocolo_id")
            )
            paired_replicados = rep_qs.filter(
                pk__in=matched.values("replicado_id")
            )
            return paired_protocols, paired_replicados, True

    links = _paired_links(prot_qs, rep_qs)
    protocol_ids = set(links)
    replicado_ids = set(links.values())
    return (
        prot_qs.filter(pk__in=protocol_ids),
        rep_qs.filter(pk__in=replicado_ids),
        False,
    )


def _average_replication_hours(qs: QuerySet[ReplicacaoD1Replicado]) -> float | None:
    duration = ExpressionWrapper(
        F("data_cadastro_destino") - F("data_cadastro_origem"),
        output_field=DurationField(),
    )
    avg = (
        qs.filter(data_cadastro_destino__isnull=False, data_cadastro_origem__isnull=False)
        .annotate(replication_duration=duration)
        .aggregate(value=Avg("replication_duration"))["value"]
    )
    if avg is None:
        return None
    return round(avg.total_seconds() / 3600, 2)


def _daily_capacity(start: date, end: date) -> tuple[dict[date, int], float]:
    general = ReplicacaoD1ConfigGeral.objects.order_by("pk").first()
    quota = float(general.meta_produ_diaria) if general else 300.0
    scales = ReplicacaoD1EscalaDia.objects.filter(data__gte=start, data__lte=end).values_list(
        "data", "auditores_brflow"
    )
    return {day: int(round(auditors * quota)) for day, auditors in scales}, quota


def _projection_channel_key(fila: str) -> str:
    normalized = normalize_key(str(fila or "").replace(",", "."))
    if normalized == "3.1" or "case" in normalized or (
        "documentoscopia" in normalized and "3.1" in normalized
    ):
        return "case"
    if "biometr" in normalized or normalized == "bio":
        return "bio"
    if "redoc" in normalized:
        return "redoc"
    return "brflow"


def _projection_capacity_by_channel(
    start: date,
    end: date,
) -> dict[str, dict[str, Any]]:
    general = ReplicacaoD1ConfigGeral.objects.order_by("pk").first()
    base_quota = max(0.0, float(general.meta_produ_diaria)) if general else 300.0
    scale_fields = [definition["auditors_field"] for definition in PROJECTION_CHANNELS.values()]
    scale_rows = ReplicacaoD1EscalaDia.objects.filter(data__gte=start, data__lte=end).values(
        "data", *scale_fields
    )
    result: dict[str, dict[str, Any]] = {}
    for key, definition in PROJECTION_CHANNELS.items():
        configured_quota = float(getattr(general, definition["quota_field"], base_quota)) if general else base_quota
        quota = max(0.0, configured_quota) or base_quota
        result[key] = {
            "key": key,
            "canal": definition["label"],
            "fila": definition["fila"],
            "meta_por_auditor": quota,
            "daily": {},
        }
    for scale in scale_rows:
        day = scale["data"]
        for key, definition in PROJECTION_CHANNELS.items():
            channel = result[key]
            auditors = int(scale[definition["auditors_field"]] or 0)
            # O status do destino controla a execução do robô, não a disponibilidade
            # operacional informada pela escala. A projeção deve refletir a escala.
            channel["daily"][day] = int(round(auditors * channel["meta_por_auditor"]))
    return result


def _daily_productivity(start: date, end: date) -> dict[date, int]:
    """Produção da Rotina para a etapa G Auditoria, agrupada pela data da análise."""
    rows = (
        RotinaProdBrutoRecord.objects.filter(
            Q(
                dat_analise__gte=start,
                dat_analise__lte=end,
            )
            | Q(
                dat_analise__isnull=True,
                report_date__gte=start,
                report_date__lte=end,
            ),
            nom_etapa__iexact=G_AUDITORIA_PRODUCTIVITY_STAGE,
        )
        .annotate(day=Coalesce("dat_analise", "report_date"))
        .values("day")
        .annotate(total=Count("id"))
    )
    return {row["day"]: int(row["total"] or 0) for row in rows if row["day"]}


def _empty_destino_counts() -> dict[str, int]:
    return {key: 0 for key in WORKFLOW_DESTINO_ORDER}


def _destino_rows_from_counts(
    counts: dict[str, int],
    *,
    total: int | None = None,
) -> list[dict[str, Any]]:
    grand_total = total if total is not None else sum(counts.values())
    rows: list[dict[str, Any]] = []
    for key in WORKFLOW_DESTINO_ORDER:
        value = int(counts.get(key, 0) or 0)
        if value <= 0:
            continue
        rows.append(
            {
                "key": key,
                "label": WORKFLOW_DESTINO_LABELS[key],
                "total": value,
                "share_pct": _pct(value, grand_total),
            }
        )
    rows.sort(key=lambda row: (-row["total"], row["label"]))
    return rows


def _aggregate_destino_counts(
    rep_qs: QuerySet[ReplicacaoD1Replicado],
) -> dict[str, int]:
    counts = _empty_destino_counts()
    for workflow_destino, total in rep_qs.values_list("workflow_destino").annotate(
        total=Count("id")
    ):
        key, _ = classify_workflow_destino(workflow_destino)
        counts[key] += int(total or 0)
    return counts


def _daily_destino_counts(
    rep_qs: QuerySet[ReplicacaoD1Replicado],
    data_de: date,
    data_ate: date,
) -> dict[date, dict[str, int]]:
    daily: dict[date, dict[str, int]] = defaultdict(_empty_destino_counts)
    for report_date, workflow_destino, total in rep_qs.values_list(
        "report_date", "workflow_destino"
    ).annotate(total=Count("id")):
        day = report_date - timedelta(days=1)
        if day < data_de or day > data_ate:
            continue
        key, _ = classify_workflow_destino(workflow_destino)
        daily[day][key] += int(total or 0)
    return daily


def _breakdown_by_workflow_destino(
    rep_qs: QuerySet[ReplicacaoD1Replicado],
) -> list[dict[str, Any]]:
    counts = _aggregate_destino_counts(rep_qs)
    return _destino_rows_from_counts(counts)


def _official_breakdown(
    rep_qs: QuerySet[ReplicacaoD1Replicado], field: str
) -> list[dict[str, Any]]:
    rows = (
        rep_qs.values(field)
        .annotate(total=Count("id"))
        .order_by("-total", field)
    )
    return [
        {
            "label": row[field] or "Sem informação",
            "total": row["total"],
            "replicado": row["total"],
            "taxa_sucesso": 100.0,
        }
        for row in rows[:100]
    ]


def _client_exceptions(
    wf_qs: QuerySet[ReplicacaoD1WorkflowDia],
    rep_qs: QuerySet[ReplicacaoD1Replicado],
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """Compara disponibilidade/expectativa do bot com a confirmação oficial."""
    planned: dict[tuple[date, str], dict[str, Any]] = {}
    for row in wf_qs.annotate(execution_day=TruncDate("run__data_execucao")).values(
        "execution_day",
        "cliente",
        "disponivel_d1",
        "protocolos_planejados",
        "amostra_efetiva",
    ):
        day = row["execution_day"]
        client = str(row["cliente"] or "Sem cliente").strip()
        if day is None:
            continue
        key = (day, normalize_key(client))
        bucket = planned.setdefault(
            key,
            {"data": day.isoformat(), "cliente": client, "disponivel": 0, "esperado_bot": 0},
        )
        bucket["disponivel"] += int(row["disponivel_d1"] or 0)
        expected = row["protocolos_planejados"]
        if not expected:
            expected = row["amostra_efetiva"]
        bucket["esperado_bot"] += int(expected or 0)

    realized: dict[tuple[date, str], int] = defaultdict(int)
    for report_date, client, total in (
        rep_qs.values_list("report_date", "cliente_origem")
        .annotate(total=Count("id"))
    ):
        client_key = normalize_key(client or "")
        realized[(report_date - timedelta(days=1), client_key)] += int(total)

    result: dict[str, list[dict[str, Any]]] = {
        "sem_volume": [],
        "volume_sem_replicacao": [],
        "replicacao_parcial": [],
    }
    configured_keys = {key[1] for key in planned}
    for key, base in planned.items():
        replicated = realized.get(key, 0)
        expected = int(base["esperado_bot"])
        row = {
            **base,
            "replicados": replicated,
            "gap": max(expected - replicated, 0),
        }
        if int(base["disponivel"]) <= 0:
            result["sem_volume"].append(row)
        elif replicated <= 0:
            result["volume_sem_replicacao"].append(row)
        elif expected > 0 and replicated < expected:
            result["replicacao_parcial"].append(row)

    for rows in result.values():
        rows.sort(key=lambda row: (row["gap"], row["disponivel"]), reverse=True)
    unmapped = sum(
        total
        for (_day, key), total in realized.items()
        if key and key not in configured_keys
    )
    return result, unmapped


def _serialize_official_replicado(row: ReplicacaoD1Replicado) -> dict[str, Any]:
    return {
        "id": row.pk,
        "data_operacional": (row.report_date - timedelta(days=1)).isoformat(),
        "report_date": row.report_date.isoformat(),
        "protocolo_origem": row.protocolo_origem,
        "protocolo_destino": row.protocolo_destino,
        "cliente_origem": row.cliente_origem,
        "cliente_destino": row.cliente_destino,
        "workflow_origem": row.workflow_origem,
        "workflow_destino": row.workflow_destino,
        "data_cadastro_origem": (
            row.data_cadastro_origem.isoformat() if row.data_cadastro_origem else None
        ),
        "data_cadastro_destino": (
            row.data_cadastro_destino.isoformat() if row.data_cadastro_destino else None
        ),
        "tipo_conclusao": row.tipo_conclusao_analise_origem,
    }


def _mix_bucket(value: str) -> str:
    normalized = normalize_key(value or "")
    if normalized == "automatico":
        return "automatico"
    if normalized == "manual":
        return "manual"
    return "nao_classificado"


def _filter_options(params: DashboardParams) -> dict[str, list[str]]:
    base = replace(params, run_id="", cliente="", workflow="", canal="", fila="", status="", protocolo="")
    workflows = _filter_workflows(base)
    replicados = _filter_replicados(base)
    runs = _filter_protocolos(base).values_list("run_id", flat=True).distinct().order_by("-run_id")[:100]
    clients = set(
        workflows.exclude(cliente="").values_list("cliente", flat=True).distinct()
    )
    clients.update(
        replicados.exclude(cliente_origem="").values_list("cliente_origem", flat=True).distinct()
    )
    workflow_names = set(
        workflows.exclude(workflow_config="").values_list("workflow_config", flat=True).distinct()
    )
    workflow_names.update(
        replicados.exclude(workflow_origem="").values_list("workflow_origem", flat=True).distinct()
    )
    return {
        "runs": list(runs),
        "clientes": sorted(clients, key=normalize_key),
        "workflows": sorted(workflow_names, key=normalize_key),
        "canais": list(
            workflows.exclude(canal_destino="")
            .values_list("canal_destino", flat=True)
            .distinct()
            .order_by("canal_destino")
        ),
        "status": [
            STATUS_REPLICADO,
            STATUS_PENDENTE,
            STATUS_RECEBIDO,
            STATUS_FALHOU,
            STATUS_DIVERGENTE,
        ],
    }


def _replication_analytics(
    params: DashboardParams,
    prot_qs: QuerySet[ReplicacaoD1Protocolo],
    wf_qs: QuerySet[ReplicacaoD1WorkflowDia],
) -> dict[str, Any]:
    rep_params = replace(params, workflow="") if params.workflow else params
    rep_qs = _filter_replicados(rep_params)
    paired_protocol_qs, paired_rep_qs, fast_reconciliation = _paired_querysets(prot_qs, rep_qs)
    paired_protocol_ids = paired_protocol_qs.order_by().values("pk")
    paired_rep_ids = paired_rep_qs.order_by().values("pk")
    outside_qs = rep_qs.exclude(pk__in=paired_rep_ids)
    if params.workflow:
        workflow_names = {params.workflow}
        workflow_names.update(
            wf_qs.exclude(workflow_brflow="").values_list("workflow_brflow", flat=True)
        )
        outside_filter = Q()
        for name in workflow_names:
            outside_filter |= Q(workflow_origem__iexact=name)
        outside_qs = outside_qs.filter(outside_filter)
        rep_qs = rep_qs.filter(Q(pk__in=paired_rep_ids) | Q(pk__in=outside_qs.values("pk")))
    if params.status == STATUS_REPLICADO:
        rep_qs = paired_rep_qs
        outside_qs = outside_qs.none()
    elif params.status == STATUS_DIVERGENTE:
        rep_qs = outside_qs
        paired_rep_qs = paired_rep_qs.none()
    elif params.status in {STATUS_PENDENTE, STATUS_FALHOU, STATUS_RECEBIDO}:
        rep_qs = rep_qs.none()
        paired_rep_qs = paired_rep_qs.none()
        outside_qs = outside_qs.none()

    chosen = prot_qs.count()
    chosen_replicated = paired_protocol_qs.count()
    chosen_not_replicated = max(chosen - chosen_replicated, 0)
    total_replicated = rep_qs.count()
    outside_sample = outside_qs.count()

    data_de = params.data_de or timezone.localdate()
    data_ate = params.data_ate or data_de
    capacity, daily_quota = _daily_capacity(data_de, data_ate)
    total_capacity = sum(capacity.values())
    saved_protocols = wf_qs.aggregate(value=Sum("protocolos_salvos"))["value"] or 0

    run_rows = list(
        ReplicacaoD1Run.objects.filter(run_id__in=prot_qs.values("run_id"))
        .annotate(execution_day=TruncDate("data_execucao"))
        .values_list("run_id", "execution_day")
    )
    confirmation_dates = set(
        ReplicacaoD1Replicado.objects.filter(
            report_date__in=[day + timedelta(days=1) for _, day in run_rows if day]
        ).values_list("report_date", flat=True)
    )
    runs_without_confirmation = sum(
        1 for _, day in run_rows if day and day + timedelta(days=1) not in confirmation_dates
    )

    plan_daily: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in (
        prot_qs.annotate(execution_day=TruncDate("run__data_execucao"))
        .values("execution_day", "status_operacional")
        .annotate(total=Count("id"))
    ):
        day = row["execution_day"]
        if day:
            plan_daily[day]["escolhidos_bot"] += row["total"]
            plan_daily[day][row["status_operacional"]] += row["total"]
    for row in (
        paired_protocol_qs
        .annotate(execution_day=TruncDate("run__data_execucao"))
        .values("execution_day")
        .annotate(total=Count("id"))
    ):
        if row["execution_day"]:
            plan_daily[row["execution_day"]]["escolhidos_replicados"] = row["total"]

    raw_daily: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    outside_ids = outside_qs.values_list("pk", flat=True)
    destino_daily = _daily_destino_counts(rep_qs, data_de, data_ate)
    for report_date, conclusion, total in rep_qs.values_list(
        "report_date", "tipo_conclusao_analise_origem"
    ).annotate(total=Count("id")):
        day = report_date - timedelta(days=1)
        raw_daily[day]["total_replicados"] += total
        raw_daily[day][_mix_bucket(conclusion)] += total
    for report_date, total in (
        ReplicacaoD1Replicado.objects.filter(pk__in=outside_ids)
        .values_list("report_date")
        .annotate(total=Count("id"))
    ):
        raw_daily[report_date - timedelta(days=1)]["replicados_sem_escolha"] = total

    daily_series: list[dict[str, Any]] = []
    cursor = data_de
    while cursor <= data_ate:
        plan = plan_daily[cursor]
        raw = raw_daily[cursor]
        selected = plan.get("escolhidos_bot", 0)
        matched = plan.get("escolhidos_replicados", 0)
        meta = capacity.get(cursor, 0)
        total = raw.get("total_replicados", 0)
        day_destino = destino_daily.get(cursor, _empty_destino_counts())
        daily_series.append(
            {
                "data_execucao": cursor.isoformat(),
                "data_confirmacao": (cursor + timedelta(days=1)).isoformat(),
                "total_replicados": total,
                "por_destino": _destino_rows_from_counts(day_destino, total=total or None),
                "escolhidos_bot": selected,
                "escolhidos_replicados": matched,
                "escolhidos_nao_replicados": max(selected - matched, 0),
                "replicados_sem_escolha": raw.get("replicados_sem_escolha", 0),
                "pendentes": plan.get(STATUS_PENDENTE, 0),
                "falhos": plan.get(STATUS_FALHOU, 0),
                "recebidos": plan.get(STATUS_RECEBIDO, 0),
                "aguardando_execucao": plan.get(STATUS_PLANEJADO, 0),
                "pendentes_pos_execucao": plan.get(STATUS_PENDENTE, 0),
                "meta": meta,
                "automatico": raw.get("automatico", 0),
                "manual": raw.get("manual", 0),
                "nao_classificado": raw.get("nao_classificado", 0),
                "taxa_replicacao_bot": _pct(matched, selected),
                "atingimento_meta": _pct(total, meta),
            }
        )
        cursor += timedelta(days=1)

    plan_workflow: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    workflow_alias = {
        normalize_key(row["workflow_brflow"]): row["workflow_config"]
        for row in wf_qs.exclude(workflow_brflow="").values("workflow_config", "workflow_brflow")
    }
    for workflow, total in prot_qs.values_list("workflow_config").annotate(total=Count("id")):
        plan_workflow[workflow or "Sem workflow"]["escolhidos_bot"] += total
    for workflow, total in (
        paired_protocol_qs
        .values_list("workflow_config")
        .annotate(total=Count("id"))
    ):
        plan_workflow[workflow or "Sem workflow"]["escolhidos_replicados"] += total
    for workflow, total in paired_protocol_qs.values_list("workflow_config").annotate(
        total=Count("id")
    ):
        plan_workflow[workflow or "Sem workflow"]["total_replicados"] += total
    for workflow, total in outside_qs.values_list("workflow_origem").annotate(total=Count("id")):
        label = workflow_alias.get(normalize_key(workflow or ""), workflow or "Sem workflow")
        plan_workflow[label]["total_replicados"] += total
        plan_workflow[label]["replicados_sem_escolha"] += total
    workflow_performance = []
    for label, values in plan_workflow.items():
        selected = values.get("escolhidos_bot", 0)
        matched = values.get("escolhidos_replicados", 0)
        workflow_performance.append(
            {
                "label": label,
                "total_replicados": values.get("total_replicados", 0),
                "escolhidos_bot": selected,
                "escolhidos_replicados": matched,
                "escolhidos_nao_replicados": max(selected - matched, 0),
                "replicados_sem_escolha": values.get("replicados_sem_escolha", 0),
                "taxa_replicacao_bot": _pct(matched, selected),
            }
        )
    workflow_performance.sort(
        key=lambda row: (row["escolhidos_nao_replicados"], row["total_replicados"]), reverse=True
    )

    mix_counts = defaultdict(int)
    for conclusion, total in rep_qs.values_list("tipo_conclusao_analise_origem").annotate(
        total=Count("id")
    ):
        mix_counts[_mix_bucket(conclusion)] += total

    today = timezone.localdate()
    future_capacity, _ = _daily_capacity(today + timedelta(days=1), today + timedelta(days=15))

    week_params = replace(params, data_de=today - timedelta(days=6), data_ate=today, status="", protocolo="")
    week_plans = _filter_protocolos(week_params)
    week_reps = _filter_replicados(week_params)
    week_paired_protocols, _week_paired_reps, _week_fast = _paired_querysets(
        week_plans,
        week_reps,
    )
    week_capacity, _ = _daily_capacity(week_params.data_de, week_params.data_ate)
    week_rep_daily: dict[date, int] = defaultdict(int)
    for report_date, total in (
        week_reps.values_list("report_date").annotate(total=Count("id"))
    ):
        week_rep_daily[report_date - timedelta(days=1)] += int(total)
    active_days = [day for day, total in week_rep_daily.items() if total > 0]
    active_meta = sum(week_capacity.get(day, 0) for day in active_days)
    active_replicated = sum(week_rep_daily[day] for day in active_days)
    recent_rate = (active_replicated / active_meta) if active_meta > 0 else 1.2
    projection_fallback = active_meta <= 0

    projection_series = []
    for offset in range(1, 16):
        day = today + timedelta(days=offset)
        meta = future_capacity.get(day, 0)
        projection_series.append(
            {
                "data": day.isoformat(),
                "meta": meta,
                "projetado": int(round(meta * recent_rate)) if meta > 0 else None,
                "escala_disponivel": day in future_capacity,
            }
        )

    destino_counts = _aggregate_destino_counts(rep_qs)
    por_destino = _destino_rows_from_counts(destino_counts, total=total_replicated)

    return {
        "_paired_protocol_ids": set(paired_protocol_qs.values_list("pk", flat=True)),
        "_outside_replicado_qs": outside_qs,
        "_fast_reconciliation": fast_reconciliation,
        "summary": {
            "total_replicados": total_replicated,
            "por_destino": por_destino,
            "escolhidos_bot": chosen,
            "escolhidos_replicados": chosen_replicated,
            "escolhidos_nao_replicados": chosen_not_replicated,
            "replicados_sem_escolha": outside_sample,
            "taxa_replicacao_bot": _pct(chosen_replicated, chosen),
            "pct_fora_amostra": _pct(outside_sample, total_replicated),
            "tempo_medio_escolhidos_horas": _average_replication_hours(paired_rep_qs),
            "tempo_medio_fora_amostra_horas": _average_replication_hours(outside_qs),
            "workflows_com_gap": sum(
                1 for row in workflow_performance if row["escolhidos_nao_replicados"] > 0
            ),
            "meta_periodo": total_capacity,
            "protocolos_salvos": int(saved_protocols),
            "atingimento_meta": _pct(total_replicated, total_capacity),
            "gap_meta": total_capacity - total_replicated,
            "runs_sem_confirmacao": runs_without_confirmation,
        },
        "daily_series": daily_series,
        "workflow_performance": workflow_performance,
        "mix": {
            "automatico": mix_counts["automatico"],
            "manual": mix_counts["manual"],
            "nao_classificado": mix_counts["nao_classificado"],
            "pct_automatico": _pct(mix_counts["automatico"], total_replicated),
        },
        "projection": {
            "metodo": "taxa_recente_capacidade",
            "premissa_percentual_meta": round(recent_rate * 100, 1),
            "meta_diaria_por_auditor": daily_quota,
            "fallback_120_usado": projection_fallback,
            "proximos_7_dias": sum(
                row["projetado"] or 0 for row in projection_series[:7]
            ),
            "proximos_15_dias": sum(row["projetado"] or 0 for row in projection_series),
            # Alias temporário do contrato v3.
            "proximos_30_dias": sum(row["projetado"] or 0 for row in projection_series),
            "media_replicados_ultimos_7_dias": round(week_reps.count() / 7),
            "taxa_rep_meta_ultimos_7_dias": _pct(week_reps.count(), active_meta),
            "taxa_bot_ultimos_7_dias": _pct(week_paired_protocols.count(), week_plans.count()),
            "series": projection_series,
        },
    }


def _ingestion_health(params: DashboardParams) -> dict[str, Any]:
    ingest_qs = BotDataIngestion.objects.filter(domain="replicacao_d1")
    if params.data_de:
        ingest_qs = ingest_qs.filter(reference_date__gte=params.data_de)
    if params.data_ate:
        ingest_qs = ingest_qs.filter(reference_date__lte=params.data_ate)
    if params.run_id:
        ingest_qs = ingest_qs.filter(run_id=params.run_id)

    last_ok = (
        ingest_qs.filter(status=BotDataIngestion.STATUS_COMPLETED)
        .order_by("-finished_at")
        .first()
    )
    last_any = ingest_qs.order_by("-finished_at").first()
    sync_logs = ReplicacaoD1SyncLog.objects.all()
    if params.run_id:
        sync_logs = sync_logs.filter(run_id=params.run_id)
    last_sync = sync_logs.order_by("-started_at").first()

    delay_hours: float | None = None
    stale = False
    if last_ok and last_ok.finished_at:
        delay_hours = round(
            (timezone.now() - last_ok.finished_at).total_seconds() / 3600,
            1,
        )
        stale = delay_hours > 24

    return {
        "last_success": _serialize_ingestion(last_ok),
        "last_ingestion": _serialize_ingestion(last_any),
        "last_sync_log": (
            {
                "id": last_sync.pk,
                "kind": last_sync.kind,
                "success": last_sync.success,
                "run_id": last_sync.run_id,
                "report_date": last_sync.report_date.isoformat() if last_sync.report_date else None,
                "finished_at": last_sync.finished_at.isoformat() if last_sync.finished_at else None,
                "rows_loaded": last_sync.rows_loaded or last_sync.row_count,
                "rows_rejected": last_sync.rows_rejected or 0,
                "message": last_sync.message[:200] if last_sync.message else "",
            }
            if last_sync
            else None
        ),
        "delay_hours": delay_hours,
        "stale": stale,
        "rows_rejected_total": ingest_qs.aggregate(total=Sum("rows_rejected"))["total"] or 0,
    }


def _serialize_ingestion(ing: BotDataIngestion | None) -> dict[str, Any] | None:
    if ing is None:
        return None
    return {
        "id": ing.pk,
        "source_key": ing.source_key[:16] + "…",
        "kind": ing.kind,
        "reference_date": ing.reference_date.isoformat() if ing.reference_date else None,
        "run_id": ing.run_id,
        "status": ing.status,
        "rows_read": ing.rows_read,
        "rows_loaded": ing.rows_loaded,
        "rows_rejected": ing.rows_rejected,
        "finished_at": ing.finished_at.isoformat() if ing.finished_at else None,
        "error_summary": ing.error_summary[:200] if ing.error_summary else "",
    }


def _time_series(params: DashboardParams, prot_qs: QuerySet) -> list[dict[str, Any]]:
    by_date: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in (
        prot_qs.annotate(execution_day=TruncDate("run__data_execucao"))
        .values("execution_day", "status_operacional")
        .annotate(count=Count("id"))
        .order_by("execution_day")
    ):
        if row["execution_day"] is None:
            continue
        key = row["execution_day"].isoformat()
        by_date[key][row["status_operacional"]] = row["count"]
        by_date[key]["total"] = by_date[key].get("total", 0) + row["count"]

    series = []
    for dt in sorted(by_date.keys()):
        bucket = by_date[dt]
        total = bucket.get("total", 0)
        replicados = bucket.get(STATUS_REPLICADO, 0)
        series.append(
            {
                "data": dt,
                "planejados": total,
                "replicados": replicados,
                "pendentes": bucket.get(STATUS_PENDENTE, 0),
                "falhos": bucket.get(STATUS_FALHOU, 0),
                "recebidos": bucket.get(STATUS_RECEBIDO, 0),
                "taxa_sucesso": _pct(replicados, total),
            }
        )
    return series


def _breakdown_by_field(
    prot_qs: QuerySet,
    wf_qs: QuerySet,
    field: str,
) -> list[dict[str, Any]]:
    if field == "workflow":
        rows = (
            prot_qs.values("workflow_config", "status_operacional")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        agg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for row in rows:
            agg[row["workflow_config"]][row["status_operacional"]] = row["count"]
            agg[row["workflow_config"]]["total"] += row["count"]
        return [
            {"label": k, **v, "taxa_sucesso": _pct(v.get(STATUS_REPLICADO, 0), v.get("total", 0))}
            for k, v in sorted(agg.items(), key=lambda x: -x[1].get("total", 0))[:50]
        ]

    if field == "cliente":
        wf_map: dict[tuple[str, str], str] = {}
        for wf in wf_qs.values("run_id", "workflow_config", "cliente"):
            wf_map[(wf["run_id"], wf["workflow_config"])] = wf["cliente"] or "Sem cliente"
        agg = defaultdict(lambda: defaultdict(int))
        for p in prot_qs.values("run_id", "workflow_config", "status_operacional"):
            cliente = wf_map.get((p["run_id"], p["workflow_config"]), "Sem cliente")
            agg[cliente][p["status_operacional"]] += 1
            agg[cliente]["total"] += 1
        return [
            {"label": k, **v, "taxa_sucesso": _pct(v.get(STATUS_REPLICADO, 0), v.get("total", 0))}
            for k, v in sorted(agg.items(), key=lambda x: -x[1].get("total", 0))[:50]
        ]

    if field == "canal":
        rows = (
            prot_qs.values("canal_destino", "status_operacional")
            .annotate(count=Count("id"))
            .order_by("-count")
        )
        agg = defaultdict(lambda: defaultdict(int))
        for row in rows:
            label = row["canal_destino"] or "Sem canal"
            agg[label][row["status_operacional"]] = row["count"]
            agg[label]["total"] += row["count"]
        return [
            {"label": k, **v, "taxa_sucesso": _pct(v.get(STATUS_REPLICADO, 0), v.get("total", 0))}
            for k, v in sorted(agg.items(), key=lambda x: -x[1].get("total", 0))
        ]

    if field == "status":
        rows = prot_qs.values("status_operacional").annotate(count=Count("id")).order_by("-count")
        return [{"label": r["status_operacional"], "total": r["count"]} for r in rows]

    return []


def _serialize_protocolo(
    p: ReplicacaoD1Protocolo,
    wf_map: dict,
    paired_ids: set[int] | None = None,
) -> dict[str, Any]:
    wf_key = (p.run_id, p.workflow_config)
    wf = wf_map.get(wf_key, {})
    return {
        "id": p.pk,
        "run_id": p.run_id,
        "data_referencia_d1": p.data_referencia_d1.isoformat(),
        "data_execucao": p.run.data_execucao.isoformat() if p.run.data_execucao else None,
        "protocolo": p.protocolo,
        "workflow_config": p.workflow_config,
        "workflow_d1": p.workflow_d1,
        "canal_destino": p.canal_destino,
        "cliente": wf.get("cliente", ""),
        "fila": wf.get("fila", ""),
        "status_brflow": p.status_brflow,
        "status_operacional": STATUS_REPLICADO if p.pk in (paired_ids or set()) else p.status_operacional,
        "replicado_em": p.replicado_em.isoformat() if p.replicado_em else None,
        "erro_resumido": p.erro_resumido,
    }


def build_replicacao_d1_dashboard(params: DashboardParams) -> dict[str, Any]:
    # Status is a drill-down filter, never a denominator filter.  This keeps
    # rates and reconciliation totals stable while the Protocolos tab narrows
    # its visible rows.
    global_params = replace(params, status="")
    global_prot_qs = _filter_protocolos(global_params)
    prot_qs = _status_filtered_protocols(params, global_prot_qs)
    wf_qs = _filter_workflows(global_params)
    official_rep_qs = _filter_replicados(global_params)
    official_visible_qs = official_rep_qs
    if params.status and params.status != STATUS_REPLICADO:
        official_visible_qs = official_rep_qs.none()
    analytics = _replication_analytics(global_params, global_prot_qs, wf_qs)
    replication_summary = analytics["summary"]

    status_counts = dict(
        prot_qs.values("status_operacional").annotate(c=Count("id")).values_list("status_operacional", "c")
    )
    planejados = prot_qs.count()
    replicados = official_rep_qs.count()
    pendentes = status_counts.get(STATUS_PENDENTE, 0)
    falhos = status_counts.get(STATUS_FALHOU, 0)
    # status_operacional is mutually exclusive; confirmations are represented
    # by the paired count and must not be subtracted from RECEBIDO.
    recebidos = status_counts.get(STATUS_RECEBIDO, 0)
    aguardando_execucao = status_counts.get(STATUS_PLANEJADO, 0)
    pendentes_pos_execucao = pendentes
    total_replicados_d1 = replicados
    fora_amostra = replication_summary["replicados_sem_escolha"]

    uploads_workflows = wf_qs.filter(
        Q(status_brflow__iexact="SALVO_OK") | Q(status_brflow__iexact="UPLOAD_OK")
    ).count()
    workflows_total = wf_qs.count()

    divergent = [
        {
            "report_date": row["report_date"].isoformat(),
            "protocolo_origem": row["protocolo_origem"],
            "protocolo_normalizado": row["protocolo_origem_normalizado"],
            "workflow_origem": row["workflow_origem"],
            "status_operacional": STATUS_DIVERGENTE,
        }
        for row in analytics["_outside_replicado_qs"].values(
            "report_date",
            "protocolo_origem",
            "protocolo_origem_normalizado",
            "workflow_origem",
        )[:20]
    ]
    divergencias_total = analytics["_outside_replicado_qs"].count()
    divergentes = len(divergent)

    from apps.common.models import BotDataIngestion

    last_ingestion = (
        BotDataIngestion.objects.filter(
            domain="replicacao_d1",
            status=BotDataIngestion.STATUS_COMPLETED,
        )
        .order_by("-finished_at")
        .values_list("finished_at", flat=True)
        .first()
    )
    as_of = last_ingestion
    if as_of is None:
        as_of = prot_qs.aggregate(max_sync=Max("run__synced_at"))["max_sync"]

    wf_map = {
        (w.run_id, w.workflow_config): {
            "cliente": w.cliente,
            "fila": w.fila,
            "segmento": w.segmento,
            "status_brflow": w.status_brflow,
        }
        for w in wf_qs.only("run_id", "workflow_config", "cliente", "fila", "segmento", "status_brflow")
    }

    paginator = Paginator(
        prot_qs.order_by("-run__data_execucao", "workflow_config", "protocolo"),
        params.page_size,
    )
    page_obj = paginator.get_page(params.page)
    protocolos_page = [
        _serialize_protocolo(p, wf_map, analytics["_paired_protocol_ids"])
        for p in page_obj.object_list
    ]

    official_paginator = Paginator(
        official_visible_qs.order_by("-report_date", "cliente_origem", "protocolo_origem"),
        params.page_size,
    )
    official_page_obj = official_paginator.get_page(params.page)
    official_page = [_serialize_official_replicado(row) for row in official_page_obj.object_list]

    data_de = params.data_de or timezone.localdate()
    data_ate = params.data_ate or data_de
    productivity = _daily_productivity(data_de, data_ate)
    calendar_rows = [
        {
            "data": row["data_execucao"],
            "data_confirmacao": row["data_confirmacao"],
            "produtividade": productivity.get(date.fromisoformat(row["data_execucao"]), 0),
            "replicados": row["total_replicados"],
            "por_destino": row.get("por_destino", []),
            "meta": row["meta"],
            "atingimento_meta": row["atingimento_meta"],
            "bot_escolhidos": row["escolhidos_bot"],
            "bot_confirmados": row["escolhidos_replicados"],
        }
        for row in analytics["daily_series"]
    ]
    exceptions, unmapped_clients = _client_exceptions(wf_qs, official_rep_qs)
    total_productivity = sum(row["produtividade"] for row in calendar_rows)
    total_capacity = replication_summary["meta_periodo"]

    runs_in_scope = prot_qs.values_list("run_id", flat=True).distinct()[:20]
    ingestion_health = _ingestion_health(params)
    data_quality = {
        "runs_sem_confirmacao_d1": replication_summary["runs_sem_confirmacao"],
        "carga_atrasada": bool(ingestion_health["stale"]),
        "registros_rejeitados": ingestion_health["rows_rejected_total"],
        "protocolos_nao_classificados": analytics["mix"]["nao_classificado"],
        "clientes_nao_mapeados": unmapped_clients,
        "pendencias_mais_24h": 0,
        "cobertura_reconciliacao": _pct(
            replication_summary["escolhidos_replicados"],
            replication_summary["escolhidos_bot"],
        ),
    }
    diagnostic = _build_diagnostic(
        replication_summary,
        analytics["workflow_performance"],
        exceptions,
        analytics["daily_series"],
        data_quality,
    )

    return {
        "meta": {
            "contract_version": 6,
            "previous_contract_version": 5,
            "deprecated_aliases": {
                "replicados": "confirmados_d1",
                "divergentes": "divergencias_total",
                "uploads_concluidos": "recebidos + confirmados_d1",
                "projection.proximos_30_dias": "projection.proximos_15_dias",
            },
            "date_axis": "execucao",
            "confirmation_pairing": "D+1_calendar",
            "default_period_days": 7,
            "data_de": params.data_de.isoformat() if params.data_de else None,
            "data_ate": params.data_ate.isoformat() if params.data_ate else None,
            "run_id": params.run_id or None,
            "as_of": as_of.isoformat() if as_of and hasattr(as_of, "isoformat") else None,
            "reconciliation_rule_version": "1",
            "rollout_flags": rollout_flags_snapshot(),
            "runs_in_scope": list(runs_in_scope),
            "official_source": "replicacao_d1_replicado",
            "projection_assumption": "taxa replicados/meta dos últimos 7 dias ativos aplicada à escala futura; fallback de 120%",
        },
        "indicators": {
            "planejados": planejados,
            "replicados": replicados,
            "produtividade_gaq": total_productivity,
            "capacidade_periodo": total_capacity,
            "pendentes": pendentes,
            "falhos": falhos,
            "recebidos": recebidos,
            "divergentes": divergentes,
            "uploads_concluidos": recebidos + replication_summary["escolhidos_replicados"],
            "aguardando_execucao": aguardando_execucao,
            "pendentes_pos_execucao": pendentes_pos_execucao,
            "confirmados_d1": replicados,
            "total_replicados_d1": total_replicados_d1,
            "fora_amostra": fora_amostra,
            "divergencias_total": divergencias_total,
            "workflows_upload_ok": uploads_workflows,
            "workflows_total": workflows_total,
            "taxa_sucesso": _pct(replicados, planejados),
            "taxa_upload": _pct(recebidos + replication_summary["escolhidos_replicados"], planejados),
            "taxa_confirmacao": _pct(replicados, planejados),
            "cobertura": _pct(replicados, planejados),
        },
        "time_series": [
            {
                "data": row["data_execucao"],
                "planejados": row["escolhidos_bot"],
                "replicados": row["total_replicados"],
                "pendentes": row["pendentes"],
                "falhos": row["falhos"],
                "recebidos": row["recebidos"],
                "aguardando_execucao": row["aguardando_execucao"],
                "pendentes_pos_execucao": row["pendentes_pos_execucao"],
                "taxa_sucesso": row["taxa_replicacao_bot"],
            }
            for row in analytics["daily_series"]
        ],
        "replication_summary": replication_summary,
        "daily_series": analytics["daily_series"],
        "calendario": calendar_rows,
        "excecoes_clientes": exceptions,
        "workflow_performance": analytics["workflow_performance"],
        "replication_mix": analytics["mix"],
        "projection": analytics["projection"],
        "diagnostic": diagnostic,
        "filter_options": _filter_options(params),
        "breakdown": {
            "by_workflow": _official_breakdown(official_rep_qs, "workflow_origem"),
            "by_workflow_destino": _breakdown_by_workflow_destino(official_rep_qs),
            "by_cliente": _official_breakdown(official_rep_qs, "cliente_origem"),
            "by_canal": _breakdown_by_field(prot_qs, wf_qs, "canal"),
            "by_status": _breakdown_by_field(prot_qs, wf_qs, "status"),
        },
        "protocolos": {
            "count": paginator.count,
            "page": page_obj.number,
            "page_size": params.page_size,
            "num_pages": paginator.num_pages,
            "results": protocolos_page,
        },
        "replicados_oficiais": {
            "count": official_paginator.count,
            "page": official_page_obj.number,
            "page_size": params.page_size,
            "num_pages": official_paginator.num_pages,
            "results": official_page,
        },
        "comparativo_bot": {
            "escolhidos": replication_summary["escolhidos_bot"],
            "escolhidos_replicados": replication_summary["escolhidos_replicados"],
            "escolhidos_nao_replicados": replication_summary["escolhidos_nao_replicados"],
            "replicados_sem_escolha": replication_summary["replicados_sem_escolha"],
            "taxa_replicacao": replication_summary["taxa_replicacao_bot"],
        },
        "divergentes_amostra": divergent,
        "comparison": {
            "periodo_referencia": {
                "de": params.data_de.isoformat() if params.data_de else None,
                "ate": params.data_ate.isoformat() if params.data_ate else None,
            },
            "periodo_anterior": None,
            "indicadores": {
                "planejados": planejados,
                "uploads_concluidos": recebidos + replication_summary["escolhidos_replicados"],
                "confirmados_d1": replication_summary["escolhidos_replicados"],
                "fora_amostra": fora_amostra,
                "divergencias_total": divergencias_total,
            },
            "percentuais": {
                "taxa_upload": _pct(recebidos + replication_summary["escolhidos_replicados"], planejados),
                "taxa_confirmacao": _pct(replication_summary["escolhidos_replicados"], planejados),
                "cobertura_reconciliacao": _pct(replication_summary["escolhidos_replicados"], planejados),
            },
        },
        "data_quality": data_quality,
        "ingestion_health": ingestion_health,
    }


def _build_diagnostic(
    summary: dict[str, Any],
    workflows: list[dict[str, Any]],
    exceptions: dict[str, list[dict[str, Any]]],
    daily_series: list[dict[str, Any]],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    issue_order = {"sem_confirmacao": 0, "parcial": 1, "fora_amostra": 2}
    workflow_rows = []
    for row in workflows:
        selected = int(row["escolhidos_bot"])
        confirmed = int(row["escolhidos_replicados"])
        outside = int(row["replicados_sem_escolha"])
        if selected > 0 and confirmed == 0:
            issue = "sem_confirmacao"
        elif confirmed < selected:
            issue = "parcial"
        elif outside > 0:
            issue = "fora_amostra"
        else:
            continue
        workflow_rows.append({**row, "tipo": issue, "gap": max(selected - confirmed, 0)})
    workflow_rows.sort(
        key=lambda row: (issue_order[row["tipo"]], -row["gap"], normalize_key(row["label"]))
    )

    client_rows = []
    for group, issue in (
        ("volume_sem_replicacao", "sem_confirmacao"),
        ("replicacao_parcial", "parcial"),
    ):
        for row in exceptions.get(group, []):
            client_rows.append({**row, "tipo": issue})
    client_rows.sort(
        key=lambda row: (issue_order[row["tipo"]], -int(row["gap"]), normalize_key(row["cliente"]))
    )

    return {
        "summary": {
            "gap_total": int(summary["escolhidos_nao_replicados"]),
            "workflows_com_gap": int(summary["workflows_com_gap"]),
            "clientes_sem_replicacao": len({
                normalize_key(row["cliente"])
                for row in exceptions.get("volume_sem_replicacao", [])
            }),
            "clientes_parciais": len({
                normalize_key(row["cliente"])
                for row in exceptions.get("replicacao_parcial", [])
            }),
            "fora_amostra": int(summary["replicados_sem_escolha"]),
            "cobertura": float(data_quality["cobertura_reconciliacao"]),
        },
        "workflows": workflow_rows,
        "clientes": client_rows,
        "daily_trend": [
            {
                "data": row["data_execucao"],
                "escolhidos": row["escolhidos_bot"],
                "confirmados": row["escolhidos_replicados"],
                "gap": row["escolhidos_nao_replicados"],
                "fora_amostra": row["replicados_sem_escolha"],
                "taxa": row["taxa_replicacao_bot"],
            }
            for row in daily_series
        ],
        "quality_warning": {
            "attention": bool(
                data_quality["carga_atrasada"]
                or data_quality["registros_rejeitados"]
                or data_quality["protocolos_nao_classificados"]
                or data_quality.get("clientes_nao_mapeados", 0)
            ),
            "carga_atrasada": data_quality["carga_atrasada"],
            "registros_rejeitados": data_quality["registros_rejeitados"],
            "protocolos_nao_classificados": data_quality["protocolos_nao_classificados"],
            "clientes_nao_mapeados": data_quality.get("clientes_nao_mapeados", 0),
        },
    }


def _projection_aliases() -> dict[str, dict[str, str]]:
    aliases: dict[str, dict[str, str]] = {}
    workflows = ReplicacaoD1Workflow.objects.filter(ativo=True).select_related("cliente").order_by("nome_canonico")
    for workflow in workflows:
        channel_key = _projection_channel_key(workflow.fila)
        metadata = {
            "workflow": workflow.nome_canonico,
            "cliente": workflow.cliente.nome if workflow.cliente_id else "",
            "fila": workflow.fila or "",
            "canal_key": channel_key,
            "canal": PROJECTION_CHANNELS[channel_key]["label"],
        }
        for name in (
            workflow.nome_canonico,
            workflow.nome_d1,
            workflow.nome_selenium,
            workflow.nome_regra_brflow,
            workflow.nome_observado_original,
        ):
            key = normalize_key(name or "")
            if key and key not in aliases:
                aliases[key] = metadata
    return aliases


def _projection_history(today: date) -> tuple[date, date, int, QuerySet[ReplicacaoD1Replicado]]:
    history_end = today
    history_start = today - timedelta(days=6)
    queryset = ReplicacaoD1Replicado.objects.filter(
        report_date__gte=history_start + timedelta(days=1),
        report_date__lte=history_end + timedelta(days=1),
    )
    if queryset.exists():
        return history_start, history_end, 7, queryset
    history_start = today - timedelta(days=29)
    queryset = ReplicacaoD1Replicado.objects.filter(
        report_date__gte=history_start + timedelta(days=1),
        report_date__lte=history_end + timedelta(days=1),
    )
    return history_start, history_end, 30, queryset


def _allocate_projection(total: int, rows: list[dict[str, Any]]) -> list[int]:
    if total <= 0 or not rows:
        return [0 for _ in rows]
    raw = [total * row["share"] for row in rows]
    allocated = [int(value) for value in raw]
    remaining = total - sum(allocated)
    order = sorted(
        range(len(rows)),
        key=lambda index: (-(raw[index] - allocated[index]), normalize_key(rows[index]["workflow"])),
    )
    for index in order[:remaining]:
        allocated[index] += 1
    return allocated


def _build_workflow_projection_rows() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    today = timezone.localdate()
    history_start, history_end, history_days, history_qs = _projection_history(today)
    aliases = _projection_aliases()
    grouped: dict[str, dict[str, Any]] = {}
    for item in history_qs.values("workflow_origem", "cliente_origem").annotate(total=Count("id")):
        original = item["workflow_origem"] or "Sem workflow"
        metadata = aliases.get(normalize_key(original))
        canonical = metadata["workflow"] if metadata else original
        key = normalize_key(canonical) or canonical
        row = grouped.setdefault(
            key,
            {
                "workflow": canonical,
                "cliente": metadata["cliente"] if metadata else (item["cliente_origem"] or ""),
                "fila": metadata["fila"] if metadata else "",
                "canal_key": metadata["canal_key"] if metadata else "brflow",
                "canal": metadata["canal"] if metadata else PROJECTION_CHANNELS["brflow"]["label"],
                "historical_replicated": 0,
            },
        )
        row["historical_replicated"] += int(item["total"])

    total_history = sum(row["historical_replicated"] for row in grouped.values())
    rows = list(grouped.values())
    history_by_channel: dict[str, int] = defaultdict(int)
    for row in rows:
        history_by_channel[row["canal_key"]] += row["historical_replicated"]
    for row in rows:
        channel_history = history_by_channel[row["canal_key"]]
        row["share"] = row["historical_replicated"] / channel_history if channel_history else 0.0
        row["share_pct"] = 0.0
        row["series"] = []

    comparison_start = today - timedelta(days=14)
    comparison_qs = ReplicacaoD1Replicado.objects.filter(
        report_date__gte=comparison_start + timedelta(days=1),
        report_date__lte=today + timedelta(days=1),
    )
    comparison_global: dict[date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    comparison_by_workflow: dict[str, dict[date, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for item in comparison_qs.values(
        "report_date", "workflow_origem", "tipo_conclusao_analise_origem"
    ).annotate(total=Count("id")):
        operational_day = item["report_date"] - timedelta(days=1)
        total = int(item["total"])
        bucket = _mix_bucket(item["tipo_conclusao_analise_origem"])
        comparison_global[operational_day][bucket] += total
        original = item["workflow_origem"] or "Sem workflow"
        metadata = aliases.get(normalize_key(original))
        canonical = metadata["workflow"] if metadata else original
        comparison_by_workflow[normalize_key(canonical) or canonical][operational_day][bucket] += total

    historical_series = [
        {
            "data": (comparison_start + timedelta(days=offset)).isoformat(),
            "realizado": sum(comparison_global[comparison_start + timedelta(days=offset)].values()),
            "automatico": comparison_global[comparison_start + timedelta(days=offset)]["automatico"],
            "manual": comparison_global[comparison_start + timedelta(days=offset)]["manual"],
            "nao_classificado": comparison_global[comparison_start + timedelta(days=offset)]["nao_classificado"],
        }
        for offset in range(15)
    ]
    for key, row in grouped.items():
        row["historical_series"] = [
            {
                "data": (comparison_start + timedelta(days=offset)).isoformat(),
                "actual": sum(comparison_by_workflow[key][comparison_start + timedelta(days=offset)].values()),
                "automatic": comparison_by_workflow[key][comparison_start + timedelta(days=offset)]["automatico"],
                "manual": comparison_by_workflow[key][comparison_start + timedelta(days=offset)]["manual"],
                "unclassified": comparison_by_workflow[key][comparison_start + timedelta(days=offset)]["nao_classificado"],
            }
            for offset in range(15)
        ]
        row["actual_15"] = sum(day["actual"] for day in row["historical_series"])
        row["actual_automatic_15"] = sum(day["automatic"] for day in row["historical_series"])
        row["actual_manual_15"] = sum(day["manual"] for day in row["historical_series"])
        row["actual_unclassified_15"] = sum(day["unclassified"] for day in row["historical_series"])

    history_capacity = _projection_capacity_by_channel(history_start, history_end)
    history_daily_by_channel: dict[str, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    for item in history_qs.values("report_date", "workflow_origem").annotate(total=Count("id")):
        original = item["workflow_origem"] or "Sem workflow"
        metadata = aliases.get(normalize_key(original))
        channel_key = metadata["canal_key"] if metadata else "brflow"
        history_daily_by_channel[channel_key][item["report_date"] - timedelta(days=1)] += int(item["total"])

    channel_rates: dict[str, float] = {}
    fallback_channels: list[str] = []
    for channel_key in {row["canal_key"] for row in rows}:
        daily_history = history_daily_by_channel[channel_key]
        active_days = [day for day, total in daily_history.items() if total > 0]
        active_capacity = sum(history_capacity[channel_key]["daily"].get(day, 0) for day in active_days)
        channel_total = history_by_channel[channel_key]
        channel_rates[channel_key] = (
            channel_total / active_capacity
            if active_capacity > 0
            else (1.2 if channel_total else 0.0)
        )
        if active_capacity <= 0 and channel_total > 0:
            fallback_channels.append(channel_key)

    horizon_start = today + timedelta(days=1)
    horizon_end = today + timedelta(days=15)
    future_capacity = _projection_capacity_by_channel(horizon_start, horizon_end)
    scale_dates = set(future_capacity["brflow"]["daily"])
    missing_scale_dates = [
        (today + timedelta(days=offset)).isoformat()
        for offset in range(1, 16)
        if today + timedelta(days=offset) not in scale_dates
    ]
    rows_by_channel: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_channel[row["canal_key"]].append(row)
    channel_series: dict[str, list[dict[str, Any]]] = defaultdict(list)
    global_series = []
    for offset in range(1, 16):
        day = today + timedelta(days=offset)
        global_capacity = 0
        global_projected = 0
        for channel_key, channel_rows in rows_by_channel.items():
            capacity = future_capacity[channel_key]["daily"].get(day, 0)
            projected = int(round(capacity * channel_rates[channel_key])) if capacity > 0 else 0
            global_capacity += capacity
            global_projected += projected
            channel_series[channel_key].append(
                {"data": day.isoformat(), "capacidade": capacity, "projetado": projected}
            )
            allocated = _allocate_projection(projected, channel_rows)
            for index, row in enumerate(channel_rows):
                row["series"].append(
                    {
                        "data": day.isoformat(),
                        "channel_capacity": capacity,
                        "channel_projected": projected,
                        "projected": allocated[index],
                    }
                )
        global_series.append(
            {"data": day.isoformat(), "meta": global_capacity, "projetado": global_projected}
        )
        for row in rows:
            row["series"][-1]["global_capacity"] = global_capacity
            row["series"][-1]["global_projected"] = global_projected

    total_projected = 0
    for row in rows:
        row["projected_7"] = sum(day["projected"] for day in row["series"][:7])
        row["projected_15"] = sum(day["projected"] for day in row["series"])
        total_projected += row["projected_15"]
    for row in rows:
        row["share_pct"] = round(
            (row["projected_15"] / total_projected * 100)
            if total_projected
            else (row["historical_replicated"] / total_history * 100 if total_history else 0.0),
            2,
        )
        row.pop("share", None)
    rows.sort(key=lambda row: (-row["projected_15"], normalize_key(row["workflow"])))
    channel_details = []
    for channel_key in rows_by_channel:
        definition = future_capacity[channel_key]
        series = channel_series[channel_key]
        channel_details.append(
            {
                "key": channel_key,
                "canal": definition["canal"],
                "fila": definition["fila"],
                "meta_por_auditor": definition["meta_por_auditor"],
                "historical_replicated": history_by_channel[channel_key],
                "recent_rate": round(channel_rates[channel_key], 4),
                "fallback_rate_used": channel_key in fallback_channels and any(
                    item["capacidade"] > 0 for item in series
                ),
                "capacity_15": sum(item["capacidade"] for item in series),
                "projected_15": sum(item["projetado"] for item in series),
                "series": series,
            }
        )
    channel_details.sort(key=lambda item: list(PROJECTION_CHANNELS).index(item["key"]))
    effective_fallback_channels = [
        item["key"] for item in channel_details if item["fallback_rate_used"]
    ]
    meta = {
        "as_of": timezone.now().isoformat(),
        "horizon_start": horizon_start.isoformat(),
        "horizon_end": horizon_end.isoformat(),
        "history_start": history_start.isoformat(),
        "history_end": history_end.isoformat(),
        "history_days": history_days,
        "comparison_start": comparison_start.isoformat(),
        "comparison_end": today.isoformat(),
        "method": "participacao_oficial_capacidade_por_canal",
        "fallback_rate_used": bool(effective_fallback_channels),
        "fallback_channels": effective_fallback_channels,
        "scale_days_covered": 15 - len(missing_scale_dates),
        "missing_scale_dates": missing_scale_dates,
        "global_series": global_series,
        "channel_series": channel_details,
        "historical_series": historical_series,
        "historical_replicated": total_history,
    }
    return meta, rows


def build_projection_destino_breakdown(
    params: WorkflowProjectionParams,
) -> dict[str, Any]:
    """Breakdown oficial por workflow destino na janela realizada da projeção (15 dias)."""
    today = timezone.localdate()
    comparison_start = today - timedelta(days=14)
    rep_qs = ReplicacaoD1Replicado.objects.filter(
        report_date__gte=comparison_start + timedelta(days=1),
        report_date__lte=today + timedelta(days=1),
    )
    if params.cliente:
        rep_qs = rep_qs.filter(cliente_origem__iexact=params.cliente)
    if params.workflow:
        workflow_names = {params.workflow}
        workflow_names.update(
            ReplicacaoD1WorkflowDia.objects.filter(workflow_config__iexact=params.workflow)
            .exclude(workflow_brflow="")
            .values_list("workflow_brflow", flat=True)
        )
        workflow_filter = Q()
        for name in workflow_names:
            workflow_filter |= Q(workflow_origem__iexact=name)
        rep_qs = rep_qs.filter(workflow_filter)

    destino_bucket = None
    if params.canal:
        destino_bucket = destino_bucket_for_projection_canal(params.canal)
    elif params.fila:
        destino_bucket = destino_bucket_for_fila(params.fila)
    if destino_bucket:
        rep_qs = rep_qs.filter(workflow_destino_q_for_bucket(destino_bucket))
    elif params.canal or params.fila:
        rep_qs = rep_qs.none()

    total = rep_qs.count()
    por_destino = _destino_rows_from_counts(_aggregate_destino_counts(rep_qs), total=total)
    return {
        "total_replicados": total,
        "por_destino": por_destino,
        "periodo_de": comparison_start.isoformat(),
        "periodo_ate": today.isoformat(),
    }


def _filter_workflow_projection_rows(
    rows: list[dict[str, Any]],
    params: WorkflowProjectionParams,
) -> list[dict[str, Any]]:
    if params.workflow:
        needle = normalize_key(params.workflow)
        rows = [row for row in rows if needle in normalize_key(row["workflow"])]
    if params.cliente:
        rows = [row for row in rows if normalize_key(row["cliente"]) == normalize_key(params.cliente)]
    if params.canal:
        rows = [row for row in rows if normalize_key(row["canal"]) == normalize_key(params.canal)]
    if params.fila:
        rows = [row for row in rows if normalize_key(row["fila"]) == normalize_key(params.fila)]
    return rows


def _projection_filtered_summary(meta: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected_channels = {row["canal_key"] for row in rows}
    channels = [item for item in meta["channel_series"] if item["key"] in selected_channels]
    global_series = []
    for index, source_day in enumerate(meta["global_series"]):
        global_series.append(
            {
                "data": source_day["data"],
                "meta": sum(int(channel["series"][index]["capacidade"]) for channel in channels),
                "projetado": sum(int(row["series"][index]["projected"]) for row in rows),
            }
        )
    historical_series = []
    for index, source_day in enumerate(meta["historical_series"]):
        historical_series.append(
            {
                "data": source_day["data"],
                "realizado": sum(int(row["historical_series"][index]["actual"]) for row in rows),
                "automatico": sum(int(row["historical_series"][index]["automatic"]) for row in rows),
                "manual": sum(int(row["historical_series"][index]["manual"]) for row in rows),
                "nao_classificado": sum(int(row["historical_series"][index]["unclassified"]) for row in rows),
            }
        )
    projected_15 = sum(row["projetado"] for row in global_series)
    actual_15 = sum(row["realizado"] for row in historical_series)
    return {
        "global_projected_7": sum(row["projetado"] for row in global_series[:7]),
        "global_projected_15": projected_15,
        "global_actual_15": actual_15,
        "global_actual_automatic_15": sum(row["automatico"] for row in historical_series),
        "global_actual_manual_15": sum(row["manual"] for row in historical_series),
        "global_actual_unclassified_15": sum(row["nao_classificado"] for row in historical_series),
        "variation_15_pct": round(((projected_15 - actual_15) / actual_15) * 100, 1) if actual_15 else None,
        "workflow_count": len(rows),
        "historical_replicated": sum(int(row["historical_replicated"]) for row in rows),
        "capacity_15": sum(row["meta"] for row in global_series),
        "global_series": global_series,
        "channel_series": channels,
        "historical_series": historical_series,
    }


def build_workflow_projection_export_data(
    params: WorkflowProjectionParams,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retorna a base completa da projeção respeitando os filtros da exportação."""
    meta, rows = _build_workflow_projection_rows()
    rows = _filter_workflow_projection_rows(rows, params)
    return meta, rows


def build_workflow_projection(params: WorkflowProjectionParams) -> dict[str, Any]:
    meta, all_rows = _build_workflow_projection_rows()
    clientes = sorted({row["cliente"] for row in all_rows if row["cliente"]})
    canais = sorted({row["canal"] for row in all_rows if row["canal"]})
    filas = sorted({row["fila"] for row in all_rows if row["fila"]})
    rows = _filter_workflow_projection_rows(all_rows, params)
    paginator = Paginator(rows, params.page_size)
    page_obj = paginator.get_page(params.page)
    summary = _projection_filtered_summary(meta, rows)
    response_meta = {
        key: value
        for key, value in meta.items()
        if key not in {"global_series", "historical_series", "channel_series"}
    }
    return {
        "meta": response_meta,
        "summary": summary,
        "filter_options": {"clientes": clientes, "canais": canais, "filas": filas},
        "count": paginator.count,
        "page": page_obj.number,
        "page_size": params.page_size,
        "num_pages": paginator.num_pages,
        "results": list(page_obj.object_list),
    }


def build_workflow_projection_export_csv(params: WorkflowProjectionParams) -> tuple[str, bytes]:
    meta, rows = build_workflow_projection_export_data(params)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow([
        "Workflow", "Cliente", "Canal", "Fila", "Data", "Janela historica (dias)",
        "Replicados no historico", "Participacao (%)", "Capacidade do canal",
        "Projecao do canal", "Capacidade global", "Projecao global",
        "Projecao workflow", "Metodo", "Fallback taxa",
    ])
    for row in rows:
        for day in row["series"]:
            writer.writerow([
                row["workflow"], row["cliente"], row["canal"], row["fila"], day["data"], meta["history_days"],
                row["historical_replicated"], str(row["share_pct"]).replace(".", ","),
                day["channel_capacity"], day["channel_projected"],
                day["global_capacity"], day["global_projected"], day["projected"],
                meta["method"], "sim" if row["canal_key"] in meta["fallback_channels"] else "nao",
            ])
    filename = f"projecao-workflows-{meta['horizon_start']}-{meta['horizon_end']}.csv"
    return filename, ("\ufeff" + buffer.getvalue()).encode("utf-8")


def build_dashboard_export_csv(params: DashboardParams) -> tuple[str, str]:
    """Exporta as confirmações oficiais e sinaliza o pareamento com o bot."""
    export_params = replace(params, status="")
    rep_qs = _filter_replicados(export_params)
    if params.status and params.status != STATUS_REPLICADO:
        rep_qs = rep_qs.none()
    prot_qs = _filter_protocolos(export_params)
    paired = _paired_links(prot_qs, rep_qs)
    paired_by_rep = {rep_id: protocol_id for protocol_id, rep_id in paired.items()}
    protocol_meta = {
        row["id"]: row for row in prot_qs.values("id", "run_id", "workflow_config")
    }
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow(
        [
            "Data operacional",
            "Data confirmação",
            "Protocolo origem",
            "Protocolo destino",
            "Cliente origem",
            "Cliente destino",
            "Workflow origem",
            "Workflow destino",
            "Data cadastro origem",
            "Data cadastro destino",
            "Tipo conclusão",
            "Escolhido pelo bot",
            "Run ID",
        ]
    )
    for row in rep_qs.order_by("-report_date", "cliente_origem", "protocolo_origem").iterator(
        chunk_size=2000
    ):
        protocol = protocol_meta.get(paired_by_rep.get(row.pk), {})
        writer.writerow(
            [
                (row.report_date - timedelta(days=1)).isoformat(),
                row.report_date.isoformat(),
                row.protocolo_origem,
                row.protocolo_destino,
                row.cliente_origem,
                row.cliente_destino,
                row.workflow_origem,
                row.workflow_destino,
                row.data_cadastro_origem.isoformat() if row.data_cadastro_origem else "",
                row.data_cadastro_destino.isoformat() if row.data_cadastro_destino else "",
                row.tipo_conclusao_analise_origem,
                "Sim" if protocol else "Não",
                protocol.get("run_id", ""),
            ]
        )
    filename = f"replicacao-d1-{params.data_de:%Y%m%d}-{params.data_ate:%Y%m%d}.csv"
    return filename, "\ufeff" + buffer.getvalue()


def build_protocolo_detail(run_id: str, protocolo: str) -> dict[str, Any] | None:
    norm = normalize_protocolo(protocolo)
    prot = (
        ReplicacaoD1Protocolo.objects.filter(run_id=run_id)
        .filter(Q(protocolo=protocolo) | Q(protocolo_normalizado=norm))
        .first()
    )
    if prot is None:
        return None

    wf = (
        ReplicacaoD1WorkflowDia.objects.filter(
            run_id=run_id,
            workflow_config=prot.workflow_config,
        ).first()
    )
    rep = (
        ReplicacaoD1Replicado.objects.filter(
            protocolo_origem_normalizado=prot.protocolo_normalizado or norm,
            report_date__gte=prot.data_referencia_d1,
            report_date__lte=prot.data_referencia_d1 + timedelta(days=2),
        )
        .order_by("report_date")
        .first()
    )

    run = ReplicacaoD1Run.objects.filter(run_id=run_id).first()
    display_status = prot.status_operacional
    if (
        rep
        and run
        and run.data_execucao
        and rep.report_date == run.data_execucao.date() + timedelta(days=1)
    ):
        display_status = STATUS_REPLICADO

    divergencia = None
    if prot.status_operacional == STATUS_DIVERGENTE:
        divergencia = "Plano e confirmação não fecham."
    elif rep and wf and rep.workflow_origem and wf.workflow_brflow:
        if normalize_key(rep.workflow_origem) != normalize_key(wf.workflow_brflow):
            divergencia = "Workflow de origem difere do plano."

    return {
        "protocolo": {
            "run_id": prot.run_id,
            "protocolo": prot.protocolo,
            "protocolo_normalizado": prot.protocolo_normalizado or norm,
            "status_operacional": display_status,
            "status_brflow": prot.status_brflow,
            "workflow_config": prot.workflow_config,
            "canal_destino": prot.canal_destino,
            "data_referencia_d1": prot.data_referencia_d1.isoformat(),
            "data_analise": prot.data_analise.isoformat() if prot.data_analise else None,
            "replicado_em": prot.replicado_em.isoformat() if prot.replicado_em else None,
            "erro_resumido": prot.erro_resumido,
        },
        "workflow": (
            {
                "workflow_config": wf.workflow_config,
                "cliente": wf.cliente,
                "fila": wf.fila,
                "segmento": wf.segmento,
                "status_brflow": wf.status_brflow,
            }
            if wf
            else None
        ),
        "run": (
            {
                "run_id": run.run_id,
                "data_referencia_d1": run.data_referencia_d1.isoformat(),
                "data_execucao": run.data_execucao.isoformat() if run.data_execucao else None,
                "protocolos_total": run.protocolos_total,
                "synced_at": run.synced_at.isoformat(),
            }
            if run
            else None
        ),
        "replicado": (
            {
                "report_date": rep.report_date.isoformat(),
                "protocolo_destino": rep.protocolo_destino,
                "workflow_destino": rep.workflow_destino,
                "workflow_origem": rep.workflow_origem,
                "cliente_destino": rep.cliente_destino,
                "data_cadastro_destino": (
                    rep.data_cadastro_destino.isoformat() if rep.data_cadastro_destino else None
                ),
            }
            if rep
            else None
        ),
        "divergencia": divergencia,
    }
