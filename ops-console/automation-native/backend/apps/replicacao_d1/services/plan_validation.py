"""Persistência imutável, validação e aprovação dos planos D-1."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from apps.replicacao_d1.models import (
    ReplicacaoD1ExecutionEvent,
    ReplicacaoD1FonteLote,
    ReplicacaoD1PlanDeletion,
    ReplicacaoD1PlanReview,
    ReplicacaoD1Protocolo,
    ReplicacaoD1Run,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.normalization import normalize_protocolo


class PlanNotApprovedError(RuntimeError):
    code = "PLAN_NOT_APPROVED"


class PlanImmutableError(RuntimeError):
    code = "PLAN_IMMUTABLE"


class PlanDeletionBlockedError(RuntimeError):
    code = "PLAN_DELETE_BLOCKED"


def deleted_plan_run_ids():
    """Subquery reutilizável com os planos removidos da operação."""
    return ReplicacaoD1PlanDeletion.objects.values_list("run_id", flat=True)


def exclude_deleted_plans(queryset, *, field: str = "run_id"):
    """Oculta planos excluídos sem apagar seus dados operacionais/auditáveis."""
    return queryset.exclude(**{f"{field}__in": deleted_plan_run_ids()})


def is_plan_deleted(run_id: str) -> bool:
    return ReplicacaoD1PlanDeletion.objects.filter(run_id=str(run_id or "").strip()).exists()


_NON_ACTIONABLE_WARNING_MARKERS = (
    "sem registros",
    "csv vazio",
    "ausente(s) no parquet",
    "inaplicável (sem registro",
)

_WARNING_GROUP_LABELS = {
    "cadastro": "Cadastro pendente",
    "metas": "Balanceamento e categorização",
    "capacidade": "Capacidade e redistribuição",
    "outros": "Outros pontos de atenção",
}


def plan_warning_category(warning: str) -> str | None:
    """Classifica somente avisos que exigem alguma ação do usuário."""
    text = str(warning or "").strip().casefold()
    if not text or any(marker in text for marker in _NON_ACTIONABLE_WARNING_MARKERS):
        return None
    if (
        "ausente no cadastro" in text
        or "sem cadastro" in text
        or "pendente no banco" in text
        or ("registrad" in text and "pendente" in text)
    ):
        return "cadastro"
    if "meta mensal" in text or "limite mensal de balanceamento" in text or "segmento/categoria" in text:
        return "metas"
    if "redistribu" in text or "capacidade" in text or "amostra parcial" in text:
        return "capacidade"
    return "outros"


def build_plan_warning_groups(
    warnings: Iterable[str] | None,
    *,
    max_items: int = 20,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for raw in warnings or []:
        warning = str(raw or "").strip()
        category = plan_warning_category(warning)
        if not category:
            continue
        items = grouped.setdefault(category, [])
        if warning not in items:
            items.append(warning)
    result: list[dict[str, Any]] = []
    limit = max(1, int(max_items or 20))
    for key in ("cadastro", "metas", "capacidade", "outros"):
        items = grouped.get(key) or []
        if not items:
            continue
        result.append(
            {
                "key": key,
                "label": _WARNING_GROUP_LABELS[key],
                "count": len(items),
                "items": items[:limit],
                "remaining": max(0, len(items) - limit),
            }
        )
    return result


def _json_value(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _is_modo_qtd_fila(fila: str) -> bool:
    fila_norm = str(fila or "").strip().casefold()
    return fila_norm in {"bio", "redoc"}


def _modo_replicacao_row(row: dict[str, Any]) -> str:
    """Resolve o modo persistido, usando fila somente para payloads legados."""
    modo = str(row.get("modo_replicacao") or "").strip().casefold()
    if modo in {"protocolos", "qtd"}:
        return modo
    return "qtd" if _is_modo_qtd_fila(str(row.get("fila") or "")) else "protocolos"


def _is_modo_qtd_row(row: dict[str, Any]) -> bool:
    return _modo_replicacao_row(row) == "qtd"


def _planned_qty_row(row: dict[str, Any]) -> int:
    return int(row.get("protocolos_planejados") or row.get("amostra_efetiva") or 0)


def _qtd_planned_total(workflow_rows: Iterable[dict[str, Any]]) -> int:
    return sum(
        _planned_qty_row(row)
        for row in workflow_rows
        if _is_modo_qtd_row(row)
    )


def compute_executable_total(*, protocol_count: int, workflow_rows: Iterable[dict[str, Any]]) -> int:
    """Total executável: IDs de ``protocolos`` + quantidades de ``qtd``."""
    return int(protocol_count) + _qtd_planned_total(workflow_rows)


def compute_executable_total_for_run(run: ReplicacaoD1Run) -> int:
    protocol_count = run.protocolos.count()
    workflow_rows = list(
        run.workflows.filter(protocolos_planejados__gt=0).values(
            "fila", "modo_replicacao", "protocolos_planejados", "amostra_efetiva",
        )
    )
    return compute_executable_total(protocol_count=protocol_count, workflow_rows=workflow_rows)


def _repair_run_executable_total(
    run: ReplicacaoD1Run,
    executable_total: int,
    *,
    protocol_count: int,
    qtd_planned_total: int,
) -> None:
    if run.protocolos_total == executable_total:
        return
    run.protocolos_total = executable_total
    summary = dict(run.plan_summary or {})
    summary["protocolos_total"] = executable_total
    summary["protocolos_csv_total"] = protocol_count
    summary["qtd_planejada_total"] = qtd_planned_total
    run.plan_summary = summary
    run.save(update_fields=["protocolos_total", "plan_summary", "synced_at"])


def calculate_plan_hash(
    *,
    run_id: str,
    data_referencia_d1: date,
    source_batch_id: int,
    config_hash: str,
    workflows: Iterable[dict[str, Any]],
    protocolos: Iterable[dict[str, Any]],
) -> str:
    workflow_rows = sorted(
        (_json_value(dict(row)) for row in workflows),
        key=lambda row: str(row.get("workflow_config", "")),
    )
    protocolo_rows = sorted(
        (_json_value(dict(row)) for row in protocolos),
        key=lambda row: (str(row.get("workflow_config", "")), str(row.get("protocolo_normalizado") or row.get("protocolo", ""))),
    )
    payload = {
        "schema_version": 1,
        "run_id": run_id,
        "data_referencia_d1": data_referencia_d1.isoformat(),
        "source_batch_id": source_batch_id,
        "config_hash": config_hash,
        "workflows": workflow_rows,
        "protocolos": protocolo_rows,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _pick(model, row: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key in allowed}


def _ensure_aware_datetime(value: Any) -> datetime | None:
    """Normaliza data_analise para timezone-aware (retroativo/pandas costuma vir naive)."""
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        return None
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _dedupe_protocol_rows(protocolos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Um protocolo por run (constraint run+protocolo); mantém a primeira ocorrência."""
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    avisos: list[str] = []
    for row in protocolos:
        norm = str(row.get("protocolo_normalizado") or normalize_protocolo(row.get("protocolo") or ""))
        if not norm:
            continue
        if norm in seen:
            avisos.append(
                f"Protocolo duplicado omitido na persistência: {row.get('protocolo')} "
                f"({row.get('workflow_config')})"
            )
            continue
        seen.add(norm)
        kept.append(row)
    return kept, avisos


