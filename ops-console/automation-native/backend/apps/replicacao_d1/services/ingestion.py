# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.common.models import BotDataArtifact, BotDataIngestion, BotDbSyncJob

PARSER_VERSION = "1"


def compute_content_sha256(path: Path | str) -> str:
    """SHA-256 do conteúdo em streaming."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def build_source_key(
    *,
    domain: str,
    kind: str,
    reference_date: date | None = None,
    run_id: str = "",
    source_file: str = "",
    source_mtime: float | None = None,
    source_size: int | None = None,
) -> str:
    parts = [
        domain,
        kind,
        reference_date.isoformat() if reference_date else "",
        run_id or "",
        source_file or "",
        str(source_mtime or ""),
        str(source_size or ""),
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:64]


def _basename_only(path: str) -> str:
    if not path:
        return ""
    return Path(path.replace("\\", "/")).name


def _semantic_key(
    *,
    kind: str,
    run_id: str = "",
    reference_date: date | None = None,
    safe_name: str = "",
) -> str:
    if run_id:
        return f"{kind}:{run_id}"
    if reference_date:
        return f"{kind}:{reference_date.isoformat()}"
    return f"{kind}:{safe_name or 'unknown'}"


def resolve_artifact(
    *,
    domain: str,
    kind: str,
    reference_date: date | None = None,
    run_id: str = "",
    source_file: str = "",
    content_path: Path | str | None = None,
    source_size: int | None = None,
) -> BotDataArtifact:
    safe_name = _basename_only(source_file)
    semantic_key = _semantic_key(
        kind=kind,
        run_id=run_id,
        reference_date=reference_date,
        safe_name=safe_name,
    )
    content_sha256 = ""
    legacy_unverified = True
    size = source_size

    path = Path(content_path) if content_path else None
    if path and path.is_file():
        content_sha256 = compute_content_sha256(path)
        legacy_unverified = False
        if size is None:
            size = path.stat().st_size

    if content_sha256:
        artifact, _ = BotDataArtifact.objects.get_or_create(
            domain=domain,
            kind=kind,
            content_sha256=content_sha256,
            defaults={
                "semantic_key": semantic_key,
                "safe_name": safe_name or path.name if path else "",
                "content_size": size,
                "reference_date": reference_date,
                "legacy_unverified": False,
            },
        )
        return artifact

    artifact, _ = BotDataArtifact.objects.get_or_create(
        domain=domain,
        kind=kind,
        semantic_key=semantic_key,
        content_sha256="",
        defaults={
            "safe_name": safe_name,
            "content_size": size,
            "reference_date": reference_date,
            "legacy_unverified": legacy_unverified,
        },
    )
    return artifact


def start_ingestion(
    *,
    domain: str,
    kind: str,
    reference_date: date | None = None,
    run_id: str = "",
    source_file: str = "",
    source_mtime: float | None = None,
    source_size: int | None = None,
    sync_job: BotDbSyncJob | None = None,
    content_path: Path | str | None = None,
) -> BotDataIngestion:
    artifact = resolve_artifact(
        domain=domain,
        kind=kind,
        reference_date=reference_date,
        run_id=run_id,
        source_file=source_file,
        content_path=content_path,
        source_size=source_size,
    )
    safe_file = _basename_only(source_file)
    # Serializa a alocação do número da tentativa por artefato. Isso evita que
    # dois drains concorrentes recebam o mesmo attempt_number.
    with transaction.atomic():
        artifact = BotDataArtifact.objects.select_for_update().get(pk=artifact.pk)
        last_attempt = (
            BotDataIngestion.objects.filter(artifact=artifact)
            .aggregate(m=Max("attempt_number"))
            .get("m")
            or 0
        )
        attempt_number = int(last_attempt) + 1
        ingestion = BotDataIngestion.objects.create(
            source_key=f"a{artifact.pk}:t{attempt_number}",
            artifact=artifact,
            attempt_number=attempt_number,
            parser_version=PARSER_VERSION,
            domain=domain,
            kind=kind,
            reference_date=reference_date,
            run_id=run_id or "",
            source_file=safe_file,
            source_mtime=source_mtime,
            source_size=source_size,
            status=BotDataIngestion.STATUS_PROCESSING,
            sync_job=sync_job,
        )
    return ingestion


def finish_ingestion(
    ingestion: BotDataIngestion,
    *,
    success: bool,
    rows_read: int = 0,
    rows_loaded: int = 0,
    rows_rejected: int = 0,
    error_summary: str = "",
    partial: bool = False,
) -> BotDataIngestion:
    if success and rows_rejected > 0 and rows_loaded > 0:
        status = BotDataIngestion.STATUS_PARTIAL
    elif success and partial:
        status = BotDataIngestion.STATUS_PARTIAL
    elif success:
        status = BotDataIngestion.STATUS_COMPLETED
    else:
        status = BotDataIngestion.STATUS_FAILED
    ingestion.status = status
    ingestion.rows_read = rows_read
    ingestion.rows_loaded = rows_loaded
    ingestion.rows_rejected = rows_rejected
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
