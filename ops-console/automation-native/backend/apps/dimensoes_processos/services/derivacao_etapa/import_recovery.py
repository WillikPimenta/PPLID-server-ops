# -*- coding: utf-8 -*-
"""Recuperação de scan/import órfãos (status running sem job ativo)."""
from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.dimensoes_processos.models import DerivacaoEtapaImportRun

PURGE_KIND = "purge"


def _stale_minutes() -> int:
    return int(getattr(settings, "DERIVACAO_ETAPA_IMPORT_STALE_MINUTES", 15))


def recover_stale_derivacao_import_runs() -> int:
    """Marca scan/import órfãos (running há muito tempo) como erro."""
    cutoff = timezone.now() - timedelta(minutes=_stale_minutes())
    stale = DerivacaoEtapaImportRun.objects.filter(
        status=DerivacaoEtapaImportRun.STATUS_RUNNING,
        started_at__lt=cutoff,
        run_kind__in=(
            DerivacaoEtapaImportRun.KIND_SCAN,
            DerivacaoEtapaImportRun.KIND_IMPORT,
        ),
    )
    count = 0
    for run in stale.iterator():
        metrics = dict(run.metrics or {})
        metrics["phase"] = "error"
        metrics["error"] = "Scan/import interrompido (servidor reiniciado ou timeout)."
        run.status = DerivacaoEtapaImportRun.STATUS_ERROR
        run.message = "Scan/import interrompido."
        run.finished_at = timezone.now()
        run.metrics = metrics
        run.save(update_fields=["status", "message", "finished_at", "metrics"])
        count += 1
    return count


def has_active_derivacao_scan_or_import() -> bool:
    """True se há scan/import realmente em execução (iniciado dentro da janela stale)."""
    cutoff = timezone.now() - timedelta(minutes=_stale_minutes())
    return DerivacaoEtapaImportRun.objects.filter(
        status=DerivacaoEtapaImportRun.STATUS_RUNNING,
        run_kind__in=(
            DerivacaoEtapaImportRun.KIND_SCAN,
            DerivacaoEtapaImportRun.KIND_IMPORT,
        ),
        started_at__gte=cutoff,
    ).exists()


def has_running_derivacao_purge() -> bool:
    return DerivacaoEtapaImportRun.objects.filter(
        status=DerivacaoEtapaImportRun.STATUS_RUNNING,
        run_kind=DerivacaoEtapaImportRun.KIND_PURGE,
    ).exists()