@transaction.atomic
def persist_plan(
    *,
    run_id: str,
    data_referencia_d1: date,
    source_batch_id: int,
    workflows: list[dict[str, Any]],
    protocolos: list[dict[str, Any]],
    config_snapshot_id: int | None = None,
    config_version: int | None = None,
    config_hash: str = "",
    warnings: list[str] | None = None,
    auditores_ativos_brflow: int | None = None,
    auditores_ativos_case: int | None = None,
) -> ReplicacaoD1Run:
    """Publica o plano uma única vez; qualquer mudança exige outro ``run_id``."""
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("run_id obrigatório")
    if ReplicacaoD1PlanDeletion.objects.filter(run_id=rid).exists():
        raise PlanImmutableError("Este run_id pertence a um plano excluído. Gere um novo run_id.")
    source_batch = ReplicacaoD1FonteLote.objects.select_for_update().get(pk=source_batch_id)
    if source_batch.status != ReplicacaoD1FonteLote.STATUS_READY:
        raise ValueError("O lote de fonte D-1 ainda não está pronto.")

    workflows = [dict(row) for row in workflows]
    for row in workflows:
        row["modo_replicacao"] = _modo_replicacao_row(row)

    modos_por_workflow = {
        str(row.get("workflow_config") or "").strip(): _modo_replicacao_row(row)
        for row in workflows
        if str(row.get("workflow_config") or "").strip()
    }
    normalized_protocols: list[dict[str, Any]] = []
    for item in protocolos:
        row = dict(item)
        row["protocolo"] = str(row.get("protocolo") or "").strip()
        row["protocolo_normalizado"] = str(
            row.get("protocolo_normalizado") or normalize_protocolo(row["protocolo"])
        )
        if not row["protocolo_normalizado"] or not str(row.get("workflow_config") or "").strip():
            raise ValueError("Plano contém protocolo ou workflow vazio.")
        row["data_analise"] = _ensure_aware_datetime(row.get("data_analise"))
        if modos_por_workflow.get(str(row["workflow_config"]).strip()) == "qtd":
            continue
        normalized_protocols.append(row)

    deduped_protocols, dedupe_avisos = _dedupe_protocol_rows(normalized_protocols)
    if dedupe_avisos:
        warnings = list(warnings or [])
        warnings.extend(dedupe_avisos)
    normalized_protocols = deduped_protocols
    qtd_planned_total = sum(
        _planned_qty_row(row)
        for row in workflows
        if _is_modo_qtd_row(row)
    )
    if not normalized_protocols and qtd_planned_total <= 0:
        raise ValueError("Plano não possui protocolos ou quantidades qtd para persistir.")

    protocol_workflows = {
        str(row.get("workflow_config") or "").strip()
        for row in normalized_protocols
    }
    persistible_workflows = set(protocol_workflows)
    for row in workflows:
        wf = str(row.get("workflow_config") or "").strip()
        if wf and _is_modo_qtd_row(row) and _planned_qty_row(row) > 0:
            persistible_workflows.add(wf)
    configured_workflows = {
        str(row.get("workflow_config") or "").strip()
        for row in workflows
        if str(row.get("workflow_config") or "").strip()
    }
    missing_workflows = sorted(protocol_workflows - configured_workflows)
    if missing_workflows:
        preview = ", ".join(missing_workflows[:5])
        raise ValueError(f"Plano contém protocolo(s) sem workflow persistível: {preview}")
    workflows = [
        dict(row)
        for row in workflows
        if str(row.get("workflow_config") or "").strip() in persistible_workflows
    ]

    plan_hash = calculate_plan_hash(
        run_id=rid,
        data_referencia_d1=data_referencia_d1,
        source_batch_id=source_batch_id,
        config_hash=config_hash,
        workflows=workflows,
        protocolos=normalized_protocols,
    )
    run, created = ReplicacaoD1Run.objects.select_for_update().get_or_create(
        run_id=rid,
        defaults={
            "data_referencia_d1": data_referencia_d1,
            "status_canonical": ReplicacaoD1Run.STATUS_PLANNED,
            "validation_status": ReplicacaoD1Run.VALIDATION_PENDING,
        },
    )
    if not created and run.plan_hash:
        if run.plan_hash == plan_hash:
            return run
        raise PlanImmutableError("O plano já foi publicado. Gere um novo run_id para alterá-lo.")
    if not created and (run.protocolos.exists() or run.workflows.exists()):
        raise PlanImmutableError("O run já possui dados. Gere um novo run_id para um novo plano.")

    older_pending = ReplicacaoD1Run.objects.select_for_update().filter(
        data_referencia_d1=data_referencia_d1,
        validation_status=ReplicacaoD1Run.VALIDATION_PENDING,
    ).exclude(pk=run.pk)
    older_pending.update(validation_status=ReplicacaoD1Run.VALIDATION_SUPERSEDED)

    workflow_fields = {
        "workflow_config", "workflow_d1", "workflow_brflow", "canal_destino", "cliente",
        "segmento", "categoria", "fila", "modo_replicacao", "amostra_diaria", "amostra_solicitada",
        "amostra_efetiva", "disponivel_d1", "status_amostra", "faixa_horaria",
        "excluidos_historico", "redistribuidos", "protocolos_manuais",
        "protocolos_automaticos", "protocolos_retroativos", "warnings",
    }
    protocol_fields = {
        "protocolo", "protocolo_normalizado", "workflow_config", "workflow_d1",
        "data_analise", "hora", "canal_destino", "matricula_tipo", "selection_reason",
        "source_record_id",
    }
    ReplicacaoD1WorkflowDia.objects.bulk_create(
        [
            ReplicacaoD1WorkflowDia(
                run=run,
                data_referencia_d1=data_referencia_d1,
                protocolos_planejados=int(row.get("protocolos_planejados") or row.get("amostra_efetiva") or 0),
                **_pick(ReplicacaoD1WorkflowDia, row, workflow_fields),
            )
            for row in workflows
        ],
        batch_size=500,
    )
    ReplicacaoD1Protocolo.objects.bulk_create(
        [
            ReplicacaoD1Protocolo(
                run=run,
                data_referencia_d1=data_referencia_d1,
                **_pick(ReplicacaoD1Protocolo, row, protocol_fields),
            )
            for row in normalized_protocols
        ],
        batch_size=2000,
    )

    summary = {
        "protocolos_total": len(normalized_protocols) + qtd_planned_total,
        "protocolos_csv_total": len(normalized_protocols),
        "qtd_planejada_total": qtd_planned_total,
        "workflows_total": len(workflows),
        "disponivel_d1": sum(int(row.get("disponivel_d1") or 0) for row in workflows),
        "excluidos_historico": sum(int(row.get("excluidos_historico") or 0) for row in workflows),
        "redistribuidos": sum(int(row.get("redistribuidos") or 0) for row in workflows),
        "protocolos_manuais": sum(int(row.get("protocolos_manuais") or 0) for row in workflows),
        "protocolos_automaticos": sum(int(row.get("protocolos_automaticos") or 0) for row in workflows),
        "protocolos_retroativos": sum(int(row.get("protocolos_retroativos") or 0) for row in workflows),
    }
    run.status_canonical = ReplicacaoD1Run.STATUS_PLANNED
    run.validation_status = ReplicacaoD1Run.VALIDATION_PENDING
    run.source_batch = source_batch
    run.config_snapshot_id = config_snapshot_id
    run.config_version = config_version
    run.config_hash = config_hash
    run.plan_hash = plan_hash
    run.plan_revision = 1
    run.plan_summary = summary
    run.plan_warnings = list(warnings or [])
    run.protocolos_total = summary["protocolos_total"]
    run.workflows_total = summary["workflows_total"]
    run.auditores_ativos_brflow = auditores_ativos_brflow
    run.auditores_ativos_case = auditores_ativos_case
    run.parquet_referencia = ""
    run.save()
    ReplicacaoD1ExecutionEvent.objects.create(
        run=run,
        phase="planning",
        status="awaiting_approval",
        message="Plano persistido no banco e aguardando validação.",
        payload={"plan_hash": plan_hash, "plan_revision": 1, **summary},
    )
    return run


