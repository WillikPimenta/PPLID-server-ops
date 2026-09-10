# -*- coding: utf-8 -*-
"""Cancelamento cooperativo da carga retroativa (pedido + rollback seguro)."""
from __future__ import annotations

import logging
import uuid
from datetime import date
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.qualidade_operacional.models import QualidadeImportBatch
from apps.qualidade_operacional.services.filter_catalog import refresh_filter_catalog
from apps.qualidade_operacional.services.monthly_import import (
    MonthlyImportError,
    _bulk_from_iter,
    _config,
    _month_bounds,
    _restore_objects,
)

logger = logging.getLogger(__name__)

HEARTBEAT_ALIVE_SECONDS = 120


class CancelAccepted(Exception):
    """Fluxo esperado: cancelamento detectado pelo worker."""


class CancelConflict(MonthlyImportError):
    """Lote em estado não cancelável."""


def new_worker_token() -> str:
    return uuid.uuid4().hex


def worker_alive(batch: QualidadeImportBatch, *, max_age_seconds: int = HEARTBEAT_ALIVE_SECONDS) -> bool:
    if batch.status not in QualidadeImportBatch.ACTIVE_WORKER_STATUSES:
        return False
    hb = batch.heartbeat_at
    if hb is None:
        return False
    return (timezone.now() - hb).total_seconds() <= max_age_seconds


def completed_months_needing_rollback(batch: QualidadeImportBatch) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for month in list(batch.month_plan or []):
        if month.get("status") != "completed":
            continue
        if not month.get("backup_path"):
            continue
        out.append(dict(month))
    return out


def batch_flags(batch: QualidadeImportBatch) -> dict[str, bool]:
    status = batch.status
    completed = completed_months_needing_rollback(batch)
    cancel_pending = status in QualidadeImportBatch.CANCEL_PENDING_STATUSES
    is_terminal = status in QualidadeImportBatch.TERMINAL_STATUSES
    can_cancel = (
        batch.import_mode == QualidadeImportBatch.MODE_RETROACTIVE
        and status in QualidadeImportBatch.CANCELABLE_STATUSES
    )
    can_resume = (
        batch.import_mode == QualidadeImportBatch.MODE_RETROACTIVE
        and not batch.upload_complete
        and status
        in {
            QualidadeImportBatch.STATUS_UPLOADING,
            QualidadeImportBatch.STATUS_FAILED,
        }
        and status != QualidadeImportBatch.STATUS_CANCELLED
    )
    can_restore = (
        status
        in {
            QualidadeImportBatch.STATUS_COMPLETED,
            QualidadeImportBatch.STATUS_FAILED,
            QualidadeImportBatch.STATUS_ROLLBACK_FAILED,
        }
        and bool(completed or (batch.backup_path or "").strip())
    )
    if status == QualidadeImportBatch.STATUS_CANCELLED and completed:
        # Cancel com rollback incompleto não deveria ocorrer; se sobrar backup, permitir restore.
        can_restore = True
    rollback_available = bool(completed) and status in {
        QualidadeImportBatch.STATUS_FAILED,
        QualidadeImportBatch.STATUS_ROLLBACK_FAILED,
        QualidadeImportBatch.STATUS_COMPLETED,
        QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
        QualidadeImportBatch.STATUS_CANCELLING,
    }
    return {
        "can_cancel": can_cancel,
        "cancel_pending": cancel_pending,
        "can_resume": can_resume,
        "can_restore": can_restore and status != QualidadeImportBatch.STATUS_RESTORING,
        "rollback_available": rollback_available,
        "worker_alive": worker_alive(batch),
        "is_terminal": is_terminal,
        "can_confirm": status == QualidadeImportBatch.STATUS_VALIDATED,
    }


def conditional_batch_update(
    batch_id,
    *,
    expected_statuses: set[str] | frozenset[str] | None = None,
    require_token: str | None = None,
    require_progress_gte: int | None = None,
    **fields,
) -> int:
    """Update condicional: impede worker antigo de sobrescrever estado final."""
    qs = QualidadeImportBatch.objects.filter(pk=batch_id)
    if expected_statuses is not None:
        qs = qs.filter(status__in=expected_statuses)
    if require_token is not None:
        qs = qs.filter(worker_token=require_token)
    if require_progress_gte is not None and "progress_percent" in fields:
        new_pct = int(fields["progress_percent"])
        qs = qs.filter(progress_percent__lte=new_pct)
    return qs.update(**fields)


