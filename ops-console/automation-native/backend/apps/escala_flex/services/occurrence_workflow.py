"""Workflow de ocorrências operacionais (pendente, aprovada, finalizada, etc.)."""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from ..models import OperationalOccurrence


def occurrence_start_at(occurrence: OperationalOccurrence):
    if occurrence.scheduled_time:
        return timezone.make_aware(datetime.combine(occurrence.date, occurrence.scheduled_time))
    return occurrence.created_at


def resolve_operational_occurrence_workflow_status(
    occurrence: OperationalOccurrence,
) -> str:
    if occurrence.cancelled:
        return "cancelled"
    if occurrence.approved is None:
        return "pending"
    if not occurrence.approved:
        return "rejected"
    start_at = occurrence_start_at(occurrence)
    if start_at:
        end_at = start_at + timedelta(seconds=occurrence.forecast_seconds)
        if timezone.now() >= end_at:
            return "finalized"
    return "approved"


def occurrence_end_at(occurrence: OperationalOccurrence, reference=None):
    start_at = occurrence_start_at(occurrence)
    if not start_at:
        return None
    return start_at + timedelta(seconds=occurrence.forecast_seconds)


def is_operational_occurrence_in_progress(
    occurrence: OperationalOccurrence,
    reference=None,
) -> bool:
    """True se a ocorrência aprovada já iniciou e ainda não finalizou."""
    reference = reference or timezone.now()
    if occurrence.cancelled or occurrence.approved is not True:
        return False
    start_at = occurrence_start_at(occurrence)
    end_at = occurrence_end_at(occurrence)
    if not start_at or not end_at:
        return False
    return start_at <= reference < end_at


def get_pending_occurrence_extension(occurrence: OperationalOccurrence):
    return occurrence.extensions.filter(approved__isnull=True).order_by("-created_at").first()


def occurrence_has_pending_extension(occurrence: OperationalOccurrence) -> bool:
    return occurrence.extensions.filter(approved__isnull=True).exists()


def is_operational_occurrence_manageable(occurrence: OperationalOccurrence) -> bool:
    """True se a ocorrência aprovada ainda pode ser editada ou cancelada."""
    return resolve_operational_occurrence_workflow_status(occurrence) == "approved"


def is_operational_occurrence_rejected(occurrence: OperationalOccurrence) -> bool:
    return not occurrence.cancelled and occurrence.approved is False


def is_operational_occurrence_editable(occurrence: OperationalOccurrence) -> bool:
    """True se a ocorrência pode ser editada (aprovada ativa ou recusada)."""
    return is_operational_occurrence_manageable(occurrence) or is_operational_occurrence_rejected(
        occurrence
    )


def _reference_floor(reference=None):
    reference = reference or timezone.now()
    return timezone.localtime(reference).replace(second=0, microsecond=0)


def is_occurrence_start_in_past(occurrence: OperationalOccurrence, reference=None) -> bool:
    start_at = occurrence_start_at(occurrence)
    if not start_at:
        return False
    ref_floor = _reference_floor(reference)
    return start_at.replace(second=0, microsecond=0) < ref_floor


def validate_rejected_occurrence_scheduled_time(
    occurrence: OperationalOccurrence,
    scheduled_time,
    *,
    reference=None,
) -> str | None:
    """Exige horário >= agora quando o início original da ocorrência já passou."""
    if not is_occurrence_start_in_past(occurrence, reference=reference):
        return None
    if not scheduled_time:
        return "O horário de início já passou. Ajuste para o horário atual ou posterior."
    naive = datetime.combine(occurrence.date, scheduled_time)
    start = timezone.make_aware(naive, timezone.get_current_timezone())
    if start.replace(second=0, microsecond=0) < _reference_floor(reference):
        return (
            "O horário de início já passou. Ajuste para o horário atual ou posterior antes de aprovar."
        )
    return None