def _review_payload(review: ReplicacaoD1PlanReview) -> dict[str, Any]:
    return {
        "id": review.pk,
        "action": review.action,
        "user": review.user.get_username() if review.user_id else "",
        "reason": review.reason,
        "plan_hash": review.plan_hash,
        "plan_revision": review.plan_revision,
        "created_at": review.created_at,
    }


def plan_deletion_block_reason(run: ReplicacaoD1Run) -> str:
    """Explica por que um plano não pode ser removido."""
    if is_plan_deleted(run.run_id):
        return "Este plano já foi excluído da operação."
    if not run.plan_hash:
        return "O plano ainda não foi publicado integralmente."
    if (
        run.status_canonical == ReplicacaoD1Run.STATUS_RUNNING
        and run.validation_status not in {
            ReplicacaoD1Run.VALIDATION_REJECTED,
            ReplicacaoD1Run.VALIDATION_SUPERSEDED,
        }
    ):
        return "A execução deste plano ainda está ativa. Finalize ou interrompa a execução antes de excluir."
    if (
        run.status_canonical == ReplicacaoD1Run.STATUS_PLANNED
        and run.validation_status == ReplicacaoD1Run.VALIDATION_APPROVED
    ):
        return "O plano está aprovado e disponível para execução. Rejeite ou substitua o plano antes de excluir."
    return ""


