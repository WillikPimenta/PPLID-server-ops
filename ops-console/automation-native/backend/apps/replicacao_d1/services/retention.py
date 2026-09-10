# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.common.models import BotDataArtifact, BotDataIngestion
from apps.replicacao_d1.models import ReplicacaoD1SyncLog
from apps.replicacao_d1.services.artifact_retention import ingestion_eligible_for_purge


@dataclass
class RetentionReport:
    sync_logs_deleted: int = 0
    ingestions_deleted: int = 0
    artifacts_deleted: int = 0
    sync_log_cutoff: str = ""
    ingestion_cutoff: str = ""

    def as_dict(self) -> dict:
        return {
            "sync_logs_deleted": self.sync_logs_deleted,
            "ingestions_deleted": self.ingestions_deleted,
            "artifacts_deleted": self.artifacts_deleted,
            "sync_log_cutoff": self.sync_log_cutoff,
            "ingestion_cutoff": self.ingestion_cutoff,
        }


def _sync_log_retention_days() -> int:
    return max(7, int(getattr(settings, "REPLICACAO_D1_SYNC_LOG_RETENTION_DAYS", 90)))


def _ingestion_retention_days() -> int:
    return max(14, int(getattr(settings, "REPLICACAO_D1_INGESTION_RETENTION_DAYS", 180)))


def purge_replicacao_d1_retention(*, dry_run: bool = False) -> RetentionReport:
    """Remove logs técnicos antigos; preserva runs, protocolos e replicados."""
    now = timezone.now()
    sync_cutoff = now - timedelta(days=_sync_log_retention_days())
    ingest_cutoff = now - timedelta(days=_ingestion_retention_days())

    sync_qs = ReplicacaoD1SyncLog.objects.filter(started_at__lt=sync_cutoff)
    ingest_qs = BotDataIngestion.objects.filter(
        domain="replicacao_d1",
        started_at__lt=ingest_cutoff,
    ).exclude(status=BotDataIngestion.STATUS_PROCESSING).select_related("artifact")

    ingest_ids = [ing.pk for ing in ingest_qs if ingestion_eligible_for_purge(ing)]
    ingest_deletable = BotDataIngestion.objects.filter(pk__in=ingest_ids)
    artifact_ids = list(
        ingest_deletable.exclude(artifact_id=None).values_list("artifact_id", flat=True)
    )

    report = RetentionReport(
        sync_logs_deleted=sync_qs.count(),
        ingestions_deleted=ingest_deletable.count(),
        sync_log_cutoff=sync_cutoff.isoformat(),
        ingestion_cutoff=ingest_cutoff.isoformat(),
    )

    if not dry_run:
        sync_qs.delete()
        ingest_deletable.delete()
        if artifact_ids:
            report.artifacts_deleted = BotDataArtifact.objects.filter(
                pk__in=artifact_ids,
                domain="replicacao_d1",
                ingestions__isnull=True,
            ).delete()[0]
    else:
        report.artifacts_deleted = BotDataArtifact.objects.filter(
            pk__in=artifact_ids,
            domain="replicacao_d1",
            ingestions__isnull=True,
        ).count()

    return report


def legacy_run_columns_report() -> dict[str, int]:
    """Contagem de valores preenchidos em colunas legadas (pré-remoção)."""
    from apps.replicacao_d1.models import ReplicacaoD1Run

    # Após migration as colunas não existem; helper usado só na migration.
    counts: dict[str, int] = {}
    if not hasattr(ReplicacaoD1Run, "source_file"):
        return counts
    counts["source_file"] = ReplicacaoD1Run.objects.exclude(source_file="").count()
    counts["config_version"] = ReplicacaoD1Run.objects.filter(config_version__isnull=False).count()
    counts["config_hash"] = ReplicacaoD1Run.objects.exclude(config_hash="").count()
    return counts
