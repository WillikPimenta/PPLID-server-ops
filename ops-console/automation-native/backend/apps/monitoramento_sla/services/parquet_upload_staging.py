# -*- coding: utf-8 -*-
"""Staging temporário de upload Parquet (monitoramento SLA consolidado)."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from dataclasses import asdict, dataclass
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

CACHE_PREFIX = "monitoramento_sla_parquet_upload:"


class UploadValidationError(ValueError):
    """Erro de validação no upload de Parquet."""


@dataclass
class ParquetUploadRecord:
    upload_id: str
    user_id: str
    path: str
    original_name: str
    sha256: str
    size_bytes: int
    expires_at: str


def _max_bytes() -> int:
    return int(getattr(settings, "MONITORAMENTO_SLA_PARQUET_MAX_BYTES", 500 * 1024 * 1024))


def _ttl_hours() -> int:
    return int(getattr(settings, "MONITORAMENTO_SLA_PARQUET_UPLOAD_TTL_HOURS", 24))


def _upload_dir() -> Path:
    base = getattr(settings, "MONITORAMENTO_SLA_PARQUET_UPLOAD_DIR", "")
    if base:
        path = Path(base)
    else:
        path = Path(settings.BASE_DIR) / "tmp" / "monitoramento_sla_parquet"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_key(upload_id: str) -> str:
    return f"{CACHE_PREFIX}{upload_id}"


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
    if not name.lower().endswith(".parquet"):
        raise UploadValidationError(f"Arquivo inválido: {name}. Apenas .parquet são aceitos.")


def _save_record(record: ParquetUploadRecord) -> None:
    expires = datetime.fromisoformat(record.expires_at)
    if timezone.is_naive(expires):
        expires = timezone.make_aware(expires)
    ttl = max(60, int((expires - timezone.now()).total_seconds()))
    cache.set(_cache_key(record.upload_id), json.dumps(asdict(record), default=str), timeout=ttl)


def expire_stale_uploads(*, user=None) -> int:
    # Cache TTL handles expiry; physical cleanup on access.
    removed = 0
    upload_dir = _upload_dir()
    cutoff = timezone.now() - timedelta(hours=_ttl_hours() + 1)
    for path in upload_dir.glob("*.parquet"):
        try:
            if path.stat().st_mtime < cutoff.timestamp():
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def delete_upload_record(record: ParquetUploadRecord) -> None:
    cache.delete(_cache_key(record.upload_id))
    try:
        Path(record.path).unlink(missing_ok=True)
    except OSError:
        pass


def get_upload_record(upload_id: str, *, user=None) -> ParquetUploadRecord:
    expire_stale_uploads(user=user)
    raw = cache.get(_cache_key(upload_id))
    if not raw:
        raise UploadValidationError("Upload não encontrado ou expirado.")
    data = json.loads(raw)
    record = ParquetUploadRecord(**data)
    expires = datetime.fromisoformat(record.expires_at)
    if timezone.is_naive(expires):
        expires = timezone.make_aware(expires)
    if expires < timezone.now():
        delete_upload_record(record)
        raise UploadValidationError("Upload expirado. Envie o arquivo novamente.")
    if user is not None and str(record.user_id) != str(user.pk):
        raise UploadValidationError("Upload não pertence ao usuário atual.")
    if not Path(record.path).is_file():
        delete_upload_record(record)
        raise UploadValidationError("Arquivo de upload não encontrado.")
    return record


def stage_parquet_upload(*, user, upload: UploadedFile) -> ParquetUploadRecord:
    name = Path(upload.name or "upload.parquet").name
    _validate_upload_name(name)
    sha256, size = _digest_upload(upload)
    if size > _max_bytes():
        raise UploadValidationError(
            f"Arquivo excede { _max_bytes() // (1024 * 1024) } MB."
        )

    expire_stale_uploads(user=user)
    upload_id = str(uuid.uuid4())
    dest = _upload_dir() / f"{upload_id}.parquet"
    with dest.open("wb") as out:
        upload.seek(0)
        for chunk in iter(lambda: upload.read(1024 * 1024), b""):
            out.write(chunk)

    expires_at = timezone.now() + timedelta(hours=_ttl_hours())
    record = ParquetUploadRecord(
        upload_id=upload_id,
        user_id=str(user.pk),
        path=str(dest),
        original_name=name,
        sha256=sha256,
        size_bytes=size,
        expires_at=expires_at.isoformat(),
    )
    _save_record(record)
    return record