def can_delete_plan(run: ReplicacaoD1Run) -> bool:
    """Exclusão física é permitida apenas antes de qualquer início operacional."""
    return not plan_deletion_block_reason(run)


def build_plan_validation(run_id: str) -> dict[str, Any]:
    run = (
        exclude_deleted_plans(ReplicacaoD1Run.objects.select_related("source_batch", "reviewed_by"))
        .prefetch_related("reviews__user")
        .get(run_id=run_id)
    )
    workflow_rows = list(
        run.workflows.filter(protocolos_planejados__gt=0).order_by("workflow_config").values(
            "workflow_config", "workflow_d1", "workflow_brflow", "cliente", "segmento",
            "categoria", "fila", "modo_replicacao", "canal_destino", "amostra_diaria", "amostra_solicitada",
            "amostra_efetiva", "protocolos_planejados", "disponivel_d1", "excluidos_historico", "redistribuidos",
            "protocolos_manuais", "protocolos_automaticos", "protocolos_retroativos",
            "status_amostra", "warnings",
        )
    )
    actual_by_workflow = {
        row["workflow_config"]: row["total"]
        for row in run.protocolos.values("workflow_config").annotate(total=Count("id"))
    }
    for row in workflow_rows:
        if _is_modo_qtd_row(row):
            continue
        row["protocolos_planejados"] = actual_by_workflow.get(row["workflow_config"], 0)

    protocol_count = run.protocolos.count()
    qtd_planned_total = _qtd_planned_total(workflow_rows)
    executable_total = compute_executable_total(
        protocol_count=protocol_count,
        workflow_rows=workflow_rows,
    )
    _repair_run_executable_total(
        run,
        executable_total,
        protocol_count=protocol_count,
        qtd_planned_total=qtd_planned_total,
    )
    retro_selected = sum(int(row.get("protocolos_retroativos") or 0) for row in workflow_rows)
    retro_warning = ""
    if retro_selected == 0 and run.config_snapshot_id:
        from apps.replicacao_d1.config_models import ReplicacaoD1ConfigSnapshot

        snap = ReplicacaoD1ConfigSnapshot.objects.filter(pk=run.config_snapshot_id).first()
        retro_cfg = ((snap.snapshot_json or {}).get("persistent") or {}).get("retroativo") if snap else {}
        if retro_cfg.get("retroativo_ativo") and retro_cfg.get("workflows"):
            retro_warning = (
                "Retroativo configurado, mas nenhum protocolo retroativo foi selecionado neste plano."
            )

    workflow_count = len(workflow_rows)
    qtd_workflow_count = sum(
        1
        for row in workflow_rows
        if _is_modo_qtd_row(row) and int(row.get("protocolos_planejados") or 0) > 0
    )
    issues: list[str] = []
    if run.protocolos_total != executable_total:
        issues.append("Total do run diverge do total de protocolos persistidos.")
    protocol_workflow_count = run.protocolos.values("workflow_config").distinct().count()
    expected_protocol_workflows = workflow_count - qtd_workflow_count
    if protocol_workflow_count != expected_protocol_workflows:
        issues.append("Total de workflows diverge dos workflows presentes nos protocolos.")
    duplicate_count = (
        run.protocolos.values("protocolo_normalizado").annotate(total=Count("id")).filter(total__gt=1).count()
    )
    if duplicate_count:
        issues.append(f"Há {duplicate_count} protocolo(s) normalizado(s) duplicado(s) no plano.")

    by_hour = list(run.protocolos.values("hora").annotate(total=Count("id")).order_by("hora"))
    by_type = list(run.protocolos.values("matricula_tipo").annotate(total=Count("id")).order_by("matricula_tipo"))
    source = run.source_batch
    deletion_block_reason = plan_deletion_block_reason(run)
    return {
        "run": {
            "run_id": run.run_id,
            "data_referencia_d1": run.data_referencia_d1,
            "status": run.status_canonical,
            "validation_status": run.validation_status,
            "plan_hash": run.plan_hash,
            "plan_revision": run.plan_revision,
            "config_version": run.config_version,
            "config_hash": run.config_hash,
            "protocolos_total": run.protocolos_total,
            "workflows_total": workflow_count,
            "plan_summary": run.plan_summary,
            "warnings_count": len(run.plan_warnings or []),
            "source_batch_id": run.source_batch_id,
            "created_at": run.created_at,
            "reviewed_at": run.reviewed_at,
            "reviewed_by": run.reviewed_by.get_username() if run.reviewed_by_id else "",
            "review_reason": run.review_reason,
        },
        "source": None if source is None else {
            "id": source.pk,
            "report_date": source.report_date,
            "status": source.status,
            "schema_version": source.schema_version,
            "content_hash": source.content_hash,
            "rows_read": source.rows_read,
            "rows_valid": source.rows_valid,
            "rows_duplicate": source.rows_duplicate,
            "rows_rejected": source.rows_rejected,
            "finished_at": source.finished_at,
        },
        "kpis": {
            **(run.plan_summary or {}),
            "protocolos_csv_total": protocol_count,
            "protocolos_total": executable_total,
            "qtd_planejada_total": qtd_planned_total,
            "workflows_total": workflow_count,
            "duplicados_no_plano": duplicate_count,
        },
        "workflows": workflow_rows,
        "distribuicao_hora": by_hour,
        "distribuicao_matricula": by_type,
        "warnings": [
            warning
            for warning in ([retro_warning] if retro_warning else []) + list(run.plan_warnings or [])
            if plan_warning_category(warning)
        ],
        "warning_groups": build_plan_warning_groups(
            ([retro_warning] if retro_warning else []) + list(run.plan_warnings or [])
        ),
        "validation_issues": issues,
        "can_approve": run.validation_status == ReplicacaoD1Run.VALIDATION_PENDING and not issues,
        "can_delete": not deletion_block_reason,
        "delete_block_reason": deletion_block_reason,
        "reviews": [_review_payload(item) for item in run.reviews.all()],
    }


