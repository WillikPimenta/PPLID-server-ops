# -*- coding: utf-8 -*-
"""Ciclo de vida operacional do run D-1 (planejamento → execução → fechamento)."""
from __future__ import annotations

from datetime import date, datetime

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.replicacao_d1.config_models import ReplicacaoD1ConfigSnapshot
from apps.replicacao_d1.models import (
    ReplicacaoD1PlanDeletion,
    ReplicacaoD1Run,
    ReplicacaoD1WorkflowDia,
)
from apps.replicacao_d1.normalization import (
    RESULTADO_CANCELADO,
    RESULTADO_FALHOU,
    RESULTADO_INATIVO,
    RESULTADO_NAO_SALVO,
    RESULTADO_PULADO,
    RESULTADO_SALVO,
    RESULTADO_SEM_ALTERACAO,
    STATUS_FALHOU,
)
from apps.replicacao_d1.services.plan_validation import compute_executable_total_for_run

_STATUS_RANK = {
    ReplicacaoD1Run.STATUS_LEGACY: 0,
    ReplicacaoD1Run.STATUS_PLANNED: 1,
    ReplicacaoD1Run.STATUS_RUNNING: 2,
    ReplicacaoD1Run.STATUS_PARTIAL: 3,
    ReplicacaoD1Run.STATUS_FAILED: 3,
    ReplicacaoD1Run.STATUS_COMPLETED: 4,
    ReplicacaoD1Run.STATUS_CANCELLED: 0,
}

_BRFLOW_OK = frozenset({"SALVO_OK", "UPLOAD_OK", "SEM_ALTERACAO"})


def _parse_data_ref(value: str | date | None, fallback: date | None = None) -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    if text:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    if fallback:
        return fallback
    return timezone.now().date()


def _should_apply_status(
    current: str,
    new: str,
    *,
    salvo_ok: int,
    prev_salvo_ok: int,
) -> bool:
    if current == new:
        return True
    if current == ReplicacaoD1Run.STATUS_COMPLETED:
        if new == ReplicacaoD1Run.STATUS_PARTIAL and salvo_ok < prev_salvo_ok:
            return True
        if new in (ReplicacaoD1Run.STATUS_PLANNED, ReplicacaoD1Run.STATUS_PARTIAL):
            return False
    if _STATUS_RANK.get(new, 0) < _STATUS_RANK.get(current, 0):
        return False
    return True


def _derive_run_status(
    *,
    workflows_total: int,
    salvo_ok: int,
    has_failures: bool,
    pulados: int = 0,
    inativos: int = 0,
) -> str:
    if workflows_total <= 0:
        return ReplicacaoD1Run.STATUS_PLANNED
    if has_failures and salvo_ok == 0:
        return ReplicacaoD1Run.STATUS_FAILED
    if has_failures:
        return ReplicacaoD1Run.STATUS_PARTIAL
    if salvo_ok + pulados + inativos >= workflows_total:
        return ReplicacaoD1Run.STATUS_COMPLETED
    if salvo_ok > 0:
        return ReplicacaoD1Run.STATUS_PARTIAL
    return ReplicacaoD1Run.STATUS_PLANNED


