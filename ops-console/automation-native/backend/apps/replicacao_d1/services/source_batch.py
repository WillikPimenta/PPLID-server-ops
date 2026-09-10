"""Ingestão normalizada da fonte D-1 diretamente no PostgreSQL."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Iterable

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.replicacao_d1.models import ReplicacaoD1FonteLote, ReplicacaoD1FonteRegistro
from apps.replicacao_d1.normalization import normalize_key, normalize_protocolo


@dataclass(frozen=True)
class SourceBatchResult:
    batch: ReplicacaoD1FonteLote
    created: bool


def _column_key(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return "".join(char for char in text.casefold() if char.isalnum())


def _first(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    normalized = {_column_key(key): value for key, value in row.items()}
    for key in keys:
        normalized_key = _column_key(key)
        if normalized_key in normalized:
            return normalized[normalized_key]
    return None


def has_source_contract(columns: Iterable[Any]) -> bool:
    """Reconhece o schema bruto, incluindo cabeçalhos legados com mojibake."""
    keys = {_column_key(column) for column in columns}
    protocol_ok = "protocolo" in keys
    workflow_ok = "workflow" in keys
    analysis_date_ok = bool(keys & {"datadeanalise", "datadaanálise", "datadaanalise", "datadeanlise"})
    return protocol_ok and workflow_ok and analysis_date_ok


def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        comparison = value != value
        if isinstance(comparison, bool):
            return comparison
        return bool(comparison)
    except (TypeError, ValueError):
        return type(value).__name__ in {"NAType", "NaTType"}


def _as_text(value: Any) -> str:
    return "" if _is_missing(value) else str(value).strip()


def _as_datetime(value: Any) -> datetime | None:
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    else:
        text = str(value or "").strip()
        if not text:
            return None
        parsed = parse_datetime(text)
        if parsed is None:
            parsed_date = parse_date(text[:10])
            if parsed_date is None:
                for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
                    try:
                        parsed = datetime.strptime(text, fmt)
                        break
                    except ValueError:
                        continue
        if parsed is None:
            return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _matricula_tipo(value: Any) -> str:
    text = _as_text(value).casefold()
    if text in {"0", "0.0", "manual", "m"}:
        return ReplicacaoD1FonteRegistro.MATRICULA_MANUAL
    if text in {"1", "1.0", "automatico", "automático", "a"}:
        return ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA

    # Mantém exatamente a classificação usada pela limpeza da Rotina:
    # matrícula/e-mail corporativo = manual; demais origens = automático.
    normalized = _as_text(value).upper()
    for suffix in ("@BR.EXPERIAN.COM.BR", "@BR.EXPERIAN.COM", "@BRFLOW.COM.BR", "@BRFLOW.COM"):
        normalized = normalized.replace(suffix, "")
    normalized = normalized.replace(".", "")
    valid_prefixes = {"A", "C", "EM", "ET", "Q"}
    is_current = (
        len(normalized) == 7
        and normalized[:1] in valid_prefixes
        and normalized[1:6].isdigit()
        and normalized[-1:] in valid_prefixes
    )
    is_legacy = (
        len(normalized) == 7
        and normalized[:2] in valid_prefixes
        and normalized[-5:].isdigit()
    )
    if is_current or is_legacy:
        return ReplicacaoD1FonteRegistro.MATRICULA_MANUAL
    return ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA


def classify_matricula_tipo(value: Any) -> str:
    """Classifica matrícula como manual ou automático (mesma regra da fonte D-1)."""
    return _matricula_tipo(value)


def matricula_tipo_q(tipo: str, *, field: str = "matricula") -> Q | None:
    """Filtro ORM equivalente a ``classify_matricula_tipo`` para agregações."""
    normalized = (tipo or "").strip().casefold()
    if normalized not in {
        ReplicacaoD1FonteRegistro.MATRICULA_MANUAL,
        ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA,
        ReplicacaoD1FonteRegistro.MATRICULA_DESCONHECIDA,
    }:
        return None

    manual_pattern = (
        r"^\s*(?:0(?:\.0)?|manual|m|"
        r"(?:[acq]\.*\d\.*\d\.*\d\.*\d\.*\d\.*[acq]|"
        r"(?:em|et)\.*\d\.*\d\.*\d\.*\d\.*\d)"
        r"(?:@br\.experian\.com(?:\.br)?|@brflow\.com(?:\.br)?)?)\s*$"
    )
    auto_explicit = ["1", "1.0", "automatico", "automático", "a"]
    manual_q = (
        Q(**{f"{field}__iregex": manual_pattern})
    )
    if normalized == ReplicacaoD1FonteRegistro.MATRICULA_MANUAL:
        return manual_q
    if normalized == ReplicacaoD1FonteRegistro.MATRICULA_AUTOMATICA:
        return Q(**{f"{field}__in": auto_explicit}) | ~manual_q
    return Q(**{f"{field}": ""})


def _canonical_row(row: dict[str, Any], source_row_number: int) -> dict[str, Any]:
    protocolo = _as_text(_first(row, "Protocolo", "protocolo"))
    workflow = _as_text(_first(row, "Workflow", "workflow"))
    data_analise = _as_datetime(_first(
        row,
        "Data de Análise",
        "Data de Analise",
        "Data da Análise",
        "Data da Analise",
        "Data de An�lise",
        "data_analise",
    ))
    matricula = _first(row, "Matrícula", "Matricula", "matr�cula", "matricula", "tipo_matricula")
    return {
        "source_row_number": source_row_number,
        "protocolo": protocolo,
        "protocolo_normalizado": normalize_protocolo(protocolo),
        "workflow": workflow,
        "data_analise": data_analise,
        "hora": data_analise.hour if data_analise else None,
        "matricula_tipo": _matricula_tipo(matricula),
        "matricula": _as_text(matricula)[:64],
    }


def _content_hash(rows: list[dict[str, Any]]) -> str:
    canonical = [
        {
            "source_row_number": row["source_row_number"],
            "protocolo": row["protocolo"],
            "workflow": row["workflow"],
            "data_analise": row["data_analise"].isoformat() if row["data_analise"] else None,
            "matricula_tipo": row["matricula_tipo"],
            "matricula": row["matricula"],
        }
        for row in rows
    ]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ingest_source_rows(
    report_date: date,
    rows: Iterable[dict[str, Any]],
    *,
    ingestion_id: int | None = None,
    schema_version: str = "1",
    chunk_size: int = 2000,
) -> SourceBatchResult:
    """Valida e publica um lote atomically; lotes incompletos nunca ficam `ready`."""
    raw_rows = list(rows)
    normalized = [_canonical_row(row, idx) for idx, row in enumerate(raw_rows, start=1)]
    content_hash = _content_hash(normalized)

    existing = ReplicacaoD1FonteLote.objects.filter(
        report_date=report_date,
        content_hash=content_hash,
        status=ReplicacaoD1FonteLote.STATUS_READY,
    ).first()
    if existing:
        return SourceBatchResult(batch=existing, created=False)

    valid: list[dict[str, Any]] = []
    rejected = 0
    duplicate = 0
    seen: set[tuple[str, str, str]] = set()
    for row in normalized:
        if not row["protocolo_normalizado"] or not row["workflow"] or row["data_analise"] is None:
            rejected += 1
            continue
        dedup_key = (row["protocolo_normalizado"], row["workflow"].casefold(), row["data_analise"].isoformat())
        is_duplicate = dedup_key in seen
        duplicate += int(is_duplicate)
        seen.add(dedup_key)
        row["extra"] = {"duplicate": is_duplicate}
        valid.append(row)

    if not valid:
        raise ValueError("A fonte D-1 não possui linhas válidas; o lote anterior foi preservado.")

    with transaction.atomic():
        batch, created = ReplicacaoD1FonteLote.objects.select_for_update().get_or_create(
            report_date=report_date,
            content_hash=content_hash,
            defaults={
                "status": ReplicacaoD1FonteLote.STATUS_LOADING,
                "schema_version": schema_version,
                "rows_read": len(raw_rows),
                "rows_valid": len(valid),
                "rows_duplicate": duplicate,
                "rows_rejected": rejected,
                "ingestion_id": ingestion_id,
            },
        )
        if not created and batch.status == ReplicacaoD1FonteLote.STATUS_READY:
            return SourceBatchResult(batch=batch, created=False)

        batch.registros.all().delete()
        ReplicacaoD1FonteRegistro.objects.bulk_create(
            [ReplicacaoD1FonteRegistro(lote=batch, **row) for row in valid],
            batch_size=chunk_size,
        )
        batch.status = ReplicacaoD1FonteLote.STATUS_READY
        batch.schema_version = schema_version
        batch.rows_read = len(raw_rows)
        batch.rows_valid = len(valid)
        batch.rows_duplicate = duplicate
        batch.rows_rejected = rejected
        batch.error_summary = ""
        batch.ingestion_id = ingestion_id
        batch.finished_at = timezone.now()
        batch.save()
    return SourceBatchResult(batch=batch, created=True)


def latest_ready_batch(report_date: date | None = None) -> ReplicacaoD1FonteLote:
    qs = ReplicacaoD1FonteLote.objects.filter(status=ReplicacaoD1FonteLote.STATUS_READY)
    if report_date is not None:
        qs = qs.filter(report_date=report_date)
    batch = qs.order_by("-report_date", "-finished_at", "-id").first()
    if batch is None:
        suffix = f" para {report_date.isoformat()}" if report_date else ""
        raise ReplicacaoD1FonteLote.DoesNotExist(f"Nenhum lote D-1 pronto no banco{suffix}.")
    return batch


def source_batch_from_rotina(report_date: date) -> SourceBatchResult:
    """Materializa a partição da Rotina no contrato auditável do planejamento D-1."""
    from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

    rows = _rotina_raw_rows_for_day(report_date)
    if not rows:
        raise RotinaDetalhadoBrutoRecord.DoesNotExist(
            "Nenhum registro D-1 encontrado em rotina_detalhado_bruto_record "
            f"para {report_date.isoformat()}."
        )
    return ingest_source_rows(report_date, rows)


def _rotina_raw_rows_for_day(report_date: date) -> list[dict[str, Any]]:
    from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

    records = RotinaDetalhadoBrutoRecord.objects.filter(report_date=report_date).order_by(
        "protocolo", "workflow", "data_analise", "matricula", "id"
    )
    return [
        {
            "Protocolo": record["protocolo"],
            "Workflow": record["workflow"],
            "Data de Análise": record["data_analise"],
            "Matrícula": record["matricula"],
        }
        for record in records.values("protocolo", "workflow", "data_analise", "matricula")
    ]


def rotina_day_has_records(
    report_date: date,
    *,
    workflow_names: set[str] | None = None,
) -> bool:
    """True se a partição do dia possui ao menos um registro (opcionalmente filtrado por workflow)."""
    from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

    qs = RotinaDetalhadoBrutoRecord.objects.filter(report_date=report_date)
    if not qs.exists():
        return False
    workflow_filter = {_normalize_workflow_key(name) for name in (workflow_names or set()) if _as_text(name)}
    if not workflow_filter:
        return True
    for record in qs.values("workflow"):
        if _normalize_workflow_key(record["workflow"]) in workflow_filter:
            return True
    return False


def source_rows_for_day_from_rotina(
    report_date: date,
    *,
    workflow_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Lê um único dia da Rotina no contrato canônico."""
    return source_rows_range_from_rotina(
        report_date,
        report_date,
        workflow_names=workflow_names,
    )