@transaction.atomic
def review_plan(
    run_id: str,
    *,
    action: str,
    user,
    expected_plan_hash: str,
    expected_revision: int,
    reason: str = "",
) -> ReplicacaoD1Run:
    run = ReplicacaoD1Run.objects.select_for_update().get(run_id=run_id)
    if is_plan_deleted(run.run_id):
        raise PlanImmutableError("Este plano foi excluído da operação e não pode mais ser revisado.")
    if run.validation_status != ReplicacaoD1Run.VALIDATION_PENDING:
        raise PlanImmutableError("Este plano já foi revisado ou substituído.")
    if not run.plan_hash or run.plan_hash != str(expected_plan_hash or ""):
        raise PlanImmutableError("O plano mudou desde a abertura da tela. Atualize os dados.")
    if run.plan_revision != int(expected_revision):
        raise PlanImmutableError("A revisão do plano mudou. Atualize os dados.")
    if action not in {ReplicacaoD1PlanReview.ACTION_APPROVE, ReplicacaoD1PlanReview.ACTION_REJECT}:
        raise ValueError("Ação de revisão inválida.")
    clean_reason = str(reason or "").strip()
    if action == ReplicacaoD1PlanReview.ACTION_REJECT and not clean_reason:
        raise ValueError("Informe o motivo da rejeição.")
    if action == ReplicacaoD1PlanReview.ACTION_APPROVE:
        validation = build_plan_validation(run_id)
        if validation["validation_issues"]:
            raise ValueError("O plano possui divergências internas e não pode ser aprovado.")
        run.validation_status = ReplicacaoD1Run.VALIDATION_APPROVED
        event_status = "approved"
    else:
        run.validation_status = ReplicacaoD1Run.VALIDATION_REJECTED
        event_status = "rejected"
    run.reviewed_by = user
    run.reviewed_at = timezone.now()
    run.review_reason = clean_reason
    run.save(update_fields=["validation_status", "reviewed_by", "reviewed_at", "review_reason", "synced_at"])
    ReplicacaoD1PlanReview.objects.create(
        run=run,
        action=action,
        user=user,
        reason=clean_reason,
        plan_hash=run.plan_hash,
        plan_revision=run.plan_revision,
    )
    ReplicacaoD1ExecutionEvent.objects.create(
        run=run,
        phase="validation",
        status=event_status,
        message=clean_reason,
        payload={"plan_hash": run.plan_hash, "plan_revision": run.plan_revision},
    )
    return run