@transaction.atomic
def ensure_planned_run(
    run_id: str,
    *,
    data_referencia_d1: date | str | None = None,
    config_snapshot: ReplicacaoD1ConfigSnapshot | None = None,
    config_version: int | None = None,
    config_hash: str = "",
    parquet_referencia: str = "",
) -> ReplicacaoD1Run:
    """Cria/atualiza run em status planned antes do processamento."""
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("run_id obrigatório para ensure_planned_run")
    if ReplicacaoD1PlanDeletion.objects.filter(run_id=rid).exists():
        raise ValueError("Este run_id pertence a um plano excluído; gere um novo run_id.")

    data_ref = _parse_data_ref(data_referencia_d1)
    defaults: dict = {
        "data_referencia_d1": data_ref,
        "status_canonical": ReplicacaoD1Run.STATUS_PLANNED,
    }
    if config_snapshot is not None:
        defaults["config_snapshot"] = config_snapshot
    if config_version is not None:
        defaults["config_version"] = config_version
    if config_hash:
        defaults["config_hash"] = config_hash
    if parquet_referencia:
        defaults["parquet_referencia"] = parquet_referencia[:255]

    run, created = ReplicacaoD1Run.objects.select_for_update().get_or_create(
        run_id=rid,
        defaults=defaults,
    )
    if created:
        return run

    update_fields: list[str] = []
    if run.status_canonical in (
        ReplicacaoD1Run.STATUS_LEGACY,
        ReplicacaoD1Run.STATUS_PLANNED,
    ):
        run.status_canonical = ReplicacaoD1Run.STATUS_PLANNED
        update_fields.append("status_canonical")
    if config_snapshot is not None and run.config_snapshot_id != config_snapshot.pk:
        run.config_snapshot = config_snapshot
        update_fields.append("config_snapshot")
    if config_version is not None and run.config_version != config_version:
        run.config_version = config_version
        update_fields.append("config_version")
    if config_hash and run.config_hash != config_hash:
        run.config_hash = config_hash
        update_fields.append("config_hash")
    if parquet_referencia and run.parquet_referencia != parquet_referencia[:255]:
        run.parquet_referencia = parquet_referencia[:255]
        update_fields.append("parquet_referencia")
    if run.data_referencia_d1 != data_ref:
        run.data_referencia_d1 = data_ref
        update_fields.append("data_referencia_d1")
    if update_fields:
        run.save(update_fields=update_fields)
    return run


@transaction.atomic
def mark_run_started(run_id: str, *, attempt_number: int | None = None) -> None:
    rid = str(run_id or "").strip()
    if not rid:
        return
    try:
        run = ReplicacaoD1Run.objects.select_for_update().get(run_id=rid)
    except ReplicacaoD1Run.DoesNotExist:
        return
    now = timezone.now()
    fields = ["status_canonical", "synced_at"]
    run.status_canonical = ReplicacaoD1Run.STATUS_RUNNING
    if not run.started_at:
        run.started_at = now
        fields.append("started_at")
    if attempt_number is not None and run.attempt_number != attempt_number:
        run.attempt_number = attempt_number
        fields.append("attempt_number")
    run.save(update_fields=fields)


@transaction.atomic
def mark_run_failed(run_id: str, *, error_code: str = "RUN_FAILED", error_summary: str = "") -> None:
    """Marca falha de planejamento/execução sem apagar o histórico do run."""
    rid = str(run_id or "").strip()
    if not rid:
        return
    run = ReplicacaoD1Run.objects.select_for_update().filter(run_id=rid).first()
    if not run:
        return
    now = timezone.now()
    run.status_canonical = ReplicacaoD1Run.STATUS_FAILED
    run.erro_codigo = str(error_code or "RUN_FAILED")[:64]
    run.erro_resumo = str(error_summary or "Falha operacional")[:255]
    run.finished_at = run.finished_at or now
    if run.started_at:
        run.duration_seconds = round((run.finished_at - run.started_at).total_seconds(), 2)
    run.save(update_fields=["status_canonical", "erro_codigo", "erro_resumo", "finished_at", "duration_seconds", "synced_at"])


@transaction.atomic
def update_run_status(
    run_id: str,
    status: str,
    *,
    workflows_salvo_ok: int | None = None,
    workflows_total: int | None = None,
    protocolos_total: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    allow_regress: bool = False,
) -> None:
    """Atualiza status canônico do run após sync ou fechamento do bot."""
    rid = str(run_id or "").strip()
    if not rid:
        return
    try:
        run = ReplicacaoD1Run.objects.select_for_update().get(run_id=rid)
    except ReplicacaoD1Run.DoesNotExist:
        return

    prev_salvo = int(run.workflows_salvo_ok or 0)
    new_salvo = prev_salvo if workflows_salvo_ok is None else workflows_salvo_ok
    if not allow_regress and not _should_apply_status(
        run.status_canonical,
        status,
        salvo_ok=new_salvo,
        prev_salvo_ok=prev_salvo,
    ):
        status = run.status_canonical

    run.status_canonical = status
    fields = ["status_canonical", "synced_at"]
    if workflows_salvo_ok is not None:
        run.workflows_salvo_ok = workflows_salvo_ok
        fields.append("workflows_salvo_ok")
    if workflows_total is not None:
        run.workflows_total = workflows_total
        fields.append("workflows_total")
    if protocolos_total is not None:
        run.protocolos_total = protocolos_total
        fields.append("protocolos_total")
    if started_at is not None:
        if timezone.is_naive(started_at):
            started_at = timezone.make_aware(started_at, timezone.get_current_timezone())
        run.started_at = started_at
        fields.append("started_at")
    if finished_at is not None:
        if timezone.is_naive(finished_at):
            finished_at = timezone.make_aware(finished_at, timezone.get_current_timezone())
        run.finished_at = finished_at
        fields.append("finished_at")
    if run.started_at and run.finished_at:
        run.duration_seconds = round((run.finished_at - run.started_at).total_seconds(), 2)
        fields.append("duration_seconds")
    if status == ReplicacaoD1Run.STATUS_COMPLETED:
        run.erro_codigo = ""
        run.erro_resumo = ""
        fields.extend(["erro_codigo", "erro_resumo"])
    run.save(update_fields=fields)


