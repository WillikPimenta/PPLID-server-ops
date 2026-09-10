"""Upload temporário do workbook Identificação dos Processos."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from apps.dimensoes_processos.models import IdentificacaoProcessosUpload


class UploadValidationError(ValueError):
    """Erro de validação no upload do workbook."""


def _max_bytes() -> int:
    return int(getattr(settings, "IDENTIFICACAO_PROCESSOS_UPLOAD_MAX_BYTES", 50 * 1024 * 1024))


def _ttl_hours() -> int:
    return int(getattr(settings, "IDENTIFICACAO_PROCESSOS_UPLOAD_TTL_HOURS", 24))


def _digest_upload(upload: UploadedFile) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    upload.seek(0)
    for chunk in iter(lambda: upload.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    upload.seek(0)
    return digest.hexdigest(), size


def expire_stale_uploads(*, user=None) -> int:
    qs = IdentificacaoProcessosUpload.objects.filter(
        status=IdentificacaoProcessosUpload.STATUS_ACTIVE,
        expires_at__lt=timezone.now(),
    )
    if user is not None:
        qs = qs.filter(created_by=user)
    count = 0
    for upload in qs:
        upload.status = IdentificacaoProcessosUpload.STATUS_EXPIRED
        upload.save(update_fields=["status"])
        if upload.file:
            upload.file.delete(save=False)
        count += 1
    return count


def get_active_upload(token: str, *, user=None) -> IdentificacaoProcessosUpload:
    expire_stale_uploads(user=user)
    try:
        upload = IdentificacaoProcessosUpload.objects.get(token=token)
    except (IdentificacaoProcessosUpload.DoesNotExist, ValueError) as exc:
        raise UploadValidationError("Upload não encontrado.") from exc

    if user is not None and upload.created_by_id != user.pk:
        raise UploadValidationError("Upload não pertence ao usuário atual.")
    if upload.status != IdentificacaoProcessosUpload.STATUS_ACTIVE:
        raise UploadValidationError("Este upload não está mais ativo.")
    if upload.expires_at < timezone.now():
        upload.status = IdentificacaoProcessosUpload.STATUS_EXPIRED
        upload.save(update_fields=["status"])
        if upload.file:
            upload.file.delete(save=False)
        raise UploadValidationError("Upload expirado.")
    return upload


def has_running_import(*, user=None) -> bool:
    from apps.dimensoes_processos.models import IdentificacaoProcessosImportRun

    qs = IdentificacaoProcessosImportRun.objects.filter(status=IdentificacaoProcessosImportRun.STATUS_RUNNING)
    if user is not None:
        qs = qs.filter(created_by=user)
    return qs.exists()


def create_upload(*, user, upload: UploadedFile) -> IdentificacaoProcessosUpload:
    name = (upload.name or "").strip()
    if not name.lower().endswith(".xlsx"):
        raise UploadValidationError("Arquivo inválido. Apenas .xlsx são aceitos.")

    sha256, size = _digest_upload(upload)
    if size > _max_bytes():
        raise UploadValidationError(f"Arquivo excede o limite de {_max_bytes() // (1024 * 1024)} MB.")

    expire_stale_uploads(user=user)
    record = IdentificacaoProcessosUpload.objects.create(
        created_by=user,
        expires_at=timezone.now() + timedelta(hours=_ttl_hours()),
        original_name=name,
        sha256=sha256,
        size_bytes=size,
    )
    record.file.save(name, upload, save=True)
    return record


def mark_upload_consumed(upload: IdentificacaoProcessosUpload) -> None:
    upload.status = IdentificacaoProcessosUpload.STATUS_CONSUMED
    upload.save(update_fields=["status"])
