# -*- coding: utf-8 -*-
"""Carga retroativa: upload retomável, dry-run por competência e processamento durável.

Memória (validação):
  Protocolos distintos/duplicados usam SQLite em disco sob o diretório do lote
  (`protocols.sqlite`), não um set em RAM. Pico esperado ≈ buffers CSV + poucos MB
  do SQLite; totais permanecem exatos (COUNT + linhas com protocolo − distintos).

Espaço em disco:
  Antes da montagem exige margem para chunks + arquivo montado (+15%).
  Após checksum ok, chunks são removidos. Backups só na confirmação.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from apps.qualidade_operacional.models import (
    QualidadeImportBatch,
    QualidadeImportChunk,
)
from apps.qualidade_operacional.services.filter_catalog import refresh_filter_catalog
from apps.qualidade_operacional.services.monthly_import import (
    MonthlyImportError,
    _batch_dir,
    _bulk_from_iter,
    _config,
    _file_checksum,
    _header_key,
    _legacy_month_qs,
    _month_bounds,
    _restore_objects,
    _row_date,
    serialize_batch,
)
from apps.qualidade_operacional.services.falha_import_dedupe import prepare_falhas_for_import
from apps.qualidade_operacional.services.qualidade_import_cancel import (
    CancelAccepted,
    batch_flags,
    completed_months_needing_rollback,
    conditional_batch_update,
    execute_cancel_rollback,
    is_cancel_pending,
    mark_cancelled_no_data_change,
    new_worker_token,
    process_cancel_if_needed,
    raise_if_cancel_requested,
)
from apps.qualidade_operacional.services.source_config import (
    INTRANET_SOURCE_FILE,
    tsv_competencia_blocked,
    tsv_date_blocked,
)

logger = logging.getLogger(__name__)

CHUNK_SIZE_DEFAULT = 16 * 1024 * 1024  # 16 MB
STALE_MINUTES_DEFAULT = 180
CANCEL_STALE_MINUTES_DEFAULT = 10
HEARTBEAT_MIN_SECONDS = 4.0
DISK_MARGIN = 1.15
BYTES_PER_ROW_ESTIMATE = 230


def _retro_max_bytes() -> int:
    return int(
        getattr(
            settings,
            "QUALIDADE_RETRO_IMPORT_MAX_BYTES",
            2 * 1024 * 1024 * 1024,
        )
    )


def _chunk_max_bytes() -> int:
    return int(getattr(settings, "QUALIDADE_IMPORT_CHUNK_BYTES", CHUNK_SIZE_DEFAULT))


def _chunks_dir(batch: QualidadeImportBatch) -> Path:
    path = _batch_dir(batch.id) / "chunks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _disk_free(path: Path) -> int:
    target = path if path.exists() else path.parent
    return int(shutil.disk_usage(str(target)).free)


def ensure_disk_space(path: Path, needed_bytes: int, *, label: str) -> None:
    free = _disk_free(path)
    required = int(needed_bytes * DISK_MARGIN)
    if free < required:
        raise MonthlyImportError(
            f"Espaço em disco insuficiente para {label}: "
            f"livre {free // (1024 ** 2)} MB, necessário ~{required // (1024 ** 2)} MB "
            f"(margem {int((DISK_MARGIN - 1) * 100)}%)."
        )


class ProtocolTracker:
    """Contagem distinta/duplicada de protocolos com SQLite em disco (memória controlada)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            path.unlink()
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("CREATE TABLE protocols (protocolo TEXT PRIMARY KEY) WITHOUT ROWID")
        self.protocol_rows = 0
        self._pending: list[tuple[str]] = []

    def add(self, protocolo: str) -> None:
        if not protocolo:
            return
        self.protocol_rows += 1
        self._pending.append((protocolo,))
        if len(self._pending) >= 8_000:
            self.flush()

    def flush(self) -> None:
        if not self._pending:
            return
        self.conn.executemany(
            "INSERT OR IGNORE INTO protocols(protocolo) VALUES (?)",
            self._pending,
        )
        self.conn.commit()
        self._pending.clear()

    def totals(self) -> tuple[int, int]:
        self.flush()
        distinct = int(self.conn.execute("SELECT COUNT(*) FROM protocols").fetchone()[0])
        duplicates = max(0, self.protocol_rows - distinct)
        return distinct, duplicates

    def close(self) -> None:
        try:
            self.flush()
            self.conn.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


class ProgressReporter:
    """Atualiza heartbeat/progresso no máximo a cada HEARTBEAT_MIN_SECONDS; % nunca regride."""

    def __init__(self, batch_id, *, file_size: int, is_gz: bool, stage: str):
        self.batch_id = batch_id
        self.file_size = max(0, int(file_size or 0))
        self.is_gz = is_gz
        self.stage = stage
        self.started = time.monotonic()
        self._last_touch = 0.0
        self._last_percent = 0
        self.rows = 0
        self.bytes_read = 0
        self.indeterminate = bool(is_gz)

    def force_baseline(self, percent: int, phase: str) -> None:
        self._last_percent = max(self._last_percent, percent)
        now = timezone.now()
        QualidadeImportBatch.objects.filter(
            pk=self.batch_id,
            status__in={
                QualidadeImportBatch.STATUS_VALIDATING,
                QualidadeImportBatch.STATUS_PROCESSING,
                QualidadeImportBatch.STATUS_UPLOADING,
            },
        ).update(
            progress_percent=self._last_percent,
            phase=phase,
            current_stage=self.stage,
            heartbeat_at=now,
            stage_started_at=now,
            progress_indeterminate=self.indeterminate,
            rows_processed=0,
            bytes_processed=0,
            processing_rate=0,
            estimated_seconds_remaining=None,
        )

    def tick(self, *, rows: int, bytes_read: int | None, phase: str | None = None) -> None:
        self.rows = rows
        if bytes_read is not None:
            self.bytes_read = max(self.bytes_read, bytes_read)
            if self.is_gz and self.file_size > 0 and bytes_read > 0:
                self.indeterminate = False
        now_m = time.monotonic()
        if now_m - self._last_touch < HEARTBEAT_MIN_SECONDS and rows % 50_000 != 0:
            return
        self._last_touch = now_m
        elapsed = max(0.001, now_m - self.started)
        rate = self.rows / elapsed
        percent = self._compute_percent()
        if percent < self._last_percent:
            percent = self._last_percent
        self._last_percent = percent
        eta = None
        if not self.indeterminate and self.file_size > 0 and self.bytes_read > 0:
            remaining_bytes = max(0, self.file_size - self.bytes_read)
            bytes_per_s = self.bytes_read / elapsed
            if bytes_per_s > 0:
                eta = int(remaining_bytes / bytes_per_s)
        phase_text = phase or self._default_phase(rate, eta)
        QualidadeImportBatch.objects.filter(
            pk=self.batch_id,
            status__in={
                QualidadeImportBatch.STATUS_VALIDATING,
                QualidadeImportBatch.STATUS_PROCESSING,
                QualidadeImportBatch.STATUS_UPLOADING,
            },
        ).update(
            progress_percent=percent,
            phase=phase_text[:128],
            current_stage=self.stage,
            heartbeat_at=timezone.now(),
            rows_processed=self.rows,
            bytes_processed=self.bytes_read,
            processing_rate=round(rate, 1),
            estimated_seconds_remaining=eta,
            progress_indeterminate=self.indeterminate,
        )

    def _compute_percent(self) -> int:
        if self.file_size > 0 and self.bytes_read > 0 and not self.indeterminate:
            ratio = min(1.0, self.bytes_read / self.file_size)
            return max(self._last_percent, min(99, 45 + int(ratio * 54)))
        soft = min(95, 45 + int((self.rows / 500_000) * 5))
        return max(self._last_percent, soft)

    def _default_phase(self, rate: float, eta: int | None) -> str:
        if self.indeterminate:
            return f"Validando — {self.rows:,} linhas lidas".replace(",", ".")
        pct = 100.0 * self.bytes_read / self.file_size if self.file_size else 0
        eta_txt = ""
        if eta is not None:
            mins = max(1, int(round(eta / 60)))
            eta_txt = f" — ~{mins} min restantes"
        return (
            f"Validando — {self.rows:,} linhas — {pct:.1f}% "
            f"({rate:,.0f} linhas/s){eta_txt}"
        ).replace(",", ".")