def ensure_plan_approved(run_id: str) -> ReplicacaoD1Run:
    rid = str(run_id or "").strip()
    if is_plan_deleted(rid):
        raise PlanNotApprovedError("O plano foi excluído da operação e não pode ser executado.")
    try:
        run = ReplicacaoD1Run.objects.get(run_id=rid)
    except ReplicacaoD1Run.DoesNotExist as exc:
        raise PlanNotApprovedError("Plano não encontrado no banco.") from exc
    if run.validation_status != ReplicacaoD1Run.VALIDATION_APPROVED:
        raise PlanNotApprovedError(
            f"O plano {run.run_id} está com validação '{run.validation_status}' e não pode ser executado."
        )
    if not run.plan_hash:
        raise PlanNotApprovedError("O plano não possui hash de integridade.")
    return run


@transaction.atomic
def delete_plan(
    run_id: str,
    *,
    user,
    expected_plan_hash: str,
    expected_revision: int,
    confirmation_run_id: str,
    reason: str,
) -> ReplicacaoD1PlanDeletion:
    """Remove um plano não executado e preserva um tombstone de auditoria."""
    rid = str(run_id or "").strip()
    clean_reason = str(reason or "").strip()
    if not clean_reason:
        raise ValueError("Informe o motivo da exclusão.")
    if str(confirmation_run_id or "").strip() != rid:
        raise ValueError("Digite o Run ID completo para confirmar a exclusão.")

    run = ReplicacaoD1Run.objects.select_for_update().get(run_id=rid)
    if is_plan_deleted(rid):
        raise PlanDeletionBlockedError("Este plano já foi excluído da operação.")
    if not run.plan_hash or run.plan_hash != str(expected_plan_hash or ""):
        raise PlanDeletionBlockedError("O plano mudou desde a abertura da tela. Atualize os dados.")
    if run.plan_revision != int(expected_revision):
        raise PlanDeletionBlockedError("A revisão do plano mudou. Atualize os dados.")
    if not can_delete_plan(run):
        raise PlanDeletionBlockedError(
            "Somente planos não aprovados e que nunca iniciaram execução podem ser apagados."
        )

    reviews = [
        {
            "action": row.action,
            "user_id": row.user_id,
            "reason": row.reason,
            "plan_hash": row.plan_hash,
            "plan_revision": row.plan_revision,
            "created_at": row.created_at.isoformat(),
        }
        for row in run.reviews.order_by("created_at", "id")
    ]
    events = [
        {
            "phase": row.phase,
            "status": row.status,
            "message": row.message,
            "created_at": row.created_at.isoformat(),
        }
        for row in run.events.order_by("created_at", "id")
    ]
    deletion = ReplicacaoD1PlanDeletion.objects.create(
        run_id=run.run_id,
        data_referencia_d1=run.data_referencia_d1,
        source_batch=run.source_batch,
        status_canonical=run.status_canonical,
        validation_status=run.validation_status,
        plan_hash=run.plan_hash,
        plan_revision=run.plan_revision,
        config_hash=run.config_hash,
        protocolos_total=compute_executable_total_for_run(run),
        workflows_total=run.workflows.count(),
        deletion_reason=clean_reason,
        deleted_by=user,
        audit_snapshot={
            "deletion_mode": "archived",
            "operational_data_preserved": True,
            "plan_summary": run.plan_summary or {},
            "plan_warnings": run.plan_warnings or [],
            "reviews": reviews,
            "events": events,
        },
    )
    return deletion


