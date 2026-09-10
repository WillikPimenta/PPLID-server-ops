# -*- coding: utf-8 -*-
"""Upload temporário de CSVs derivacao_etapa."""
from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from apps.dimensoes_processos.models import DerivacaoEtapaUploadBatch, DerivacaoEtapaUploadFile
from apps.dimensoes_processos.services.derivacao_etapa.preflight import build_source_manifest
from apps.dimensoes_processos.services.derivacao_etapa.reader import FILE_NAME_RE


class UploadValidationError(ValueError):
    """Erro de validação no upload de CSVs."""


def _max_files() -> int:
    return int(getattr(settings, "DERIVACAO_ETAPA_UPLOAD_MAX_FILES", 400))


def _max_bytes() -> int:
    return int(getattr(settings, "DERIVACAO_ETAPA_UPLOAD_MAX_BYTES", 200 * 1024 * 1024))


def _ttl_hours() -> int:
    return int(getattr(settings, "DERIVACAO_ETAPA_UPLOAD_TTL_HOURS", 24))


def _digest_upload(upload: UploadedFile) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    upload.seek(0)
    for chunk in iter(lambda: upload.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    upload.seek(0)
    return digest.hexdigest(), size


def _validate_upload_name(name: str) -> None:
    if not name.lower().endswith(".csv"):
        raise UploadValidationError(f"Arquivo inválido: {name}. Apenas .csv são aceitos.")
    if not FILE_NAME_RE.match(Path(name).name):
        raise UploadValidationError(
            f"Arquivo inválido: {name}. Use o padrão FINALIZADO_YYYYMMDD.csv."
        )


def expire_stale_batches(*, user=None) -> int:
    qs = DerivacaoEtapaUploadBatch.objects.filter(
        status=DerivacaoEtapaUploadBatch.STATUS_ACTIVE,
        expires_at__lt=timezone.now(),
    )
    if user is not None:
        qs = qs.filter(created_by=user)
    count = 0
    for batch in qs:
        delete_upload_batch(batch, mark_expired=True)
        count += 1
    return count


def get_active_upload_batch(token: str, *, user=None) -> DerivacaoEtapaUploadBatch:
    expire_stale_batches(user=user)
    try:
        batch = DerivacaoEtapaUploadBatch.objects.prefetch_related("files").get(token=token)
    except (DerivacaoEtapaUploadBatch.DoesNotExist, ValueError) as exc:
        raise UploadValidationError("Lote de upload não encontrado.") from exc

    if user is not None and batch.created_by_id != user.pk:
        raise UploadValidationError("Lote de upload não pertence ao usuário atual.")
    if batch.status != DerivacaoEtapaUploadBatch.STATUS_ACTIVE:
        raise UploadValidationError("Este lote de upload não está mais ativo.")
    if batch.expires_at < timezone.now():
        delete_upload_batch(batch, mark_expired=True)
        raise UploadValidationError("Este lote de upload expirou. Envie os arquivos novamente.")
    return batch


def batch_file_paths(batch: DerivacaoEtapaUploadBatch) -> list[Path]:
    paths: list[Path] = []
    for row in batch.files.all():
        if row.file:
            paths.append(Path(row.file.path))
    return paths


def create_upload_batch(*, user, uploads: list[UploadedFile]) -> DerivacaoEtapaUploadBatch:
    if not uploads:
        raise UploadValidationError("Envie ao menos um arquivo CSV.")
    if len(uploads) > _max_files():
        raise UploadValidationError(f"Máximo de {_max_files()} arquivos por lote.")

    seen_names: set[str] = set()
    total_bytes = 0
    parsed: list[tuple[str, UploadedFile, str, int]] = []
    for upload in uploads:
        name = Path(upload.name or "").name
        _validate_upload_name(name)
        key = name.casefold()
        if key in seen_names:
            raise UploadValidationError(f"Arquivo duplicado no lote: {name}")
        seen_names.add(key)
        sha256, size = _digest_upload(upload)
        total_bytes += size
        parsed.append((name, upload, sha256, size))

    if total_bytes > _max_bytes():
        raise UploadValidationError(
            f"Tamanho total excede { _max_bytes() // (1024 * 1024) } MB por lote."
        )

    expire_stale_batches(user=user)
    batch = DerivacaoEtapaUploadBatch.objects.create(
        created_by=user,
        expires_at=timezone.now() + timedelta(hours=_ttl_hours()),
        status=DerivacaoEtapaUploadBatch.STATUS_ACTIVE,
    )
    try:
        for name, upload, sha256, size in parsed:
            row = DerivacaoEtapaUploadFile(
                batch=batch,
                original_name=name,
                sha256=sha256,
                size_bytes=size,
            )
            row.file.save(name, upload, save=True)
        paths = batch_file_paths(batch)
        batch.file_manifest = build_source_manifest(paths)
        batch.save(update_fields=["file_manifest"])
        return batch
    except Exception:
        delete_upload_batch(batch)
        raise


def consume_upload_batch(batch: DerivacaoEtapaUploadBatch) -> None:
    batch.status = DerivacaoEtapaUploadBatch.STATUS_CONSUMED
    batch.save(update_fields=["status"])


def delete_upload_batch(batch: DerivacaoEtapaUploadBatch, *, mark_expired: bool = False) -> None:
    for row in list(batch.files.all()):
        if row.file:
            row.file.delete(save=False)
        row.delete()
    if mark_expired:
        batch.status = DerivacaoEtapaUploadBatch.STATUS_EXPIRED
        batch.save(update_fields=["status"])
    batch.delete()