def _stream_position(fh, *, is_gz: bool) -> int | None:
    try:
        if is_gz:
            fileobj = getattr(fh, "fileobj", None)
            if fileobj is not None:
                return int(fileobj.tell())
            return None
        return int(fh.tell())
    except OSError:
        return None


def cleanup_chunk_files(batch: QualidadeImportBatch) -> int:
    """Remove partes de upload após montagem/checksum. Não apaga source montado."""
    removed = 0
    for chunk in QualidadeImportChunk.objects.filter(batch=batch):
        path = Path(chunk.path) if chunk.path else None
        if path and path.exists():
            try:
                path.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Não foi possível remover chunk %s: %s", path, exc)
    QualidadeImportChunk.objects.filter(batch=batch).delete()
    chunks_path = _batch_dir(batch.id) / "chunks"
    if chunks_path.exists():
        try:
            next(chunks_path.iterdir())
        except StopIteration:
            try:
                chunks_path.rmdir()
            except OSError:
                pass
    return removed


def init_retroactive_batch(
    *,
    kind: str,
    filename: str,
    uploaded_by,
    expected_size: int = 0,
    expected_checksum: str = "",
    chunks_expected: int = 0,
) -> QualidadeImportBatch:
    if kind not in {QualidadeImportBatch.KIND_AUDITADOS, QualidadeImportBatch.KIND_FALHAS}:
        raise MonthlyImportError("Selecione Auditorias ou Falhas.")
    name = Path(filename or "upload.tsv").name
    suffix = Path(name).suffix.lower()
    if suffix not in {".tsv", ".gz"} and not name.lower().endswith(".tsv.gz"):
        raise MonthlyImportError("Envie um arquivo .tsv ou .tsv.gz.")
    max_bytes = _retro_max_bytes()
    size = max(0, int(expected_size or 0))
    if size and size > max_bytes:
        raise MonthlyImportError(
            f"O arquivo excede o limite retroativo de {max_bytes // (1024 * 1024)} MB."
        )
    batch_root = Path(settings.MEDIA_ROOT) / "qualidade_imports"
    batch_root.mkdir(parents=True, exist_ok=True)
    if size:
        ensure_disk_space(batch_root, size * 2, label="upload e montagem do arquivo")
    reconcile_stale_qualidade_batches_for_kind(kind)
    try:
        batch = QualidadeImportBatch.objects.create(
            kind=kind,
            import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            competencia=None,
            filename=name[:255],
            uploaded_by=uploaded_by,
            status=QualidadeImportBatch.STATUS_UPLOADING,
            phase="Aguardando blocos de upload",
            progress_percent=1,
            current_stage="upload",
            heartbeat_at=timezone.now(),
            expected_checksum=(expected_checksum or "").strip().lower(),
            chunks_expected=max(0, int(chunks_expected or 0)),
            file_size=size,
        )
    except IntegrityError as exc:
        reconcile_stale_qualidade_batches_for_kind(kind)
        try:
            batch = QualidadeImportBatch.objects.create(
                kind=kind,
                import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
                competencia=None,
                filename=name[:255],
                uploaded_by=uploaded_by,
                status=QualidadeImportBatch.STATUS_UPLOADING,
                phase="Aguardando blocos de upload",
                progress_percent=1,
                current_stage="upload",
                heartbeat_at=timezone.now(),
                expected_checksum=(expected_checksum or "").strip().lower(),
                chunks_expected=max(0, int(chunks_expected or 0)),
                file_size=size,
            )
        except IntegrityError as retry_exc:
            raise MonthlyImportError(
                "Já existe uma carga retroativa em andamento para essa base."
            ) from retry_exc
        _chunks_dir(batch)
        return batch
    _chunks_dir(batch)
    return batch