@transaction.atomic
def adopt_legacy_run(run_id: str, *, reason: str = "Migração histórica de artefato") -> ReplicacaoD1Run:
    """Assina um run legado para consulta, mantendo-o bloqueado para nova execução."""
    run = ReplicacaoD1Run.objects.select_for_update().get(run_id=run_id)
    if run.plan_hash:
        return run
    workflows = list(
        run.workflows.order_by("workflow_config").values(
            "workflow_config", "workflow_d1", "workflow_brflow", "cliente", "segmento",
            "categoria", "fila", "amostra_diaria", "amostra_solicitada", "amostra_efetiva",
            "disponivel_d1", "status_amostra",
        )
    )
    protocolos = list(
        run.protocolos.order_by("workflow_config", "protocolo").values(
            "protocolo", "protocolo_normalizado", "workflow_config", "workflow_d1",
            "data_analise", "hora", "canal_destino",
        )
    )
    payload = {
        "schema_version": "legacy-1",
        "run_id": run.run_id,
        "data_referencia_d1": run.data_referencia_d1,
        "workflows": workflows,
        "protocolos": protocolos,
    }
    raw = json.dumps(_json_value(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    run.plan_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    run.plan_revision = 1
    run.validation_status = ReplicacaoD1Run.VALIDATION_SUPERSEDED
    run.plan_summary = {
        "protocolos_total": len(protocolos),
        "workflows_total": len(workflows),
        "legacy": 1,
    }
    run.plan_warnings = ["Plano migrado de arquivo histórico; somente consulta."]
    run.review_reason = reason[:500]
    run.protocolos_total = len(protocolos)
    run.workflows_total = len(workflows)
    run.save(update_fields=[
        "plan_hash", "plan_revision", "validation_status", "plan_summary", "plan_warnings",
        "review_reason", "protocolos_total", "workflows_total", "synced_at",
    ])
    ReplicacaoD1ExecutionEvent.objects.create(
        run=run,
        phase="migration",
        status="legacy_imported",
        message=reason[:500],
        payload={"plan_hash": run.plan_hash, "protocolos_total": len(protocolos), "workflows_total": len(workflows)},
    )
    return run


def append_execution_event(
    run_id: str,
    *,
    phase: str,
    status: str,
    message: str = "",
    workflow_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> ReplicacaoD1ExecutionEvent:
    return ReplicacaoD1ExecutionEvent.objects.create(
        run_id=ReplicacaoD1Run.objects.only("id").get(run_id=run_id).pk,
        workflow_id=workflow_id,
        phase=str(phase)[:32],
        status=str(status)[:32],
        message=str(message or "")[:500],
        payload=payload or {},
    )
