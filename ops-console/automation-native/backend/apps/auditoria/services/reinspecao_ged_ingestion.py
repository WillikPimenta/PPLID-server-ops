"""Auditoria de artefatos e tentativas da ingestão GED de Reinspeção."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.common.models import BotDataArtifact, BotDataIngestion, BotDbSyncJob

DOMAIN = BotDbSyncJob.DOMAIN_REINSPECAO_GED
KIND = "irregularidade"
PARSER_VERSION = "1"


@dataclass(frozen=True)
class GedIngestionAttempt:
    ingestion: BotDataIngestion
    duplicate_delivery: bool


def compute_content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handler:
        for chunk in iter(lambda: handler.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _running_sync_job(path: Path) -> BotDbSyncJob | None:
    path_value = str(path)
    resolved_value = str(path.resolve())
    return (
        BotDbSyncJob.objects.filter(
            domain=DOMAIN,
            status=BotDbSyncJob.STATUS_RUNNING,
            source_path__in={path_value, resolved_value},
        )
        .order_by("-started_at", "-id")
        .first()
    )


def start_ged_ingestion(
    path: Path,
    *,
    sync_job: BotDbSyncJob | None = None,
) -> GedIngestionAttempt:
    """Cria tentativa auditável; falhas anteriores nunca impedem um retry."""
    source = path.resolve()
    stat = source.stat()
    content_sha256 = compute_content_sha256(source)
    safe_name = source.name
    reference_date = timezone.localdate()

    artifact, _ = BotDataArtifact.objects.get_or_create(
        domain=DOMAIN,
        kind=KIND,
        content_sha256=content_sha256,
        defaults={
            "semantic_key": f"{KIND}:{content_sha256[:24]}",
            "safe_name": safe_name,
            "content_size": stat.st_size,
            "reference_date": reference_date,
            "legacy_unverified": False,
        },
    )

    with transaction.atomic():
        artifact = BotDataArtifact.objects.select_for_update().get(pk=artifact.pk)
        previous_delivery = BotDataIngestion.objects.filter(
            artifact=artifact,
            source_file=safe_name,
            source_mtime=stat.st_mtime,
            source_size=stat.st_size,
            status=BotDataIngestion.STATUS_COMPLETED,
        ).exists()
        last_attempt = (
            BotDataIngestion.objects.filter(artifact=artifact)
            .aggregate(value=Max("attempt_number"))
            .get("value")
            or 0
        )
        attempt_number = int(last_attempt) + 1
        ingestion = BotDataIngestion.objects.create(
            source_key=f"a{artifact.pk}:t{attempt_number}",
            artifact=artifact,
            attempt_number=attempt_number,
            parser_version=PARSER_VERSION,
            domain=DOMAIN,
            kind=KIND,
            reference_date=reference_date,
            run_id=source.stem[:64],
            source_file=safe_name,
            source_mtime=stat.st_mtime,
            source_size=stat.st_size,
            status=(
                BotDataIngestion.STATUS_COMPLETED
                if previous_delivery
                else BotDataIngestion.STATUS_PROCESSING
            ),
            sync_job=sync_job or _running_sync_job(source),
            finished_at=timezone.now() if previous_delivery else None,
        )

    return GedIngestionAttempt(
        ingestion=ingestion,
        duplicate_delivery=previous_delivery,
    )


def finish_ged_ingestion(
    ingestion: BotDataIngestion,
    *,
    success: bool,
    rows_read: int = 0,
    rows_loaded: int = 0,
    rows_rejected: int = 0,
    error_summary: str = "",
) -> BotDataIngestion:
    ingestion.status = (
        BotDataIngestion.STATUS_COMPLETED
        if success
        else BotDataIngestion.STATUS_FAILED
    )
    ingestion.rows_read = max(0, int(rows_read or 0))
    ingestion.rows_loaded = max(0, int(rows_loaded or 0))
    ingestion.rows_rejected = max(0, int(rows_rejected or 0))
    ingestion.error_summary = (error_summary or "")[:2000]
    ingestion.finished_at = timezone.now()
    ingestion.save(
        update_fields=[
            "status",
            "rows_read",
            "rows_loaded",
            "rows_rejected",
            "error_summary",
            "finished_at",
        ]
    )
    return ingestion