def source_rows_by_day_range_from_rotina(
    data_inicio: date,
    data_fim: date,
    *,
    workflow_names: set[str] | None = None,
) -> dict[date, list[dict[str, Any]]]:
    """LÃª todo o intervalo em uma consulta, preservando a deduplicaÃ§Ã£o de cada dia."""
    from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

    if data_inicio > data_fim:
        raise ValueError("data_inicio deve ser anterior ou igual a data_fim.")

    workflow_filter = {
        _normalize_workflow_key(name)
        for name in (workflow_names or set())
        if _as_text(name)
    }
    records = RotinaDetalhadoBrutoRecord.objects.filter(
        report_date__gte=data_inicio,
        report_date__lte=data_fim,
    ).order_by("report_date", "protocolo", "workflow", "data_analise", "matricula", "id")

    dedup: dict[tuple[date, str, str], dict[str, Any]] = {}
    for record in records.values("protocolo", "workflow", "data_analise", "matricula", "report_date"):
        row = _canonical_row(
            {
                "Protocolo": record["protocolo"],
                "Workflow": record["workflow"],
                "Data de AnÃ¡lise": record["data_analise"],
                "MatrÃ­cula": record["matricula"],
            },
            source_row_number=0,
        )
        if not row["protocolo_normalizado"] or not row["workflow"] or row["data_analise"] is None:
            continue
        workflow_key = _normalize_workflow_key(row["workflow"])
        if workflow_filter and workflow_key not in workflow_filter:
            continue
        report_day = record["report_date"]
        dedup_key = (report_day, row["protocolo_normalizado"], workflow_key)
        current = dedup.get(dedup_key)
        if current is None or (row["data_analise"] or datetime.min) >= (current["data_analise"] or datetime.min):
            row["extra"] = {"retroativo_report_date": report_day.isoformat()}
            dedup[dedup_key] = row

    rows_by_day: dict[date, list[dict[str, Any]]] = {}
    for (report_day, _protocol, _workflow), row in dedup.items():
        rows_by_day.setdefault(report_day, []).append(row)
    return rows_by_day


