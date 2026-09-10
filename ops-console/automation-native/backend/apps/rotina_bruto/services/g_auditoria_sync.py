# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

import pandas as pd
from django.db import transaction
from django.utils import timezone

from apps.rotina_bruto.models import RotinaGAuditoriaRecord
from apps.rotina_bruto.services.parquet_reader import read_parquet
from apps.rotina_bruto.services.parsers import parse_date_series
from apps.rotina_bruto.services.source_path import SourceFileInfo

MAPPING_VERSION = 1
DEADLINE_DAYS = 120
BATCH_SIZE = 1000

REQUIRED_COLUMNS = frozenset(
    {
        "Chave Registro Origem",
        "Cliente Origem",
        "Workflow Origem",
        "Nivel Hierarquico Origem",
        "Usuario Origem",
        "Numero do CPF",
        "N. do Contrato/Proposta",
        "Data de Cadastro Origem",
        "Status do Registro Origem",
        "Tipo de Conclusao de Analise Origem",
        "NOMOPERADOR",
        "Id_transacao_origem",
        "ID_TRANSACAO_ORIGEM",
        "TMPANALISE",
        "Cliente Destino",
        "Data de Cadastro Destino",
        "Alertas Destino",
        "Protocolo Origem",
        "Protocolo Destino",
        "Workflow Destino",
        "MATRICULA",
        "Matricula Destino",
        "ETAPA",
        "Data de Conclusao Origem",
        "Data de Conclusao Destino",
        "Resultado Origem",
        "Resultado Destino",
        "Status do Registro Destino",
        "Tipo de Conclusao de Analise Destino",
    }
)

SOURCE_KEY_COLUMNS = (
    "Chave Registro Origem",
    "Id_transacao_origem",
    "ID_TRANSACAO_ORIGEM",
    "Protocolo Origem",
    "Protocolo Destino",
    "ETAPA",
    "Data de Conclusao Destino",
)


@dataclass(frozen=True)
class GAuditoriaSyncResult:
    row_count: int
    metrics: dict[str, Any]
    source_sha256: str
    mapping_version: int = MAPPING_VERSION