def receive_chunk(
    batch: QualidadeImportBatch,
    *,
    index: int,
    uploaded_file,
    checksum: str = "",
) -> QualidadeImportChunk:
    if batch.import_mode != QualidadeImportBatch.MODE_RETROACTIVE:
        raise MonthlyImportError("Este lote não é de carga retroativa.")
    if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES | {
        QualidadeImportBatch.STATUS_CANCELLED,
    }:
        raise MonthlyImportError("Este lote foi cancelado e não aceita novos blocos.")
    if batch.status not in {
        QualidadeImportBatch.STATUS_UPLOADING,
        QualidadeImportBatch.STATUS_FAILED,
    }:
        if batch.upload_complete:
            raise MonthlyImportError("O upload deste lote já foi finalizado.")
        if batch.status != QualidadeImportBatch.STATUS_UPLOADING:
            raise MonthlyImportError("Este lote não está recebendo upload.")
    if index < 0:
        raise MonthlyImportError("Índice de bloco inválido.")
    max_chunk = _chunk_max_bytes()
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        raise MonthlyImportError("Bloco vazio.")
    if size > max_chunk * 2:
        raise MonthlyImportError(
            f"Bloco excede o tamanho máximo ({max_chunk // (1024 * 1024)} MB)."
        )
    ensure_disk_space(_chunks_dir(batch), size, label=f"bloco {index}")

    digest = hashlib.sha256()
    target = _chunks_dir(batch) / f"{index:06d}.part"
    written = 0
    with target.open("wb") as output:
        for piece in uploaded_file.chunks():
            output.write(piece)
            digest.update(piece)
            written += len(piece)
    hex_digest = digest.hexdigest()
    if checksum and checksum.lower() != hex_digest:
        target.unlink(missing_ok=True)
        raise MonthlyImportError(f"Checksum do bloco {index} não confere.")

    chunk, _created = QualidadeImportChunk.objects.update_or_create(
        batch=batch,
        index=index,
        defaults={
            "size": written,
            "checksum_sha256": hex_digest,
            "path": str(target),
        },
    )
    received = QualidadeImportChunk.objects.filter(batch=batch).count()
    bytes_received = sum(
        QualidadeImportChunk.objects.filter(batch=batch).values_list("size", flat=True)
    )
    expected = batch.chunks_expected or max(received, index + 1)
    pct = min(40, max(1, int(100 * received / max(expected, 1) * 0.4)))
    previous = (
        QualidadeImportBatch.objects.filter(pk=batch.pk).values_list(
            "progress_percent", flat=True
        ).first()
        or 0
    )
    QualidadeImportBatch.objects.filter(pk=batch.pk).update(
        status=QualidadeImportBatch.STATUS_UPLOADING,
        chunks_received=received,
        bytes_received=bytes_received,
        chunks_expected=max(batch.chunks_expected, expected),
        phase=f"Upload: {received} bloco(s) recebido(s)",
        progress_percent=max(previous, pct),
        current_stage="upload",
        heartbeat_at=timezone.now(),
        failure_detail="",
    )
    return chunk


def complete_upload(batch: QualidadeImportBatch) -> QualidadeImportBatch:
    if batch.import_mode != QualidadeImportBatch.MODE_RETROACTIVE:
        raise MonthlyImportError("Este lote não é de carga retroativa.")
    if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES | {
        QualidadeImportBatch.STATUS_CANCELLED,
    }:
        raise MonthlyImportError("Este lote foi cancelado.")
    chunks = list(batch.chunks.order_by("index"))
    if not chunks:
        raise MonthlyImportError("Nenhum bloco foi enviado.")
    indices = [c.index for c in chunks]
    if indices != list(range(len(indices))):
        missing = sorted(set(range(max(indices) + 1)) - set(indices))
        raise MonthlyImportError(
            f"Blocos faltando para montar o arquivo: {missing[:12]}"
            + ("…" if len(missing) > 12 else "")
        )

    chunk_bytes = sum(c.size for c in chunks)
    ensure_disk_space(
        _batch_dir(batch.id),
        chunk_bytes,
        label="montagem do arquivo fonte",
    )

    is_gz = batch.filename.lower().endswith(".gz")
    assembled = _batch_dir(batch.id) / ("source.tsv.gz" if is_gz else "source.tsv")
    digest = hashlib.sha256()
    written = 0
    with assembled.open("wb") as output:
        for chunk in chunks:
            path = Path(chunk.path)
            if not path.exists():
                raise MonthlyImportError(f"Bloco {chunk.index} ausente no disco.")
            with path.open("rb") as source:
                while True:
                    piece = source.read(1024 * 1024)
                    if not piece:
                        break
                    output.write(piece)
                    digest.update(piece)
                    written += len(piece)
    hex_digest = digest.hexdigest()
    if batch.expected_checksum and batch.expected_checksum != hex_digest:
        raise MonthlyImportError("Checksum final do arquivo não confere.")
    if written > _retro_max_bytes():
        raise MonthlyImportError("Arquivo montado excede o limite configurado.")

    cleanup_chunk_files(batch)

    QualidadeImportBatch.objects.filter(pk=batch.pk).update(
        source_path=str(assembled),
        file_size=written,
        checksum_sha256=hex_digest,
        upload_complete=True,
        status=QualidadeImportBatch.STATUS_VALIDATING,
        phase="Upload concluído; validação agendada",
        progress_percent=45,
        current_stage="validate",
        heartbeat_at=timezone.now(),
        stage_started_at=timezone.now(),
        progress_indeterminate=is_gz,
        rows_processed=0,
        bytes_processed=0,
    )
    batch.refresh_from_db()
    spawn_qualidade_import_worker()
    return batch


def _open_tsv_path(path: Path):
    if str(path).lower().endswith(".gz"):
        fh = gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    else:
        fh = path.open("r", encoding="utf-8-sig", newline="")
    reader = csv.DictReader(fh, delimiter="\t")
    return fh, reader