def is_cancel_pending(batch_id) -> bool:
    status = (
        QualidadeImportBatch.objects.filter(pk=batch_id)
        .values_list("status", flat=True)
        .first()
    )
    return status in QualidadeImportBatch.CANCEL_PENDING_STATUSES or status == (
        QualidadeImportBatch.STATUS_CANCELLED
    )


def raise_if_cancel_requested(batch_id) -> None:
    status = (
        QualidadeImportBatch.objects.filter(pk=batch_id)
        .values_list("status", flat=True)
        .first()
    )
    if status in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
        raise CancelAccepted(f"Cancelamento pendente ({status})")
    if status == QualidadeImportBatch.STATUS_CANCELLED:
        raise CancelAccepted("Lote já cancelado")
    # Fencing: outro processo marcou terminal — interromper sem tratar como cancel
    if status in {
        QualidadeImportBatch.STATUS_FAILED,
        QualidadeImportBatch.STATUS_ROLLBACK_FAILED,
        QualidadeImportBatch.STATUS_COMPLETED,
        QualidadeImportBatch.STATUS_RESTORED,
    }:
        raise CancelAccepted(f"Lote em estado terminal ({status})")


def request_cancel_qualidade_import(
    batch_id,
    user=None,
    *,
    reason: str = "",
) -> tuple[QualidadeImportBatch, str]:
    """
    Solicita cancelamento seguro.

    Retorna (batch, outcome) onde outcome ∈:
      - accepted (202): pedido registrado / imediato em uploading|validated
      - already_cancelled (200): já cancelado
      - pending (200): já em cancel_requested|cancelling
    """
    try:
        batch = QualidadeImportBatch.objects.select_related("uploaded_by", "cancelled_by").get(
            pk=batch_id
        )
    except QualidadeImportBatch.DoesNotExist as exc:
        raise MonthlyImportError("Lote não encontrado.") from exc

    if batch.import_mode != QualidadeImportBatch.MODE_RETROACTIVE:
        raise CancelConflict("Cancelamento disponível apenas para carga retroativa.")

    if batch.status == QualidadeImportBatch.STATUS_CANCELLED:
        return batch, "already_cancelled"
    if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
        return batch, "pending"

    if batch.status not in QualidadeImportBatch.CANCELABLE_STATUSES:
        raise CancelConflict(
            f"Lote em status '{batch.status}' não pode ser cancelado. "
            "Consulte o status persistido antes de reenviar ou apagar o arquivo."
        )

    now = timezone.now()
    reason_text = (reason or "").strip()[:2000]
    stage = batch.current_stage or batch.status

    # uploading / validated: cancelamento imediato sem alterar fatos
    if batch.status in {
        QualidadeImportBatch.STATUS_UPLOADING,
        QualidadeImportBatch.STATUS_VALIDATED,
    }:
        updated = conditional_batch_update(
            batch.pk,
            expected_statuses={batch.status},
            status=QualidadeImportBatch.STATUS_CANCELLED,
            phase="Carga cancelada (nenhuma alteração na base)",
            current_stage="cancelled",
            cancel_requested_at=now,
            cancelled_at=now,
            cancelled_by=user if getattr(user, "pk", None) else None,
            cancellation_reason=reason_text or f"Cancelado em {stage}",
            rollback_status=QualidadeImportBatch.ROLLBACK_NONE,
            rollback_detail="",
            worker_token="",
            finished_at=now,
            heartbeat_at=now,
            failure_detail="",
            failure_code="",
        )
        if not updated:
            batch.refresh_from_db()
            if batch.status == QualidadeImportBatch.STATUS_CANCELLED:
                return batch, "already_cancelled"
            if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
                return batch, "pending"
            raise CancelConflict("Não foi possível cancelar: estado mudou concurrentemente.")
        batch.refresh_from_db()
        return batch, "accepted"

    # validating / processing: pedido cooperativo; worker finaliza
    updated = QualidadeImportBatch.objects.filter(
        pk=batch.pk,
        status__in={
            QualidadeImportBatch.STATUS_VALIDATING,
            QualidadeImportBatch.STATUS_PROCESSING,
        },
    ).update(
        status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
        phase="Cancelamento solicitado — aguardando worker",
        current_stage="cancel_requested",
        cancel_requested_at=now,
        cancelled_by=user if getattr(user, "pk", None) else None,
        cancellation_reason=reason_text or f"Cancelamento solicitado em {stage}",
        rollback_status=(
            QualidadeImportBatch.ROLLBACK_PENDING
            if batch.status == QualidadeImportBatch.STATUS_PROCESSING
            and completed_months_needing_rollback(batch)
            else QualidadeImportBatch.ROLLBACK_NONE
        ),
        heartbeat_at=now,
        failure_detail="",
        failure_code="",
    )
    batch.refresh_from_db()
    if not updated:
        if batch.status == QualidadeImportBatch.STATUS_CANCELLED:
            return batch, "already_cancelled"
        if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
            return batch, "pending"
        # Race: validating finished → validated between check and update
        if batch.status == QualidadeImportBatch.STATUS_VALIDATED:
            return request_cancel_qualidade_import(batch_id, user, reason=reason)
        raise CancelConflict(
            f"Não foi possível cancelar: estado atual '{batch.status}'."
        )

    # Se processing já tinha meses concluídos, marcar rollback pendente com contagem atual
    if completed_months_needing_rollback(batch):
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            rollback_status=QualidadeImportBatch.ROLLBACK_PENDING,
        )
        batch.refresh_from_db()

    # Worker deve concluir cancelamento cooperativo
    from apps.qualidade_operacional.services.retroactive_import import (
        spawn_qualidade_import_worker,
    )

    spawn_qualidade_import_worker()
    return batch, "accepted"