def _chunks(values: list[Any], size: int = BATCH_SIZE) -> Iterator[list[Any]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _cell(value: Any, *, max_len: int | None = None) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    if max_len is not None:
        return text[:max_len]
    return text


def normalize_protocol(value: Any) -> str:
    return re.sub(r"\s+", "", _cell(value, max_len=100)).casefold()


def normalize_stage(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _cell(value))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = text.casefold()
    return " ".join(re.findall(r"[a-z0-9]+", text))[:512]


def normalize_dimension(value: Any) -> str:
    return normalize_stage(value)


def normalize_matricula(value: Any) -> str:
    text = _cell(value, max_len=64)
    return "" if text in {"", "-"} else text.casefold()


def _hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _deadline_status(
    data_analise: date | None,
    data_auditoria: date | None,
) -> tuple[int | None, str]:
    if data_analise is None:
        return None, RotinaGAuditoriaRecord.PRAZO_ANALISE_AUSENTE
    if data_auditoria is None:
        return None, RotinaGAuditoriaRecord.PRAZO_AUDITORIA_AUSENTE
    days = (data_auditoria - data_analise).days
    if days < 0:
        return days, RotinaGAuditoriaRecord.PRAZO_DATAS_INVERTIDAS
    if days >= DEADLINE_DAYS:
        return days, RotinaGAuditoriaRecord.PRAZO_FORA
    return days, RotinaGAuditoriaRecord.PRAZO_DENTRO


def _validate_schema(df: pd.DataFrame) -> None:
    missing = sorted(REQUIRED_COLUMNS.difference(str(col) for col in df.columns))
    if missing:
        raise ValueError(f"G Auditoria sem colunas obrigatórias: {', '.join(missing)}")
    if df.empty:
        raise ValueError("G Auditoria vazio; partição anterior preservada.")


def build_staging_records(
    df: pd.DataFrame,
    source: SourceFileInfo,
) -> tuple[list[RotinaGAuditoriaRecord], dict[str, Any]]:
    _validate_schema(df)
    analysis_dates = parse_date_series(df["Data de Conclusao Origem"])
    audit_dates = parse_date_series(df["Data de Conclusao Destino"])

    groups: dict[str, dict[str, Any]] = {}
    for position, (_, row) in enumerate(df.iterrows()):
        key_values = [_cell(row.get(column)) for column in SOURCE_KEY_COLUMNS]
        source_key = _hash_payload(key_values)
        data_analise = analysis_dates[position]
        data_auditoria = audit_dates[position]
        dias_prazo, prazo_status = _deadline_status(data_analise, data_auditoria)
        validation_errors: list[str] = []
        if _cell(row.get("Data de Conclusao Origem")) and data_analise is None:
            validation_errors.append("data_analise_invalida")
        if _cell(row.get("Data de Conclusao Destino")) and data_auditoria is None:
            validation_errors.append("data_auditoria_invalida")

        payload = {
            "source_record_key": _cell(row.get("Chave Registro Origem"), max_len=255),
            "origin_transaction_id": _cell(row.get("Id_transacao_origem"), max_len=255),
            "origin_transaction_code": _cell(row.get("ID_TRANSACAO_ORIGEM"), max_len=255),
            "protocolo_origem": _cell(row.get("Protocolo Origem"), max_len=100),
            "protocolo_destino": _cell(row.get("Protocolo Destino"), max_len=100),
            "cliente_origem": _cell(row.get("Cliente Origem"), max_len=255),
            "workflow_origem": _cell(row.get("Workflow Origem"), max_len=255),
            "workflow_destino": _cell(row.get("Workflow Destino"), max_len=255),
            "matricula_agente": normalize_matricula(row.get("MATRICULA")),
            "matricula_auditor": normalize_matricula(row.get("Matricula Destino")),
            "etapa": _cell(row.get("ETAPA"), max_len=512),
            "data_analise": data_analise,
            "data_auditoria": data_auditoria,
            "resultado_origem": _cell(row.get("Resultado Origem"), max_len=255),
            "resultado_destino": _cell(row.get("Resultado Destino"), max_len=255),
            "status_destino": _cell(row.get("Status do Registro Destino"), max_len=255),
            "tipo_conclusao_destino": _cell(
                row.get("Tipo de Conclusao de Analise Destino"), max_len=255
            ),
            "dias_prazo": dias_prazo,
            "prazo_status": prazo_status,
        }
        content_hash = _hash_payload(payload)
        record = RotinaGAuditoriaRecord(
            report_date=source.report_date,
            source_file=source.path.name,
            source_key=source_key,
            protocolo_normalizado=normalize_protocol(payload["protocolo_origem"]),
            etapa_normalizada=normalize_stage(payload["etapa"]),
            content_hash=content_hash,
            validation_errors=validation_errors,
            is_quarantined=False,
            is_active=True,
            **payload,
        )
        state = groups.get(source_key)
        if state is None:
            groups[source_key] = {
                "record": record,
                "raw_count": 1,
                "content_hashes": {content_hash},
            }
        else:
            state["raw_count"] += 1
            state["content_hashes"].add(content_hash)

    records: list[RotinaGAuditoriaRecord] = []
    rows_duplicate = 0
    rows_rejected = 0
    source_key_collisions = 0
    for state in groups.values():
        record = state["record"]
        raw_count = int(state["raw_count"])
        if len(state["content_hashes"]) > 1:
            record.is_quarantined = True
            record.validation_errors = sorted(
                {*record.validation_errors, "source_key_collision"}
            )
            rows_rejected += raw_count
            source_key_collisions += 1
        else:
            rows_duplicate += raw_count - 1
        records.append(record)

    rows_valid = sum(1 for record in records if not record.is_quarantined)
    metrics = {
        "rows_read": int(len(df)),
        "rows_valid": rows_valid,
        "rows_duplicate": rows_duplicate,
        "rows_rejected": rows_rejected,
        "source_key_collisions": source_key_collisions,
        "missing_data_analise": sum(
            record.prazo_status == RotinaGAuditoriaRecord.PRAZO_ANALISE_AUSENTE
            for record in records
        ),
        "missing_data_auditoria": sum(
            record.prazo_status == RotinaGAuditoriaRecord.PRAZO_AUDITORIA_AUSENTE
            for record in records
        ),
        "datas_invertidas": sum(
            record.prazo_status == RotinaGAuditoriaRecord.PRAZO_DATAS_INVERTIDAS
            for record in records
        ),
        "fora_prazo_120": sum(
            record.prazo_status == RotinaGAuditoriaRecord.PRAZO_FORA
            for record in records
        ),
    }
    if metrics["rows_read"] != (
        metrics["rows_valid"] + metrics["rows_duplicate"] + metrics["rows_rejected"]
    ):
        raise ValueError("Accounting inválido na carga G Auditoria.")
    return records, metrics


def _existing_content_hashes(source_keys: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for chunk in _chunks(source_keys):
        result.update(
            RotinaGAuditoriaRecord.objects.filter(source_key__in=chunk).values_list(
                "source_key", "content_hash"
            )
        )
    return result


def _persist_staging(
    records: list[RotinaGAuditoriaRecord],
    source: SourceFileInfo,
    metrics: dict[str, Any],
) -> list[RotinaGAuditoriaRecord]:
    source_keys = [record.source_key for record in records]
    existing = _existing_content_hashes(source_keys)
    metrics["rows_staging_created"] = sum(key not in existing for key in source_keys)
    metrics["rows_staging_updated"] = sum(
        key in existing and existing[key] != record.content_hash
        for key, record in zip(source_keys, records)
    )
    metrics["rows_staging_unchanged"] = sum(
        key in existing and existing[key] == record.content_hash
        for key, record in zip(source_keys, records)
    )

    previous_active_keys = set(
        RotinaGAuditoriaRecord.objects.filter(
        report_date=source.report_date,
        is_active=True,
        ).values_list("source_key", flat=True)
    )
    RotinaGAuditoriaRecord.objects.filter(report_date=source.report_date).update(
        is_active=False
    )

    update_fields = [
        "report_date",
        "source_file",
        "source_record_key",
        "origin_transaction_id",
        "origin_transaction_code",
        "protocolo_origem",
        "protocolo_normalizado",
        "protocolo_destino",
        "cliente_origem",
        "workflow_origem",
        "workflow_destino",
        "matricula_agente",
        "matricula_auditor",
        "etapa",
        "etapa_normalizada",
        "data_analise",
        "data_auditoria",
        "resultado_origem",
        "resultado_destino",
        "status_destino",
        "tipo_conclusao_destino",
        "dias_prazo",
        "prazo_status",
        "content_hash",
        "validation_errors",
        "is_quarantined",
        "is_active",
        "imported_at",
    ]
    now = timezone.now()
    for record in records:
        record.imported_at = now
    for chunk in _chunks(records):
        RotinaGAuditoriaRecord.objects.bulk_create(
            chunk,
            batch_size=BATCH_SIZE,
            update_conflicts=True,
            unique_fields=["source_key"],
            update_fields=update_fields,
        )

    persisted: list[RotinaGAuditoriaRecord] = []
    for chunk in _chunks(source_keys):
        persisted.extend(
            RotinaGAuditoriaRecord.objects.filter(source_key__in=chunk).order_by("id")
        )
    new_active_keys = {record.source_key for record in persisted if record.is_active}
    metrics["rows_staging_deactivated"] = len(
        previous_active_keys.difference(new_active_keys)
    )
    return persisted


def sync_g_auditoria_to_db(
    source: SourceFileInfo,
    *,
    dry_run: bool = False,
    project: bool | None = None,
) -> GAuditoriaSyncResult:
    """Valida e persiste o Parquet; nenhuma linha deste fluxo cria falha."""

    df = read_parquet(source.path)
    records, metrics = build_staging_records(df, source)
    source_sha256 = _file_sha256(source.path)
    metrics.update(
        {
            "arquivo": source.path.name,
            "data_referencia": source.report_date.isoformat(),
            "hash_arquivo": source_sha256,
            "mapping_version": MAPPING_VERSION,
        }
    )
    if dry_run:
        metrics["dry_run"] = True
        metrics["rows_projected_created"] = 0
        metrics["rows_projected_updated"] = 0
        metrics["rows_projected_removed"] = 0
        metrics["failure_matches"] = 0
        metrics["failure_unmatched"] = 0
        metrics["failure_ambiguous"] = 0
        return GAuditoriaSyncResult(
            row_count=metrics["rows_valid"],
            metrics=metrics,
            source_sha256=source_sha256,
        )

    from apps.qualidade_operacional.services.g_auditoria import (
        project_g_auditoria_records,
    )
    from apps.qualidade_operacional.services.source_config import (
        g_auditoria_projection_enabled,
    )

    should_project = (
        g_auditoria_projection_enabled() if project is None else bool(project)
    )
    with transaction.atomic():
        persisted = _persist_staging(records, source, metrics)
        if should_project:
            projection_metrics = project_g_auditoria_records(
                persisted,
                report_date=source.report_date,
                bump_cache=False,
            )
            metrics.update(projection_metrics)
        else:
            metrics.update(
                {
                    "projection_enabled": False,
                    "rows_projected_created": 0,
                    "rows_projected_updated": 0,
                    "rows_projected_removed": 0,
                    "failure_matches": 0,
                    "failure_unmatched": 0,
                    "failure_ambiguous": 0,
                }
            )

    if should_project:
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )

        bump_quality_cache_version()
    return GAuditoriaSyncResult(
        row_count=metrics["rows_valid"],
        metrics=metrics,
        source_sha256=source_sha256,
    )