def validate_retroactive_batch(batch: QualidadeImportBatch) -> QualidadeImportBatch:
    """Dry-run: agrupa por competência sem alterar o banco de fatos."""
    model, expected_headers, mapper = _config(batch.kind)
    path = Path(batch.source_path)
    if not path.exists():
        raise MonthlyImportError("Arquivo fonte não encontrado.")

    errors: list[str] = []
    warnings: list[str] = []
    rows_total = rows_valid = rows_errors = 0
    by_month: dict[str, dict[str, Any]] = {}
    is_gz = str(path).lower().endswith(".gz")
    file_size = path.stat().st_size
    tracker = ProtocolTracker(_batch_dir(batch.id) / "protocols.sqlite")
    reporter = ProgressReporter(
        batch.id, file_size=file_size, is_gz=is_gz, stage="validate"
    )
    reporter.force_baseline(45, "Validação iniciada")
    distinct_protocols = 0
    duplicate_protocol_rows = 0

    try:
        fh, reader = _open_tsv_path(path)
        with fh:
            actual = {_header_key(h or "") for h in (reader.fieldnames or [])}
            missing = sorted(h for h in expected_headers if _header_key(h) not in actual)
            if missing:
                raise MonthlyImportError("Colunas obrigatórias ausentes: " + ", ".join(missing))
            for line_number, row in enumerate(reader, start=2):
                rows_total += 1
                if rows_total % 25_000 == 0:
                    raise_if_cancel_requested(batch.id)
                    reporter.tick(
                        rows=rows_total,
                        bytes_read=_stream_position(fh, is_gz=is_gz),
                    )
                if None in row:
                    rows_errors += 1
                    if len(errors) < 30:
                        errors.append(
                            f"Linha {line_number}: quantidade de colunas diferente do cabeçalho."
                        )
                    continue
                row = {
                    (key or "").replace("\ufeff", "").strip(): (value or "")
                    for key, value in row.items()
                }
                row_date = _row_date(row)
                if row_date is None:
                    rows_errors += 1
                    if len(errors) < 30:
                        errors.append(f"Linha {line_number}: Data inválida ou vazia.")
                    continue
                if tsv_date_blocked(row_date):
                    rows_errors += 1
                    if len(errors) < 30:
                        errors.append(
                            f"Linha {line_number}: data {row_date.isoformat()} no período Intranet; "
                            "carga retroativa não pode atravessar o corte sem política explícita."
                        )
                    continue
                month_key = row_date.strftime("%Y-%m")
                bucket = by_month.setdefault(
                    month_key,
                    {
                        "competencia": month_key,
                        "rows_valid": 0,
                        "rows_errors": 0,
                        "previous_rows": None,
                        "status": "pending",
                        "errors": [],
                    },
                )
                try:
                    obj = mapper(row, source_file=batch.filename)
                except Exception as exc:
                    rows_errors += 1
                    bucket["rows_errors"] += 1
                    if len(errors) < 30:
                        errors.append(f"Linha {line_number}: {exc}")
                    continue
                if obj is None:
                    rows_errors += 1
                    bucket["rows_errors"] += 1
                    if len(errors) < 30:
                        errors.append(f"Linha {line_number}: registro sem chave utilizável.")
                    continue
                tracker.add(str(row.get("Protocolo") or "").strip())
                bucket["rows_valid"] += 1
                rows_valid += 1
            reporter.tick(rows=rows_total, bytes_read=_stream_position(fh, is_gz=is_gz))
    except UnicodeDecodeError as exc:
        errors.append(f"Arquivo não está em UTF-8: {exc}")
    except CancelAccepted:
        mark_cancelled_no_data_change(
            batch.id,
            phase="Validação cancelada (nenhuma alteração na base)",
        )
        batch.refresh_from_db()
        return batch
    except (OSError, csv.Error, MonthlyImportError) as exc:
        errors.append(str(exc))
    finally:
        distinct_protocols, duplicate_protocol_rows = tracker.totals()
        tracker.close()

    if is_cancel_pending(batch.id):
        mark_cancelled_no_data_change(
            batch.id,
            phase="Validação cancelada (nenhuma alteração na base)",
        )
        batch.refresh_from_db()
        return batch

    month_plan = []
    for month_key in sorted(by_month.keys()):
        bucket = by_month[month_key]
        start = date.fromisoformat(f"{month_key}-01")
        start_b, end_b = _month_bounds(start)
        previous = _legacy_month_qs(model, start_b, end_b).count()
        bucket["previous_rows"] = previous
        if tsv_competencia_blocked(start):
            bucket["status"] = "error"
            bucket["situation"] = "Competência coberta pela Intranet"
            errors.append(
                f"Competência {month_key} está no período Intranet; "
                "remova essas linhas ou use override administrativo."
            )
        elif bucket["rows_valid"] <= 0:
            bucket["status"] = "error"
            bucket["situation"] = "Sem linhas válidas"
        elif bucket["rows_errors"] and not bucket["rows_valid"]:
            bucket["status"] = "error"
            bucket["situation"] = "Erros encontrados"
        elif previous > 0:
            bucket["status"] = "replace"
            bucket["situation"] = "Será substituído"
        else:
            bucket["status"] = "ready"
            bucket["situation"] = "Pronto"
        month_plan.append(bucket)

    if duplicate_protocol_rows:
        warnings.append(
            f"{duplicate_protocol_rows:,} linha(s) repetem protocolo já visto no arquivo."
        )
    if not month_plan:
        errors.append("Nenhuma competência válida foi encontrada no arquivo.")
    if rows_errors and not rows_valid:
        errors.append("Nenhuma linha válida foi encontrada.")

    est_bytes = rows_valid * BYTES_PER_ROW_ESTIMATE
    warnings.append(
        f"Estimativa de espaço bruto da carga: ~{est_bytes / (1024 ** 3):.2f} GB "
        f"(≈{BYTES_PER_ROW_ESTIMATE} B/linha × {rows_valid:,}). "
        "Protocolos contados via SQLite em disco (sem set global em RAM)."
    )

    batch.rows_total = rows_total
    batch.rows_valid = rows_valid
    batch.rows_errors = rows_errors
    batch.rows_outside_period = 0
    batch.distinct_protocols = distinct_protocols
    batch.duplicate_protocol_rows = duplicate_protocol_rows
    batch.previous_rows = sum(int(m.get("previous_rows") or 0) for m in month_plan)
    batch.month_plan = month_plan
    batch.months_total = len(month_plan)
    batch.months_done = 0
    batch.warnings = warnings
    batch.errors = errors
    batch.validated_at = timezone.now()
    batch.rows_processed = rows_total
    batch.bytes_processed = file_size if not is_gz else max(batch.bytes_processed, reporter.bytes_read)
    batch.heartbeat_at = timezone.now()
    batch.processing_rate = reporter.rows / max(0.001, time.monotonic() - reporter.started)
    batch.estimated_seconds_remaining = 0
    batch.progress_indeterminate = False
    if month_plan:
        batch.competencia = date.fromisoformat(f"{month_plan[0]['competencia']}-01")
    if errors or (rows_errors and not rows_valid):
        new_status = QualidadeImportBatch.STATUS_FAILED
        batch.phase = "Validação retroativa reprovada"
        batch.progress_percent = max(batch.progress_percent or 0, 100)
        batch.current_stage = "failed"
        batch.failure_detail = errors[0] if errors else f"{rows_errors} linha(s) inválida(s)."
        batch.failure_code = "VALIDATION_FAILED"
        batch.last_error_at = timezone.now()
    else:
        new_status = QualidadeImportBatch.STATUS_VALIDATED
        batch.phase = (
            f"Validação concluída — {rows_total:,} linhas, {len(month_plan)} competência(s)"
        ).replace(",", ".")
        batch.progress_percent = 100
        batch.current_stage = "validated"
        batch.failure_detail = ""
        batch.failure_code = ""

    # Não sobrescrever cancelamento / falha stale / token de outro worker
    updated = QualidadeImportBatch.objects.filter(
        pk=batch.pk,
        status=QualidadeImportBatch.STATUS_VALIDATING,
    ).update(
        status=new_status,
        phase=batch.phase,
        progress_percent=batch.progress_percent,
        current_stage=batch.current_stage,
        failure_detail=batch.failure_detail,
        failure_code=batch.failure_code,
        last_error_at=batch.last_error_at if new_status == QualidadeImportBatch.STATUS_FAILED else None,
        rows_total=batch.rows_total,
        rows_valid=batch.rows_valid,
        rows_errors=batch.rows_errors,
        rows_outside_period=0,
        distinct_protocols=batch.distinct_protocols,
        duplicate_protocol_rows=batch.duplicate_protocol_rows,
        previous_rows=batch.previous_rows,
        month_plan=batch.month_plan,
        months_total=batch.months_total,
        months_done=0,
        warnings=batch.warnings,
        errors=batch.errors,
        validated_at=batch.validated_at,
        rows_processed=batch.rows_processed,
        bytes_processed=batch.bytes_processed,
        heartbeat_at=batch.heartbeat_at,
        processing_rate=batch.processing_rate,
        estimated_seconds_remaining=0,
        progress_indeterminate=False,
        competencia=batch.competencia,
        worker_token="",
    )
    if not updated:
        batch.refresh_from_db()
        if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
            mark_cancelled_no_data_change(
                batch.id,
                phase="Validação cancelada (nenhuma alteração na base)",
            )
            batch.refresh_from_db()
        return batch
    batch.refresh_from_db()
    return batch


