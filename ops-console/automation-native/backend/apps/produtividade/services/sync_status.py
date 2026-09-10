# -*- coding: utf-8 -*-
from __future__ import annotations

from django.db.models import Max, Min
from django.utils import timezone

from apps.produtividade.models import ProductivityRecord, ProductivitySyncLog
from apps.produtividade.services.criticality import criticality_methodology_payload


def _isoformat_local(value):
    """Serializa datetime no fuso do portal (America/Sao_Paulo)."""
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.isoformat()


def get_sync_status() -> dict:
    """Metadados da base de produtividade + última sincronização registrada."""
    bounds = ProductivityRecord.objects.aggregate(
        min_date=Min("recorded_at"),
        max_date=Max("recorded_at"),
    )

    last_log = ProductivitySyncLog.objects.order_by("-started_at").first()
    last_success = (
        ProductivitySyncLog.objects.filter(success=True)
        .order_by("-finished_at", "-started_at")
        .first()
    )

    last_sync_at = None
    last_sync_success = True
    last_sync_message = ""
    if last_log is not None:
        last_sync_at = _isoformat_local(last_log.finished_at or last_log.started_at)
        last_sync_success = bool(last_log.success)
        last_sync_message = last_log.message or ""

    last_success_at = None
    if last_success is not None:
        last_success_at = _isoformat_local(
            last_success.finished_at or last_success.started_at
        )

    return {
        "row_count": ProductivityRecord.objects.count(),
        "min_recorded_at": _isoformat_local(bounds["min_date"]),
        "max_recorded_at": _isoformat_local(bounds["max_date"]),
        # Campos legados — portal não usa; mantidos para compatibilidade leve
        "source_dir": "",
        "source_file": None,
        "source_file_name": None,
        "latest_available_file": None,
        "last_sync_at": last_sync_at,
        "last_success_at": last_success_at,
        "last_sync_success": last_sync_success,
        "last_sync_message": last_sync_message,
        "criticality_methodology": criticality_methodology_payload(),
    }
