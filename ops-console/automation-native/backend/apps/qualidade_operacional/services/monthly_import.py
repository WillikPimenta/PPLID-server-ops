# -*- coding: utf-8 -*-
"""Carga mensal segura dos TSV de Qualidade Operacional."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import threading
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, close_old_connections, transaction
from django.db.models import Q
from django.utils import timezone

from apps.qualidade_operacional.models import (
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeImportBatch,
)
from apps.qualidade_operacional.services.case_key import (
    build_case_key_str,
    canonical_falha_ordering,
    compare_falha_priority,
    resolve_falha_conflict,
)
from apps.qualidade_operacional.services.falha_import_dedupe import (
    prepare_falhas_for_import,
    serialize_db_conflict,
)
from apps.qualidade_operacional.services.filter_catalog import refresh_filter_catalog
from apps.qualidade_operacional.services.importer import (
    BULK_CHUNK_SIZE,
    row_to_auditado,
    row_to_falha,
)
from apps.qualidade_operacional.services.normalize import fold_ascii_upper, parse_date_br
from apps.qualidade_operacional.services.source_config import (
    INTRANET_SOURCE_FILE,
    tsv_competencia_blocked,
    tsv_date_blocked,
)

AUDITADO_HEADERS = {
    "Data", "Data análise", "ID_Cliente", "ID_Workflow", "Tipo de análise",
    "Matricula", "Matricula auditor", "Protocolo", "Cenário", "Etapa", "STATUS",
    "IRREGULARIDADES_APONTADAS", "Cadastrado anteriormente", "ID_Operations",
    "Resultado Origem", "Resultado Destino", "Protocolo Destino", "Tipo de conclusão",
}
FALHA_HEADERS = {
    "Nome da Origem", "FRKCOLABORADOR", "Protocolo", "ID_Cliente", "ID_Workflow",
    "ID_Operations", "Tipo de análise", "FRK_GERENCIAMENTO_FLUXO", "Módulo", "Cenário",
    "Data", "Usuário do Auditor", "Matrícula", "Data de Análise", "Etapa", "Tipo de Falha",
    "UF", "Tipo de documento", "Nível de Dificuldade", "des_problemas", "Tendência",
    "Novo Resultado", "Tipo de solicitação", "Tipo_modulo", "PRKCOLABORADOR",
    "Resultado da Análise", "Número da solicitação", "Qualidade da Imagem", "ORIGEM ANÁLISE",
    "Data diferença", "tipo_modulo_2", "Categoria falha", "Localidade", "agente_ativo",
    "Líder", "tipo_falha_oficial", "nivel_de_dificuldade_confer", "Sub Segmento",
    "Segmento", "CondicaoMetrica",
}


class MonthlyImportError(ValueError):
    pass


def _header_key(value: str) -> str:
    return " ".join(fold_ascii_upper(value.replace("_", " ")).split())


def _month_bounds(competencia: date) -> tuple[date, date]:
    start = competencia.replace(day=1)
    end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, end


def _config(kind: str):
    if kind == QualidadeImportBatch.KIND_AUDITADOS:
        return QualidadeAuditado, AUDITADO_HEADERS, row_to_auditado
    if kind == QualidadeImportBatch.KIND_FALHAS:
        return QualidadeFalha, FALHA_HEADERS, row_to_falha
    raise MonthlyImportError("Tipo de base inválido.")


def _batch_dir(batch_id) -> Path:
    path = Path(settings.MEDIA_ROOT) / "qualidade_imports" / str(batch_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_uploaded_tsv(batch: QualidadeImportBatch, uploaded_file) -> Path:
    filename = Path(str(uploaded_file.name or "upload.tsv")).name
    if Path(filename).suffix.lower() != ".tsv":
        raise MonthlyImportError("Envie um arquivo com extensão .tsv.")
    max_bytes = int(getattr(settings, "QUALIDADE_IMPORT_MAX_BYTES", 750 * 1024 * 1024))
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        raise MonthlyImportError("O arquivo está vazio.")
    if size > max_bytes:
        raise MonthlyImportError(f"O arquivo excede o limite de {max_bytes // (1024 * 1024)} MB.")

    target = _batch_dir(batch.id) / "source.tsv"
    digest = hashlib.sha256()
    written = 0
    with target.open("wb") as output:
        for chunk in uploaded_file.chunks():
            output.write(chunk)
            digest.update(chunk)
            written += len(chunk)
    batch.filename = filename[:255]
    batch.source_path = str(target)
    batch.file_size = written
    batch.checksum_sha256 = digest.hexdigest()
    batch.phase = "Arquivo recebido; validando estrutura"
    batch.progress_percent = 35
    batch.save(update_fields=["filename", "source_path", "file_size", "checksum_sha256", "phase", "progress_percent"])
    return target


def _file_checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_reader(path: Path):
    fh = path.open("r", encoding="utf-8-sig", newline="")
    reader = csv.DictReader(fh, delimiter="\t")
    return fh, reader


def _row_date(row: dict[str, str]) -> date | None:
    return parse_date_br(row.get("Data"))


def validate_batch(batch: QualidadeImportBatch) -> QualidadeImportBatch:
    model, expected_headers, mapper = _config(batch.kind)
    path = Path(batch.source_path)
    start, end = _month_bounds(batch.competencia)
    if tsv_competencia_blocked(batch.competencia):
        batch.status = QualidadeImportBatch.STATUS_FAILED
        batch.phase = "Importação TSV bloqueada"
        batch.progress_percent = 100
        batch.failure_detail = (
            "Competência coberta pela fonte Intranet do Indicador de Qualidade. "
            "Períodos a partir do corte são atualizados automaticamente. "
            "Use QUALIDADE_INTRANET_TSV_OVERRIDE apenas com autorização administrativa."
        )
        batch.errors = [batch.failure_detail]
        batch.validated_at = timezone.now()
        batch.save()
        return batch
    errors: list[str] = []
    warnings: list[str] = []
    rows_total = rows_valid = rows_outside = rows_errors = 0
    protocols: set[str] = set()
    case_keys: set[str] = set()
    duplicate_protocol_rows = 0
    case_key_conflicts: list[dict] = []
    rows_importable = 0
    use_case_key = batch.kind == QualidadeImportBatch.KIND_FALHAS
    file_best_by_key: dict[str, dict] = {}
    rows_without_case_key = 0

    try:
        fh, reader = _open_reader(path)
        with fh:
            actual = {_header_key(h or "") for h in (reader.fieldnames or [])}
            missing = sorted(h for h in expected_headers if _header_key(h) not in actual)
            if missing:
                raise MonthlyImportError("Colunas obrigatórias ausentes: " + ", ".join(missing))
            for line_number, row in enumerate(reader, start=2):
                rows_total += 1
                if None in row:
                    rows_errors += 1
                    if len(errors) < 20:
                        errors.append(f"Linha {line_number}: quantidade de colunas diferente do cabeçalho.")
                    continue
                row = {(key or "").replace("\ufeff", "").strip(): (value or "") for key, value in row.items()}
                row_date = _row_date(row)
                if row_date is not None and tsv_date_blocked(row_date):
                    rows_errors += 1
                    if len(errors) < 20:
                        errors.append(
                            f"Linha {line_number}: data {row_date.isoformat()} está no período Intranet."
                        )
                    continue
                if row_date is None:
                    rows_errors += 1
                    if len(errors) < 20:
                        errors.append(f"Linha {line_number}: Data inválida ou vazia.")
                    continue
                if not (start <= row_date < end):
                    rows_outside += 1
                    if len(errors) < 20:
                        errors.append(f"Linha {line_number}: Data {row_date:%d/%m/%Y} fora da competência.")
                    continue
                try:
                    obj = mapper(row, source_file=batch.filename)
                except Exception as exc:
                    rows_errors += 1
                    if len(errors) < 20:
                        errors.append(f"Linha {line_number}: {exc}")
                    continue
                if obj is None:
                    rows_errors += 1
                    if len(errors) < 20:
                        errors.append(f"Linha {line_number}: registro sem chave utilizável.")
                    continue
                protocolo = str(row.get("Protocolo") or "").strip()
                if use_case_key:
                    case_key = getattr(obj, "case_key", "") or build_case_key_str(
                        protocolo,
                        getattr(obj, "matricula", ""),
                    )
                    if case_key and case_key in case_keys:
                        duplicate_protocol_rows += 1
                    elif case_key:
                        case_keys.add(case_key)
                    if case_key:
                        row_fields = {
                            "data": getattr(obj, "data", None),
                            "data_analise": getattr(obj, "data_analise", None),
                            "protocolo": protocolo,
                            "matricula": getattr(obj, "matricula", ""),
                        }
                        existing_file = file_best_by_key.get(case_key)
                        if existing_file is None:
                            file_best_by_key[case_key] = row_fields
                        else:
                            if compare_falha_priority(row_fields, existing_file) < 0:
                                file_best_by_key[case_key] = row_fields
                    else:
                        rows_without_case_key += 1
                elif protocolo in protocols:
                    duplicate_protocol_rows += 1
                elif protocolo:
                    protocols.add(protocolo)
                rows_valid += 1
                if not use_case_key:
                    rows_importable += 1
    except UnicodeDecodeError as exc:
        errors.append(f"Arquivo não está em UTF-8: {exc}")
    except (OSError, csv.Error, MonthlyImportError) as exc:
        errors.append(str(exc))

    if use_case_key:
        seen_conflict_keys: set[str] = set()
        for case_key, row_fields in file_best_by_key.items():
            retained_conflicts = QualidadeFalha.objects.filter(case_key=case_key).exclude(
                Q(data__gte=start, data__lt=end)
                & ~Q(source_file=INTRANET_SOURCE_FILE)
            )
            existing_db = canonical_falha_ordering(retained_conflicts).first()
            if existing_db:
                action = resolve_falha_conflict(row_fields, existing_db)
                if case_key not in seen_conflict_keys and len(case_key_conflicts) < 50:
                    case_key_conflicts.append(
                        serialize_db_conflict(
                            case_key=case_key,
                            protocolo=str(row_fields.get("protocolo") or ""),
                            matricula=str(row_fields.get("matricula") or ""),
                            incoming_data=row_fields.get("data"),
                            existing=existing_db,
                            action=action,
                        )
                    )
                    seen_conflict_keys.add(case_key)
                if action != "skip":
                    rows_importable += 1
            else:
                rows_importable += 1
        rows_importable += rows_without_case_key

    previous_rows = _legacy_month_qs(model, start, end).count()
    if duplicate_protocol_rows:
        if use_case_key:
            warnings.append(
                f"{duplicate_protocol_rows:,} linha(s) repetem protocolo+matrícula no arquivo; "
                "serão deduplicadas mantendo a auditoria mais antiga (data)."
            )
        else:
            warnings.append(
                f"{duplicate_protocol_rows:,} linha(s) repetem protocolo; "
                "serão preservadas como ocorrências distintas."
            )
    if use_case_key and case_key_conflicts:
        skipped = sum(1 for c in case_key_conflicts if c.get("action") == "skip")
        replaced = sum(1 for c in case_key_conflicts if c.get("action") == "replace")
        if skipped:
            warnings.append(
                f"{skipped:,} linha(s) ignoradas por conflito com base existente (registro mais antigo mantido)."
            )
        if replaced:
            warnings.append(
                f"{replaced:,} linha(s) substituirão registro(s) mais recente(s) na base."
            )
    importable_rows = rows_importable if use_case_key else rows_valid
    if importable_rows:
        delta = importable_rows - previous_rows
        warnings.append(
            f"A competência passará de {previous_rows:,} para {importable_rows:,} linhas ({delta:+,})."
        )
    if rows_outside:
        errors.append(f"Há {rows_outside:,} linha(s) fora da competência selecionada.")
    if not rows_valid:
        errors.append("Nenhuma linha válida foi encontrada para a competência.")

    batch.rows_total = rows_total
    batch.rows_valid = rows_valid
    batch.rows_outside_period = rows_outside
    batch.rows_errors = rows_errors
    batch.distinct_protocols = len(case_keys if use_case_key else protocols)
    batch.duplicate_protocol_rows = duplicate_protocol_rows
    batch.case_key_conflicts = case_key_conflicts
    batch.rows_importable = importable_rows if use_case_key else rows_valid
    batch.previous_rows = previous_rows
    batch.warnings = warnings
    batch.errors = errors
    batch.validated_at = timezone.now()
    if errors or rows_errors:
        batch.status = QualidadeImportBatch.STATUS_FAILED
        batch.phase = "Validação reprovada"
        batch.progress_percent = 100
        batch.failure_detail = errors[0] if errors else f"{rows_errors} linha(s) inválida(s)."
    else:
        batch.status = QualidadeImportBatch.STATUS_VALIDATED
        batch.phase = "Validação concluída; aguardando confirmação"
        batch.progress_percent = 100
        batch.failure_detail = ""
    batch.save()
    return batch


def _backup_paths(batch: QualidadeImportBatch) -> list[Path]:
    raw = str(batch.backup_path or "").strip()
    if not raw:
        return []
    return [Path(part) for part in raw.split("|") if part.strip()]


def serialize_batch(batch: QualidadeImportBatch) -> dict:
    backups = _backup_paths(batch)
    competencia = batch.competencia.strftime("%Y-%m") if batch.competencia else ""
    return {
        "id": str(batch.id),
        "kind": batch.kind,
        "kind_label": batch.get_kind_display(),
        "import_mode": getattr(batch, "import_mode", QualidadeImportBatch.MODE_MONTHLY),
        "competencia": competencia,
        "filename": batch.filename,
        "status": batch.status,
        "phase": batch.phase,
        "progress_percent": batch.progress_percent,
        "file_size": batch.file_size,
        "checksum_sha256": batch.checksum_sha256,
        "rows_total": batch.rows_total,
        "rows_valid": batch.rows_valid,
        "rows_outside_period": batch.rows_outside_period,
        "rows_errors": batch.rows_errors,
        "distinct_protocols": batch.distinct_protocols,
        "duplicate_protocol_rows": batch.duplicate_protocol_rows,
        "case_key_conflicts": getattr(batch, "case_key_conflicts", None) or [],
        "rows_importable": int(getattr(batch, "rows_importable", 0) or 0),
        "previous_rows": batch.previous_rows,
        "imported_rows": batch.imported_rows,
        "warnings": batch.warnings,
        "errors": batch.errors,
        "failure_detail": batch.failure_detail,
        "backup_available": bool(backups) and all(path.exists() for path in backups),
        "can_confirm": batch.status == QualidadeImportBatch.STATUS_VALIDATED,
        "can_restore": batch.status == QualidadeImportBatch.STATUS_COMPLETED and bool(backups),
        "month_plan": getattr(batch, "month_plan", None) or [],
        "current_competencia": getattr(batch, "current_competencia", "") or "",
        "months_done": getattr(batch, "months_done", 0) or 0,
        "months_total": getattr(batch, "months_total", 0) or 0,
        "chunks_expected": getattr(batch, "chunks_expected", 0) or 0,
        "chunks_received": getattr(batch, "chunks_received", 0) or 0,
        "bytes_received": getattr(batch, "bytes_received", 0) or 0,
        "upload_complete": bool(getattr(batch, "upload_complete", False)),
        "rows_processed": int(getattr(batch, "rows_processed", 0) or 0),
        "bytes_processed": int(getattr(batch, "bytes_processed", 0) or 0),
        "heartbeat_at": (
            batch.heartbeat_at.isoformat()
            if getattr(batch, "heartbeat_at", None)
            else None
        ),
        "processing_rate": float(getattr(batch, "processing_rate", 0) or 0),
        "estimated_seconds_remaining": getattr(batch, "estimated_seconds_remaining", None),
        "current_stage": getattr(batch, "current_stage", "") or "",
        "progress_indeterminate": bool(getattr(batch, "progress_indeterminate", False)),
        "stage_started_at": (
            batch.stage_started_at.isoformat()
            if getattr(batch, "stage_started_at", None)
            else None
        ),
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "validated_at": batch.validated_at.isoformat() if batch.validated_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
    }


def _set_progress(batch_id, percent: int, phase: str, **extra) -> None:
    payload = {"progress_percent": percent, "phase": phase, **extra}
    QualidadeImportBatch.objects.filter(pk=batch_id).update(**payload)


def _legacy_month_qs(model, start: date, end: date):
    """Queryset de fatos TSV do mês — nunca inclui projeções Intranet."""
    return model.all_objects.filter(data__gte=start, data__lt=end).exclude(
        source_file=INTRANET_SOURCE_FILE
    )


def _backup_month(batch: QualidadeImportBatch) -> tuple[Path, int]:
    model, _, _ = _config(batch.kind)
    start, end = _month_bounds(batch.competencia)
    fields = [field for field in model._meta.concrete_fields if field.name != "id"]
    field_names = [field.name for field in fields]
    queryset = _legacy_month_qs(model, start, end).order_by("pk")
    count = queryset.count()
    target = _batch_dir(batch.id) / f"backup_{batch.kind}_{start:%Y_%m}.jsonl.gz"
    with gzip.open(target, "wt", encoding="utf-8", newline="\n") as output:
        output.write(json.dumps({"model": model._meta.label_lower, "fields": field_names, "rows": count}, ensure_ascii=False) + "\n")
        for values in queryset.values_list(*field_names).iterator(chunk_size=BULK_CHUNK_SIZE):
            output.write(json.dumps(dict(zip(field_names, values)), ensure_ascii=False, cls=DjangoJSONEncoder) + "\n")
    return target, count


def _iter_source_objects(batch: QualidadeImportBatch):
    _, _, mapper = _config(batch.kind)
    start, end = _month_bounds(batch.competencia)
    fh, reader = _open_reader(Path(batch.source_path))
    with fh:
        for line_number, row in enumerate(reader, start=2):
            row = {(key or "").replace("\ufeff", "").strip(): (value or "") for key, value in row.items()}
            row_date = _row_date(row)
            if row_date is None or not (start <= row_date < end):
                raise MonthlyImportError(f"Linha {line_number} divergiu da validação inicial.")
            obj = mapper(row, source_file=batch.filename)
            if obj is None:
                raise MonthlyImportError(f"Linha {line_number} sem chave utilizável.")
            yield obj


def _bulk_from_iter(model, objects) -> int:
    buffer = []
    total = 0
    for obj in objects:
        buffer.append(obj)
        if len(buffer) >= BULK_CHUNK_SIZE:
            model.all_objects.bulk_create(buffer, batch_size=BULK_CHUNK_SIZE)
            total += len(buffer)
            buffer = []
    if buffer:
        model.all_objects.bulk_create(buffer, batch_size=BULK_CHUNK_SIZE)
        total += len(buffer)
    return total


def _iter_import_objects(batch: QualidadeImportBatch):
    from apps.qualidade_operacional.services.record_admin import apply_states_to_instances

    raw = list(_iter_source_objects(batch))
    if batch.kind != QualidadeImportBatch.KIND_FALHAS:
        return apply_states_to_instances(raw, "auditado")
    to_import, _, _ = prepare_falhas_for_import(raw)
    return apply_states_to_instances(to_import, "falha")


def run_import(batch_id: str) -> None:
    batch = QualidadeImportBatch.objects.get(pk=batch_id)
    model, _, _ = _config(batch.kind)
    try:
        if tsv_competencia_blocked(batch.competencia):
            raise MonthlyImportError(
                "Competência coberta pela fonte Intranet; importação TSV bloqueada."
            )
        _set_progress(batch.id, 10, "Conferindo integridade do arquivo")
        if _file_checksum(Path(batch.source_path)) != batch.checksum_sha256:
            raise MonthlyImportError("O arquivo mudou após a validação; envie-o novamente.")
        _set_progress(batch.id, 25, "Criando backup da competência atual")
        backup_path, previous_rows = _backup_month(batch)
        QualidadeImportBatch.objects.filter(pk=batch.id).update(
            backup_path=str(backup_path), previous_rows=previous_rows
        )
        _set_progress(batch.id, 55, "Substituindo a competência em transação")
        start, end = _month_bounds(batch.competencia)
        with transaction.atomic():
            _legacy_month_qs(model, start, end).delete()
            expected = (
                int(batch.rows_importable)
                if batch.kind == QualidadeImportBatch.KIND_FALHAS
                else int(batch.rows_valid)
            )
            imported = _bulk_from_iter(model, _iter_import_objects(batch))
            if imported != expected:
                raise MonthlyImportError(
                    f"Volume importado ({imported}) diverge do validado ({expected})."
                )
        _set_progress(batch.id, 90, "Atualizando filtros e cache")
        warnings = list(batch.warnings)
        try:
            refresh_filter_catalog()
        except Exception as exc:
            warnings.append(f"Dados importados, mas o catálogo/cache não atualizou: {exc}")
        QualidadeImportBatch.objects.filter(pk=batch.id).update(
            status=QualidadeImportBatch.STATUS_COMPLETED,
            phase="Importação concluída",
            progress_percent=100,
            imported_rows=imported,
            warnings=warnings,
            finished_at=timezone.now(),
            failure_detail="",
        )
    except Exception as exc:
        QualidadeImportBatch.objects.filter(pk=batch.id).update(
            status=QualidadeImportBatch.STATUS_FAILED,
            phase="Importação falhou; dados originais preservados",
            progress_percent=100,
            failure_detail=str(exc)[:1000],
            finished_at=timezone.now(),
        )


def _restore_objects(model, backup_path: Path):
    fields = {field.name: field for field in model._meta.concrete_fields if field.name != "id"}
    with gzip.open(backup_path, "rt", encoding="utf-8") as source:
        meta = json.loads(source.readline())
        if meta.get("model") != model._meta.label_lower:
            raise MonthlyImportError("O backup não pertence à tabela selecionada.")
        for line in source:
            raw = json.loads(line)
            yield model(**{name: fields[name].to_python(value) for name, value in raw.items() if name in fields})


def run_restore(batch_id: str) -> None:
    batch = QualidadeImportBatch.objects.get(pk=batch_id)
    model, _, _ = _config(batch.kind)
    try:
        backup_path = Path(batch.backup_path)
        if not backup_path.exists():
            raise MonthlyImportError("Backup não encontrado no servidor.")
        _set_progress(batch.id, 35, "Restaurando backup da competência")
        start, end = _month_bounds(batch.competencia)
        with transaction.atomic():
            _legacy_month_qs(model, start, end).delete()
            restored = _bulk_from_iter(model, _restore_objects(model, backup_path))
            if restored != batch.previous_rows:
                raise MonthlyImportError(
                    f"Volume restaurado ({restored}) diverge do backup ({batch.previous_rows})."
                )
        _set_progress(batch.id, 90, "Atualizando filtros e cache")
        warnings = list(batch.warnings)
        try:
            refresh_filter_catalog()
        except Exception as exc:
            warnings.append(f"Backup restaurado, mas o catálogo/cache não atualizou: {exc}")
        QualidadeImportBatch.objects.filter(pk=batch.id).update(
            status=QualidadeImportBatch.STATUS_RESTORED,
            phase="Backup restaurado",
            progress_percent=100,
            warnings=warnings,
            finished_at=timezone.now(),
            failure_detail="",
        )
    except Exception as exc:
        QualidadeImportBatch.objects.filter(pk=batch.id).update(
            status=QualidadeImportBatch.STATUS_FAILED,
            phase="Restauração falhou",
            progress_percent=100,
            failure_detail=str(exc)[:1000],
            finished_at=timezone.now(),
        )


def _thread_target(function, batch_id: str) -> None:
    close_old_connections()
    try:
        function(batch_id)
    finally:
        close_old_connections()


def _schedule(function, batch_id) -> None:
    threading.Thread(target=_thread_target, args=(function, str(batch_id)), daemon=True).start()


def start_import(batch: QualidadeImportBatch) -> None:
    if getattr(batch, "import_mode", QualidadeImportBatch.MODE_MONTHLY) == QualidadeImportBatch.MODE_RETROACTIVE:
        from apps.qualidade_operacional.services.retroactive_import import start_retroactive_import

        start_retroactive_import(batch)
        return
    try:
        with transaction.atomic():
            claimed = QualidadeImportBatch.objects.filter(
                pk=batch.pk,
                status=QualidadeImportBatch.STATUS_VALIDATED,
                import_mode=QualidadeImportBatch.MODE_MONTHLY,
            ).update(
                status=QualidadeImportBatch.STATUS_PROCESSING,
                phase="Importação agendada",
                progress_percent=5,
            )
            if not claimed:
                raise MonthlyImportError("Este lote não está mais disponível para confirmação.")
    except IntegrityError as exc:
        raise MonthlyImportError("Já existe uma carga em andamento para essa base e competência.") from exc
    _schedule(run_import, batch.pk)


def start_restore(batch: QualidadeImportBatch) -> None:
    if getattr(batch, "import_mode", QualidadeImportBatch.MODE_MONTHLY) == QualidadeImportBatch.MODE_RETROACTIVE:
        from apps.qualidade_operacional.services.retroactive_import import start_retroactive_restore

        start_retroactive_restore(batch)
        return
    try:
        with transaction.atomic():
            claimed = QualidadeImportBatch.objects.filter(
                pk=batch.pk,
                status=QualidadeImportBatch.STATUS_COMPLETED,
                import_mode=QualidadeImportBatch.MODE_MONTHLY,
            ).update(
                status=QualidadeImportBatch.STATUS_RESTORING,
                phase="Restauração agendada",
                progress_percent=5,
            )
            if not claimed:
                raise MonthlyImportError("Este lote não está disponível para restauração.")
    except IntegrityError as exc:
        raise MonthlyImportError("Já existe uma operação em andamento para essa competência.") from exc
    _schedule(run_restore, batch.pk)