def serialize_batch_extended(batch: QualidadeImportBatch) -> dict:
    payload = serialize_batch(batch)
    flags = batch_flags(batch)
    payload.update(flags)
    payload["can_resume_upload"] = flags["can_resume"]
    payload["expected_checksum"] = batch.expected_checksum
    payload["cancel_requested_at"] = (
        batch.cancel_requested_at.isoformat() if batch.cancel_requested_at else None
    )
    payload["cancelled_at"] = batch.cancelled_at.isoformat() if batch.cancelled_at else None
    payload["cancellation_reason"] = batch.cancellation_reason or ""
    payload["rollback_status"] = batch.rollback_status or ""
    payload["rollback_detail"] = batch.rollback_detail or ""
    payload["failure_code"] = batch.failure_code or ""
    payload["last_error_at"] = (
        batch.last_error_at.isoformat() if batch.last_error_at else None
    )
    payload["cancelled_by"] = (
        getattr(batch.cancelled_by, "username", None) if batch.cancelled_by_id else None
    )
    return payload


def _backup_month_for(batch: QualidadeImportBatch, competencia: date) -> tuple[Path, int]:
    model, _, _ = _config(batch.kind)
    start, end = _month_bounds(competencia)
    fields = [field for field in model._meta.concrete_fields if field.name != "id"]
    field_names = [field.name for field in fields]
    queryset = _legacy_month_qs(model, start, end).order_by("pk")
    count = queryset.count()
    target = _batch_dir(batch.id) / f"backup_{batch.kind}_{start:%Y_%m}.jsonl.gz"
    ensure_disk_space(
        _batch_dir(batch.id),
        max(count * BYTES_PER_ROW_ESTIMATE // 2, 10 * 1024 * 1024),
        label=f"backup {start:%Y-%m}",
    )
    with gzip.open(target, "wt", encoding="utf-8", newline="\n") as output:
        output.write(
            json.dumps(
                {"model": model._meta.label_lower, "fields": field_names, "rows": count},
                ensure_ascii=False,
            )
            + "\n"
        )
        for values in queryset.values_list(*field_names).iterator(chunk_size=5000):
            output.write(
                json.dumps(
                    dict(zip(field_names, values)),
                    ensure_ascii=False,
                    cls=DjangoJSONEncoder,
                )
                + "\n"
            )
    return target, count


def _iter_month_objects(batch: QualidadeImportBatch, competencia: date):
    _, _, mapper = _config(batch.kind)
    start, end = _month_bounds(competencia)
    fh, reader = _open_tsv_path(Path(batch.source_path))
    with fh:
        for line_number, row in enumerate(reader, start=2):
            row = {
                (key or "").replace("\ufeff", "").strip(): (value or "")
                for key, value in row.items()
            }
            row_date = _row_date(row)
            if row_date is None or not (start <= row_date < end):
                continue
            obj = mapper(row, source_file=batch.filename)
            if obj is None:
                raise MonthlyImportError(f"Linha {line_number} sem chave utilizável.")
            yield obj


def _iter_month_objects_for_import(batch: QualidadeImportBatch, competencia: date):
    from apps.qualidade_operacional.services.record_admin import apply_states_to_instances

    objects = list(_iter_month_objects(batch, competencia))
    if batch.kind == QualidadeImportBatch.KIND_FALHAS:
        to_import, _, _ = prepare_falhas_for_import(objects)
        return apply_states_to_instances(to_import, "falha")
    return apply_states_to_instances(objects, "auditado")


def _mark_month(plan: list[dict], competencia_key: str, **updates) -> list[dict]:
    out = []
    for item in plan:
        row = dict(item)
        if row.get("competencia") == competencia_key:
            row.update(updates)
        out.append(row)
    return out


def run_retroactive_import(batch_id: str, *, worker_token: str | None = None) -> None:
    batch = QualidadeImportBatch.objects.get(pk=batch_id)
    token = worker_token or batch.worker_token or ""
    model, _, _ = _config(batch.kind)
    try:
        raise_if_cancel_requested(batch.id)
        if _file_checksum(Path(batch.source_path)) != batch.checksum_sha256:
            raise MonthlyImportError("O arquivo mudou após a validação; envie-o novamente.")
        plan = list(batch.month_plan or [])
        total_months = len(plan)
        if not total_months:
            raise MonthlyImportError("Plano de competências vazio.")
        imported_total = batch.imported_rows or 0
        backups: list[str] = []
        if batch.backup_path:
            backups = [p for p in str(batch.backup_path).split("|") if p]

        for idx, month in enumerate(plan):
            raise_if_cancel_requested(batch.id)
            if month.get("status") in {"completed", "error", "rolled_back"}:
                continue
            competencia_key = month["competencia"]
            competencia = date.fromisoformat(f"{competencia_key}-01")
            expected = int(month.get("rows_valid") or 0)
            current_pct = (
                QualidadeImportBatch.objects.filter(pk=batch.id)
                .values_list("progress_percent", flat=True)
                .first()
                or 0
            )
            pct = max(current_pct, 10 + int(80 * idx / max(total_months, 1)))

            raise_if_cancel_requested(batch.id)
            updated = conditional_batch_update(
                batch.id,
                expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
                require_token=token or None,
                current_competencia=competencia_key,
                months_done=idx,
                months_total=total_months,
                phase=f"Competência {competencia_key}: backup",
                progress_percent=pct,
                current_stage="backup",
                heartbeat_at=timezone.now(),
            )
            if not updated:
                raise_if_cancel_requested(batch.id)
                raise MonthlyImportError("Lote não está mais em processing (fencing).")

            raise_if_cancel_requested(batch.id)
            backup_path, previous_rows = _backup_month_for(batch, competencia)
            backups.append(str(backup_path))
            conditional_batch_update(
                batch.id,
                expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
                require_token=token or None,
                backup_path="|".join(backups),
                phase=f"Competência {competencia_key}: substituindo",
                progress_percent=min(95, max(pct, pct + 3)),
                current_stage="load",
                heartbeat_at=timezone.now(),
            )

            raise_if_cancel_requested(batch.id)
            start, end = _month_bounds(competencia)
            if tsv_competencia_blocked(competencia):
                raise MonthlyImportError(
                    f"{competencia_key}: competência coberta pela Intranet; TSV bloqueado."
                )
            with transaction.atomic():
                model.all_objects.filter(data__gte=start, data__lt=end).exclude(
                    source_file=INTRANET_SOURCE_FILE
                ).delete()
                imported = _bulk_from_iter(
                    model, _iter_month_objects_for_import(batch, competencia)
                )
                if imported != expected:
                    raise MonthlyImportError(
                        f"{competencia_key}: importado {imported} ≠ validado {expected}."
                    )
            imported_total += imported
            plan = _mark_month(
                plan,
                competencia_key,
                status="completed",
                situation="Concluído",
                imported_rows=imported,
                previous_rows=previous_rows,
                backup_path=str(backup_path),
            )
            conditional_batch_update(
                batch.id,
                expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
                require_token=token or None,
                month_plan=plan,
                months_done=idx + 1,
                imported_rows=imported_total,
                phase=f"Competência {competencia_key} concluída",
                progress_percent=min(
                    95, max(pct, 10 + int(80 * (idx + 1) / max(total_months, 1)))
                ),
                current_stage="reconcile",
                heartbeat_at=timezone.now(),
                rows_processed=imported_total,
            )

        raise_if_cancel_requested(batch.id)
        warnings = list(batch.warnings)
        try:
            refresh_filter_catalog()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Dados importados, mas o catálogo/cache não atualizou: {exc}")
        updated = conditional_batch_update(
            batch.id,
            expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
            require_token=token or None,
            status=QualidadeImportBatch.STATUS_COMPLETED,
            phase="Carga retroativa concluída",
            progress_percent=100,
            imported_rows=imported_total,
            months_done=total_months,
            warnings=warnings,
            finished_at=timezone.now(),
            failure_detail="",
            failure_code="",
            current_competencia="",
            current_stage="completed",
            heartbeat_at=timezone.now(),
            estimated_seconds_remaining=0,
            worker_token="",
        )
        if not updated:
            logger.warning(
                "Import concluiu mas status final não aplicado (batch=%s token=%s)",
                batch.id,
                token,
            )
    except CancelAccepted as exc:
        batch.refresh_from_db()
        if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
            process_cancel_if_needed(batch, worker_token=token or None)
        else:
            logger.info(
                "Worker interrompido por estado terminal batch=%s status=%s (%s)",
                batch.id,
                batch.status,
                exc,
            )
    except Exception as exc:  # noqa: BLE001
        now = timezone.now()
        conditional_batch_update(
            batch.id,
            expected_statuses={QualidadeImportBatch.STATUS_PROCESSING},
            require_token=token or None,
            status=QualidadeImportBatch.STATUS_FAILED,
            phase="Carga retroativa interrompida; competências concluídas preservadas",
            progress_percent=100,
            failure_detail=str(exc)[:1000],
            failure_code="IMPORT_FAILED",
            last_error_at=now,
            finished_at=now,
            current_stage="failed",
            heartbeat_at=now,
            worker_token="",
        )


def start_retroactive_import(batch: QualidadeImportBatch) -> None:
    token = new_worker_token()
    try:
        with transaction.atomic():
            claimed = QualidadeImportBatch.objects.filter(
                pk=batch.pk,
                status=QualidadeImportBatch.STATUS_VALIDATED,
                import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            ).update(
                status=QualidadeImportBatch.STATUS_PROCESSING,
                phase="Carga retroativa agendada",
                progress_percent=5,
                current_stage="queued",
                heartbeat_at=timezone.now(),
                stage_started_at=timezone.now(),
                worker_token=token,
                failure_detail="",
                failure_code="",
            )
            if not claimed:
                raise MonthlyImportError("Este lote não está mais disponível para confirmação.")
    except IntegrityError as exc:
        raise MonthlyImportError(
            "Já existe uma carga retroativa em andamento para essa base."
        ) from exc
    spawn_qualidade_import_worker()


def run_retroactive_restore(batch_id: str) -> None:
    from apps.qualidade_operacional.services.monthly_import import _backup_paths

    batch = QualidadeImportBatch.objects.get(pk=batch_id)
    model, _, _ = _config(batch.kind)
    try:
        plan = list(batch.month_plan or [])
        backups = _backup_paths(batch)
        if not backups:
            raise MonthlyImportError("Backup não encontrado no servidor.")
        month_backups: list[tuple[str, Path, int]] = []
        for month in plan:
            if month.get("status") != "completed":
                continue
            path_raw = month.get("backup_path") or ""
            if not path_raw:
                continue
            path = Path(path_raw)
            if not path.exists():
                raise MonthlyImportError(f"Backup ausente: {path.name}")
            month_backups.append((month["competencia"], path, int(month.get("previous_rows") or 0)))
        if not month_backups:
            for path in backups:
                if not path.exists():
                    raise MonthlyImportError(f"Backup ausente: {path.name}")
                stem = path.name.replace(".jsonl.gz", "")
                parts = stem.split("_")
                year_month = f"{parts[-2]}-{parts[-1]}" if len(parts) >= 2 else ""
                month_backups.append((year_month, path, -1))

        total = len(month_backups)
        for idx, (competencia_key, backup_path, previous_rows) in enumerate(month_backups):
            QualidadeImportBatch.objects.filter(pk=batch.id).update(
                current_competencia=competencia_key,
                phase=f"Restaurando {competencia_key}",
                progress_percent=10 + int(80 * idx / max(total, 1)),
                current_stage="restore",
                heartbeat_at=timezone.now(),
            )
            competencia = date.fromisoformat(f"{competencia_key}-01")
            start, end = _month_bounds(competencia)
            with transaction.atomic():
                _legacy_month_qs(model, start, end).delete()
                restored = _bulk_from_iter(model, _restore_objects(model, backup_path))
                if previous_rows >= 0 and restored != previous_rows:
                    raise MonthlyImportError(
                        f"{competencia_key}: restaurado {restored} ≠ backup {previous_rows}."
                    )
        warnings = list(batch.warnings)
        try:
            refresh_filter_catalog()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Backup restaurado, mas o catálogo/cache não atualizou: {exc}")
        QualidadeImportBatch.objects.filter(pk=batch.id).update(
            status=QualidadeImportBatch.STATUS_RESTORED,
            phase="Carga retroativa restaurada",
            progress_percent=100,
            warnings=warnings,
            finished_at=timezone.now(),
            failure_detail="",
            current_competencia="",
            current_stage="restored",
            heartbeat_at=timezone.now(),
        )
    except Exception as exc:  # noqa: BLE001
        QualidadeImportBatch.objects.filter(pk=batch.id).update(
            status=QualidadeImportBatch.STATUS_FAILED,
            phase="Restauração retroativa falhou",
            progress_percent=100,
            failure_detail=str(exc)[:1000],
            finished_at=timezone.now(),
            current_stage="failed",
            heartbeat_at=timezone.now(),
        )


def start_retroactive_restore(batch: QualidadeImportBatch) -> None:
    try:
        with transaction.atomic():
            claimed = QualidadeImportBatch.objects.filter(
                pk=batch.pk,
                status__in={
                    QualidadeImportBatch.STATUS_COMPLETED,
                    QualidadeImportBatch.STATUS_FAILED,
                    QualidadeImportBatch.STATUS_ROLLBACK_FAILED,
                },
                import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
            ).update(
                status=QualidadeImportBatch.STATUS_RESTORING,
                phase="Restauração retroativa agendada",
                progress_percent=5,
                current_stage="restore",
                heartbeat_at=timezone.now(),
                worker_token=new_worker_token(),
                failure_detail="",
                failure_code="",
            )
            if not claimed:
                raise MonthlyImportError("Este lote não está disponível para restauração.")
    except IntegrityError as exc:
        raise MonthlyImportError(
            "Já existe uma operação retroativa em andamento para essa base."
        ) from exc
    threading.Thread(target=_thread_restore, args=(str(batch.pk),), daemon=True).start()


def _thread_restore(batch_id: str) -> None:
    close_old_connections()
    try:
        run_retroactive_restore(batch_id)
    finally:
        close_old_connections()


def _stale_cutoff_minutes(batch: QualidadeImportBatch, *, minutes: int, cancel_minutes: int):
    if batch.status == QualidadeImportBatch.STATUS_CANCEL_REQUESTED:
        return timezone.now() - timedelta(minutes=cancel_minutes)
    return timezone.now() - timedelta(minutes=minutes)


def _batch_heartbeat_stale(batch: QualidadeImportBatch, cutoff) -> bool:
    hb = batch.heartbeat_at
    if hb is not None:
        return hb < cutoff
    ref = batch.stage_started_at or batch.created_at
    return bool(ref and ref < cutoff)


def _finalize_stale_batch(
    batch: QualidadeImportBatch,
    *,
    minutes: int,
    cancel_minutes: int,
) -> bool:
    """Finaliza lote sem worker vivo. Retorna True se alterou o lote."""
    active_statuses = {
        QualidadeImportBatch.STATUS_PROCESSING,
        QualidadeImportBatch.STATUS_RESTORING,
        QualidadeImportBatch.STATUS_VALIDATING,
        QualidadeImportBatch.STATUS_CANCELLING,
        QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
    }
    if batch.status not in active_statuses:
        return False

    cutoff = _stale_cutoff_minutes(
        batch, minutes=minutes, cancel_minutes=cancel_minutes
    )
    if not _batch_heartbeat_stale(batch, cutoff):
        return False

    stale_label = cancel_minutes if batch.status == QualidadeImportBatch.STATUS_CANCEL_REQUESTED else minutes

    if batch.status == QualidadeImportBatch.STATUS_CANCEL_REQUESTED:
        if not completed_months_needing_rollback(batch):
            mark_cancelled_no_data_change(
                batch.pk,
                phase="Carga cancelada (worker parado — nenhuma competência alterada)",
            )
            QualidadeImportBatch.objects.filter(
                pk=batch.pk, status=QualidadeImportBatch.STATUS_CANCELLED
            ).update(worker_token="")
        else:
            execute_cancel_rollback(str(batch.pk), worker_token=None)
        logger.info(
            "finalize_stale: cancel_requested batch=%s stale_min=%s",
            batch.pk,
            stale_label,
        )
        return True

    if batch.status == QualidadeImportBatch.STATUS_CANCELLING:
        QualidadeImportBatch.objects.filter(
            pk=batch.pk,
            status=QualidadeImportBatch.STATUS_CANCELLING,
        ).update(
            status=QualidadeImportBatch.STATUS_ROLLBACK_FAILED,
            phase="Rollback interrompido (worker parado — sem heartbeat)",
            failure_detail=f"Sem heartbeat há mais de {stale_label} minutos durante cancelling.",
            failure_code="STALE_HEARTBEAT_ROLLBACK",
            last_error_at=timezone.now(),
            finished_at=timezone.now(),
            progress_percent=100,
            current_stage="rollback_failed",
            worker_token="",
            rollback_status=QualidadeImportBatch.ROLLBACK_FAILED,
            rollback_detail="Worker interrompido durante rollback; intervenção necessária.",
        )
        return True

    QualidadeImportBatch.objects.filter(
        pk=batch.pk,
        status=batch.status,
    ).update(
        status=QualidadeImportBatch.STATUS_FAILED,
        phase="Operação marcada como falha (worker parado — sem heartbeat)",
        failure_detail=(
            f"Sem heartbeat há mais de {stale_label} minutos "
            f"(stage={batch.current_stage or '—'}, "
            f"competência={batch.current_competencia or '—'}). "
            "Consulte o lote pelo UUID antes de reenviar ou apagar."
        ),
        failure_code="STALE_HEARTBEAT",
        last_error_at=timezone.now(),
        finished_at=timezone.now(),
        progress_percent=100,
        current_stage="failed",
        worker_token="",
    )
    return True


def reconcile_stale_qualidade_batch(batch: QualidadeImportBatch) -> QualidadeImportBatch:
    """Reconcilia um lote preso (ex.: cancel_requested sem worker) antes de responder GET."""
    minutes = int(getattr(settings, "QUALIDADE_IMPORT_STALE_MINUTES", STALE_MINUTES_DEFAULT))
    cancel_minutes = int(
        getattr(
            settings,
            "QUALIDADE_IMPORT_CANCEL_STALE_MINUTES",
            CANCEL_STALE_MINUTES_DEFAULT,
        )
    )
    if _finalize_stale_batch(batch, minutes=minutes, cancel_minutes=cancel_minutes):
        batch.refresh_from_db()
    return batch


def reconcile_stale_qualidade_batches_for_kind(kind: str) -> int:
    """Libera lock retroativo da base quando o worker morreu durante cancel/operação."""
    minutes = int(getattr(settings, "QUALIDADE_IMPORT_STALE_MINUTES", STALE_MINUTES_DEFAULT))
    cancel_minutes = int(
        getattr(
            settings,
            "QUALIDADE_IMPORT_CANCEL_STALE_MINUTES",
            CANCEL_STALE_MINUTES_DEFAULT,
        )
    )
    active = QualidadeImportBatch.objects.filter(
        kind=kind,
        import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
        status__in={
            QualidadeImportBatch.STATUS_UPLOADING,
            QualidadeImportBatch.STATUS_VALIDATING,
            QualidadeImportBatch.STATUS_PROCESSING,
            QualidadeImportBatch.STATUS_RESTORING,
            QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            QualidadeImportBatch.STATUS_CANCELLING,
        },
    )
    count = 0
    for batch in active:
        if _finalize_stale_batch(batch, minutes=minutes, cancel_minutes=cancel_minutes):
            count += 1
    return count


def finalize_stale_qualidade_batches(*, minutes: int | None = None) -> int:
    """Marca como falha/cancelado lotes sem heartbeat recente (não usa created_at sozinho).

    cancel_requested usa limiar menor (QUALIDADE_IMPORT_CANCEL_STALE_MINUTES) para
    não bloquear nova carga retroativa quando o worker morre após pedido de cancelamento.
    Heartbeat recente = operação viva. Zera worker_token para fencing.
    """
    minutes = minutes or int(
        getattr(settings, "QUALIDADE_IMPORT_STALE_MINUTES", STALE_MINUTES_DEFAULT)
    )
    cancel_minutes = int(
        getattr(
            settings,
            "QUALIDADE_IMPORT_CANCEL_STALE_MINUTES",
            CANCEL_STALE_MINUTES_DEFAULT,
        )
    )
    cutoff = timezone.now() - timedelta(minutes=minutes)
    cancel_cutoff = timezone.now() - timedelta(minutes=cancel_minutes)
    candidates = QualidadeImportBatch.objects.filter(
        status__in={
            QualidadeImportBatch.STATUS_PROCESSING,
            QualidadeImportBatch.STATUS_RESTORING,
            QualidadeImportBatch.STATUS_VALIDATING,
            QualidadeImportBatch.STATUS_CANCELLING,
            QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
        },
    ).filter(
        Q(status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED, heartbeat_at__lt=cancel_cutoff)
        | Q(
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            heartbeat_at__isnull=True,
            stage_started_at__lt=cancel_cutoff,
        )
        | Q(
            status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
            heartbeat_at__isnull=True,
            stage_started_at__isnull=True,
            created_at__lt=cancel_cutoff,
        )
        | Q(
            ~Q(status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED),
            heartbeat_at__isnull=False,
            heartbeat_at__lt=cutoff,
        )
        | Q(
            ~Q(status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED),
            heartbeat_at__isnull=True,
            stage_started_at__lt=cutoff,
        )
        | Q(
            ~Q(status=QualidadeImportBatch.STATUS_CANCEL_REQUESTED),
            heartbeat_at__isnull=True,
            stage_started_at__isnull=True,
            created_at__lt=cutoff,
        )
    )
    count = 0
    for batch in candidates:
        if _finalize_stale_batch(batch, minutes=minutes, cancel_minutes=cancel_minutes):
            count += 1
    return count


def claim_next_qualidade_job() -> QualidadeImportBatch | None:
    with transaction.atomic():
        batch = (
            QualidadeImportBatch.objects.select_for_update(skip_locked=True)
            .filter(
                import_mode=QualidadeImportBatch.MODE_RETROACTIVE,
                status__in={
                    QualidadeImportBatch.STATUS_VALIDATING,
                    QualidadeImportBatch.STATUS_PROCESSING,
                    QualidadeImportBatch.STATUS_CANCEL_REQUESTED,
                    QualidadeImportBatch.STATUS_CANCELLING,
                },
            )
            .order_by("created_at")
            .first()
        )
        if batch is None:
            return None
        if batch.status == QualidadeImportBatch.STATUS_VALIDATING and not batch.upload_complete:
            return None
        token = batch.worker_token or new_worker_token()
        QualidadeImportBatch.objects.filter(pk=batch.pk).update(
            heartbeat_at=timezone.now(),
            worker_token=token,
        )
        batch.worker_token = token
        return batch


def process_qualidade_job(batch: QualidadeImportBatch) -> None:
    token = batch.worker_token or ""
    if batch.status in QualidadeImportBatch.CANCEL_PENDING_STATUSES:
        process_cancel_if_needed(batch, worker_token=token or None)
        return
    if batch.status == QualidadeImportBatch.STATUS_VALIDATING:
        try:
            validate_retroactive_batch(batch)
        except CancelAccepted:
            mark_cancelled_no_data_change(
                batch.id,
                phase="Validação cancelada (nenhuma alteração na base)",
            )
        return
    if batch.status == QualidadeImportBatch.STATUS_PROCESSING:
        run_retroactive_import(str(batch.pk), worker_token=token or None)
        return


def spawn_qualidade_import_worker(*, max_jobs: int = 3) -> bool:
    manage = Path(settings.BASE_DIR) / "manage.py"
    if not manage.exists():
        logger.warning("manage.py não encontrado; usando thread local para drain.")
        threading.Thread(target=_thread_drain, args=(max_jobs,), daemon=True).start()
        return True
    env = os.environ.copy()
    log_dir = Path(settings.BASE_DIR) / "tmp"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = open(log_dir / "qualidade_import_drain.log", "a", encoding="utf-8")
    except OSError:
        log_file = subprocess.DEVNULL
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen(  # noqa: S603
            [sys.executable, str(manage), "drain_qualidade_import", f"--max-jobs={max_jobs}"],
            cwd=str(settings.BASE_DIR),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=sys.platform != "win32",
            start_new_session=True,
        )
        return True
    except OSError as exc:
        logger.exception("Falha ao spawn drain_qualidade_import: %s", exc)
        threading.Thread(target=_thread_drain, args=(max_jobs,), daemon=True).start()
        return False


def _thread_drain(max_jobs: int) -> None:
    close_old_connections()
    try:
        drain_qualidade_imports(max_jobs=max_jobs)
    finally:
        close_old_connections()


_drain_lock_handle = None


def _drain_lock_path() -> Path:
    return Path(settings.BASE_DIR) / "tmp" / "qualidade_import_drain.lock"


def acquire_qualidade_import_drain_lock() -> bool:
    global _drain_lock_handle
    if _drain_lock_handle is not None:
        return True
    path = _drain_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _drain_lock_handle = handle
        return True
    except OSError:
        handle.close()
        return False


def release_qualidade_import_drain_lock() -> None:
    global _drain_lock_handle
    handle = _drain_lock_handle
    _drain_lock_handle = None
    if handle is None:
        return
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except OSError:
        pass


def drain_qualidade_imports(*, max_jobs: int = 5) -> int:
    if not acquire_qualidade_import_drain_lock():
        logger.info("drain_qualidade_import: outro worker já está ativo")
        return 0
    try:
        finalize_stale_qualidade_batches()
        processed = 0
        while processed < max_jobs:
            batch = claim_next_qualidade_job()
            if batch is None:
                break
            process_qualidade_job(batch)
            processed += 1
        return processed
    finally:
        release_qualidade_import_drain_lock()