def mark_cancelled_no_data_change(
    batch_id,
    *,
    worker_token: str | None = None,
    phase: str = "Carga cancelada (nenhuma alteração na base)",
) -> bool:
    now = timezone.now()
    qs = QualidadeImportBatch.objects.filter(
        pk=batch_id,
        status__in={
            QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            QualidadeImportBatch.STATUS_VALIDATING,
            QualidadeImportBatch.STATUS_CANCELLING,
        },
    )
    if worker_token is not None:
        qs = qs.filter(worker_token=worker_token)
    return (
        qs.update(
            status=QualidadeImportBatch.STATUS_CANCELLED,
            phase=phase,
            current_stage="cancelled",
            cancelled_at=now,
            finished_at=now,
            heartbeat_at=now,
            worker_token="",
            rollback_status=QualidadeImportBatch.ROLLBACK_NONE,
            progress_percent=100,
        )
        > 0
    )


def execute_cancel_rollback(batch_id: str, *, worker_token: str | None = None) -> None:
    """Restaura competências alteradas por este lote após cancelamento."""
    batch = QualidadeImportBatch.objects.get(pk=batch_id)
    model, _, _ = _config(batch.kind)

    claimed = conditional_batch_update(
        batch.pk,
        expected_statuses={
            QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            QualidadeImportBatch.STATUS_CANCELLING,
            QualidadeImportBatch.STATUS_PROCESSING,  # se ainda não transitou
        },
        require_token=worker_token,
        status=QualidadeImportBatch.STATUS_CANCELLING,
        phase="Cancelando — restaurando competências alteradas",
        current_stage="cancelling",
        rollback_status=QualidadeImportBatch.ROLLBACK_IN_PROGRESS,
        heartbeat_at=timezone.now(),
    )
    if not claimed and worker_token:
        # Token mismatch: outro processo assumiu
        current = QualidadeImportBatch.objects.filter(pk=batch.pk).values_list(
            "status", flat=True
        ).first()
        if current in QualidadeImportBatch.TERMINAL_STATUSES:
            return
        raise MonthlyImportError(
            f"Não foi possível iniciar rollback (status={current})."
        )
    if not claimed:
        claimed = conditional_batch_update(
            batch.pk,
            expected_statuses={
                QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
                QualidadeImportBatch.STATUS_CANCELLING,
            },
            status=QualidadeImportBatch.STATUS_CANCELLING,
            phase="Cancelando — restaurando competências alteradas",
            current_stage="cancelling",
            rollback_status=QualidadeImportBatch.ROLLBACK_IN_PROGRESS,
            heartbeat_at=timezone.now(),
        )

    batch.refresh_from_db()
    months = completed_months_needing_rollback(batch)
    if not months:
        mark_cancelled_no_data_change(
            batch.pk,
            worker_token=None,
            phase="Carga cancelada (nenhuma competência havia sido alterada)",
        )
        # Clear token explicitly
        QualidadeImportBatch.objects.filter(
            pk=batch.pk, status=QualidadeImportBatch.STATUS_CANCELLED
        ).update(worker_token="")
        return

    plan = list(batch.month_plan or [])
    restored_log: list[str] = []
    try:
        for idx, month in enumerate(reversed(months)):
            competencia_key = month["competencia"]
            backup_path = Path(month["backup_path"])
            previous_rows = int(month.get("previous_rows") or 0)
            conditional_batch_update(
                batch.pk,
                expected_statuses={QualidadeImportBatch.STATUS_CANCELLING},
                current_competencia=competencia_key,
                phase=f"Cancelando — restaurando {competencia_key}",
                progress_percent=min(99, 20 + int(70 * idx / max(len(months), 1))),
                heartbeat_at=timezone.now(),
                current_stage="cancel_rollback",
            )
            if not backup_path.exists():
                raise MonthlyImportError(f"Backup ausente para {competencia_key}: {backup_path.name}")
            competencia = date.fromisoformat(f"{competencia_key}-01")
            start, end = _month_bounds(competencia)
            with transaction.atomic():
                model.objects.filter(data__gte=start, data__lt=end).delete()
                restored = _bulk_from_iter(model, _restore_objects(model, backup_path))
                if restored != previous_rows:
                    raise MonthlyImportError(
                        f"{competencia_key}: restaurado {restored} ≠ backup {previous_rows}."
                    )
            restored_log.append(competencia_key)
            plan = [
                (
                    {**row, "status": "rolled_back", "situation": "Restaurado no cancelamento"}
                    if row.get("competencia") == competencia_key
                    else dict(row)
                )
                for row in plan
            ]
            QualidadeImportBatch.objects.filter(
                pk=batch.pk, status=QualidadeImportBatch.STATUS_CANCELLING
            ).update(month_plan=plan, heartbeat_at=timezone.now())

        warnings = list(batch.warnings or [])
        try:
            refresh_filter_catalog()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Rollback ok, mas catálogo/cache não atualizou: {exc}")

        now = timezone.now()
        detail = "Competências restauradas: " + ", ".join(restored_log)
        updated = conditional_batch_update(
            batch.pk,
            expected_statuses={QualidadeImportBatch.STATUS_CANCELLING},
            status=QualidadeImportBatch.STATUS_CANCELLED,
            phase="Carga cancelada e competências restauradas",
            current_stage="cancelled",
            cancelled_at=now,
            finished_at=now,
            heartbeat_at=now,
            worker_token="",
            rollback_status=QualidadeImportBatch.ROLLBACK_COMPLETED,
            rollback_detail=detail,
            month_plan=plan,
            warnings=warnings,
            current_competencia="",
            progress_percent=100,
            failure_detail="",
            failure_code="",
        )
        if not updated:
            logger.warning(
                "Rollback concluiu mas status final não aplicado (batch=%s)", batch.pk
            )
    except Exception as exc:  # noqa: BLE001
        now = timezone.now()
        msg = str(exc)[:1000]
        conditional_batch_update(
            batch.pk,
            expected_statuses={
                QualidadeImportBatch.STATUS_CANCELLING,
                QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            },
            status=QualidadeImportBatch.STATUS_ROLLBACK_FAILED,
            phase="Cancelamento com falha no rollback — intervenção necessária",
            current_stage="rollback_failed",
            finished_at=now,
            last_error_at=now,
            heartbeat_at=now,
            worker_token="",
            rollback_status=QualidadeImportBatch.ROLLBACK_FAILED,
            rollback_detail=msg,
            failure_detail=msg,
            failure_code="ROLLBACK_FAILED",
            progress_percent=100,
        )
        logger.exception("Rollback falhou para batch %s: %s", batch.pk, exc)


def process_cancel_if_needed(batch: QualidadeImportBatch, *, worker_token: str | None = None) -> bool:
    """
    Se o lote está em cancel_requested/cancelling, executa cancelamento e retorna True.
    """
    if batch.status not in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
        return False
    months = completed_months_needing_rollback(batch)
    if not months and batch.status == QualidadeImportBatch.STATUS_CANCEL_REQUESTED:
        # Validação ou processing sem meses alterados
        mark_cancelled_no_data_change(batch.pk, worker_token=worker_token)
        QualidadeImportBatch.objects.filter(
            pk=batch.pk, status=QualidadeImportBatch.STATUS_CANCELLED
        ).update(worker_token="")
        return True
    execute_cancel_rollback(str(batch.pk), worker_token=worker_token)
    return True