def source_batch_from_parquet_rows(
    report_date: date,
    rows: Iterable[dict[str, Any]],
) -> SourceBatchResult:
    """Publica linhas de parquet tratado no lote auditável D-1."""
    return ingest_source_rows(report_date, list(rows))


def source_rows_for_planning(batch: ReplicacaoD1FonteLote) -> list[dict[str, Any]]:
    """Contrato estável para o processo de planejamento, sem expor detalhes do ORM."""
    return list(
        batch.registros.order_by("source_row_number").values(
            "id",
            "source_row_number",
            "protocolo",
            "protocolo_normalizado",
            "workflow",
            "data_analise",
            "hora",
            "matricula_tipo",
            "matricula",
            "extra",
        )
    )


def _normalize_workflow_key(value: Any) -> str:
    return normalize_key(_as_text(value))


def source_rows_range_from_rotina(
    data_inicio: date,
    data_fim: date,
    *,
    workflow_names: set[str] | None = None,
    cliente_chaves: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Lê intervalo da Rotina no contrato canônico, com dedup por protocolo/workflow."""
    from apps.replicacao_d1.models import ReplicacaoD1Workflow
    from apps.rotina_bruto.models import RotinaDetalhadoBrutoRecord

    if data_inicio > data_fim:
        raise ValueError("data_inicio deve ser anterior ou igual a data_fim.")

    workflow_filter = {_normalize_workflow_key(name) for name in (workflow_names or set()) if _as_text(name)}
    if cliente_chaves and not workflow_filter:
        workflow_filter = {
            _normalize_workflow_key(name)
            for name in ReplicacaoD1Workflow.objects.filter(
                ativo=True,
                status=ReplicacaoD1Workflow.STATUS_ATIVO,
                cliente__chave_normalizada__in=cliente_chaves,
            ).values_list("nome_d1", flat=True)
            if _as_text(name)
        }
        workflow_filter.update(
            _normalize_workflow_key(name)
            for name in ReplicacaoD1Workflow.objects.filter(
                ativo=True,
                status=ReplicacaoD1Workflow.STATUS_ATIVO,
                cliente__chave_normalizada__in=cliente_chaves,
            ).values_list("nome_canonico", flat=True)
            if _as_text(name)
        )

    records = RotinaDetalhadoBrutoRecord.objects.filter(
        report_date__gte=data_inicio,
        report_date__lte=data_fim,
    ).order_by("protocolo", "workflow", "data_analise", "matricula", "id")

    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for record in records.values("protocolo", "workflow", "data_analise", "matricula", "report_date"):
        row = _canonical_row(
            {
                "Protocolo": record["protocolo"],
                "Workflow": record["workflow"],
                "Data de Análise": record["data_analise"],
                "Matrícula": record["matricula"],
            },
            source_row_number=0,
        )
        if not row["protocolo_normalizado"] or not row["workflow"] or row["data_analise"] is None:
            continue
        wf_key = _normalize_workflow_key(row["workflow"])
        if workflow_filter and wf_key not in workflow_filter:
            continue
        dedup_key = (row["protocolo_normalizado"], wf_key)
        current = dedup.get(dedup_key)
        if current is None or (row["data_analise"] or datetime.min) >= (current["data_analise"] or datetime.min):
            row["extra"] = {"retroativo_report_date": record["report_date"].isoformat()}
            dedup[dedup_key] = row
    return list(dedup.values())


def load_source_dataframe_range_db(
    data_inicio: date,
    data_fim: date,
    *,
    cliente_chaves: set[str] | None = None,
    workflow_chaves: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Alias estável para o planejamento retroativo (lista canônica, sem pandas no backend)."""
    workflow_names = workflow_chaves
    return source_rows_range_from_rotina(
        data_inicio,
        data_fim,
        workflow_names=workflow_names,
        cliente_chaves=cliente_chaves,
    )