@transaction.atomic
def close_run(
    run_id: str,
    *,
    ingestion_id: int | None = None,
    data_execucao: datetime | None = None,
    allow_regress: bool = False,
) -> ReplicacaoD1Run:
    """Fecha run agregando totais dos filhos e aplicando status canônico."""
    rid = str(run_id or "").strip()
    if not rid:
        raise ValueError("run_id obrigatório para close_run")

    run = ReplicacaoD1Run.objects.select_for_update().get(run_id=rid)
    wf_stats = (
        ReplicacaoD1WorkflowDia.objects.filter(run_id=rid)
        .aggregate(
            total=Count("id"),
            salvo_ok=Count(
                "id",
                filter=Q(resultado__in=[RESULTADO_SALVO, RESULTADO_SEM_ALTERACAO])
                | Q(status_brflow__in=list(_BRFLOW_OK)),
            ),
            falhou=Count(
                "id",
                filter=Q(resultado__in=[RESULTADO_NAO_SALVO, RESULTADO_FALHOU])
                | Q(status_operacional=STATUS_FALHOU),
            ),
            pulado=Count(
                "id",
                filter=Q(resultado__in=[RESULTADO_PULADO, RESULTADO_CANCELADO])
                | Q(status_brflow__in=["PULADO", "CANCELADO"]),
            ),
            inativo=Count(
                "id",
                filter=Q(resultado=RESULTADO_INATIVO) | Q(status_brflow="INATIVO"),
            ),
        )
    )
    workflows_total = int(wf_stats.get("total") or 0)
    salvo_ok = int(wf_stats.get("salvo_ok") or 0)
    has_failures = int(wf_stats.get("falhou") or 0) > 0
    pulados = int(wf_stats.get("pulado") or 0)
    inativos = int(wf_stats.get("inativo") or 0)
    protocolos_total = compute_executable_total_for_run(run)

    status = _derive_run_status(
        workflows_total=workflows_total,
        salvo_ok=salvo_ok,
        has_failures=has_failures,
        pulados=pulados,
        inativos=inativos,
    )
    update_run_status(
        rid,
        status,
        workflows_salvo_ok=salvo_ok,
        workflows_total=workflows_total,
        protocolos_total=protocolos_total,
        allow_regress=allow_regress,
    )

    run.refresh_from_db()
    now = timezone.now()
    update_fields: list[str] = []
    if data_execucao and not run.data_execucao:
        run.data_execucao = data_execucao
        update_fields.append("data_execucao")
    if ingestion_id and run.ingestion_id != ingestion_id:
        run.ingestion_id = ingestion_id
        update_fields.append("ingestion")
    if status in (
        ReplicacaoD1Run.STATUS_COMPLETED,
        ReplicacaoD1Run.STATUS_PARTIAL,
        ReplicacaoD1Run.STATUS_FAILED,
    ):
        if not run.finished_at:
            run.finished_at = data_execucao or now
            update_fields.append("finished_at")
        if run.started_at and run.finished_at:
            run.duration_seconds = round((run.finished_at - run.started_at).total_seconds(), 2)
            update_fields.append("duration_seconds")
    if has_failures and status == ReplicacaoD1Run.STATUS_PARTIAL:
        run.erro_codigo = "RUN_PARTIAL"
        run.erro_resumo = f"{workflows_total - salvo_ok} workflow(s) com falha ou pendência"
        update_fields.extend(["erro_codigo", "erro_resumo"])
    if update_fields:
        run.save(update_fields=update_fields)
    return run
