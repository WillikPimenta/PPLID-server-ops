# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

import pandas as pd
from django.db import transaction

from apps.suporte_claro.models import (
    SuporteClaroHistorico,
    SuporteClaroImportRef,
    SuporteClaroRegistro,
)
from apps.suporte_claro.services.import_template import IMPORT_COLUMNS, IMPORT_SHEET_NAME
from apps.suporte_claro.services.protocolos import (
    MAX_PROTOCOLOS,
    format_protocolo_legacy,
    parse_protocolos_text,
    save_protocolos,
)
from apps.suporte_claro.services.validation import parse_origem, parse_received_at, parse_status

CANAL_LABEL_TO_VALUE = {
    "teams": SuporteClaroRegistro.ORIGEM_TEAMS,
    "e-mail": SuporteClaroRegistro.ORIGEM_EMAIL,
    "email": SuporteClaroRegistro.ORIGEM_EMAIL,
    "e mail": SuporteClaroRegistro.ORIGEM_EMAIL,
    "ligação": SuporteClaroRegistro.ORIGEM_LIGACAO,
    "ligacao": SuporteClaroRegistro.ORIGEM_LIGACAO,
}

STATUS_LABEL_TO_VALUE = {
    "não iniciado": SuporteClaroRegistro.STATUS_ABERTO,
    "nao iniciado": SuporteClaroRegistro.STATUS_ABERTO,
    "aberto": SuporteClaroRegistro.STATUS_ABERTO,
    "em andamento": SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
    "em_atendimento": SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
    "concluído": SuporteClaroRegistro.STATUS_CONCLUIDO,
    "concluido": SuporteClaroRegistro.STATUS_CONCLUIDO,
}


def _norm_label(value: Any) -> str:
    return str(value or "").strip().lower()


def _cell_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, str):
        return value.strip()
    if hasattr(value, "strftime"):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value).strip()


def _parse_canal(raw: str) -> str | None:
    text = _norm_label(raw)
    if not text:
        return None
    if text in CANAL_LABEL_TO_VALUE:
        return CANAL_LABEL_TO_VALUE[text]
    return parse_origem(text)


def _parse_status_label(raw: str) -> str | None:
    text = _norm_label(raw)
    if not text:
        return None
    if text in STATUS_LABEL_TO_VALUE:
        return STATUS_LABEL_TO_VALUE[text]
    return parse_status(text)


@dataclass
class ImportRowParsed:
    row_number: int
    linha_id: str = ""
    titulo: str = ""
    protocolo: str = ""
    protocolos_raw: str = ""
    comentarios_protocolo: str = ""
    origem: str = ""
    received_at_raw: str = ""
    received_at: Any = None
    sent_by: str = ""
    irregularidade: str = ""
    status: str = ""
    avaliacao: str = ""


@dataclass
class ImportRowResult:
    row: int
    linha_id: str
    protocolo: str
    status: str  # ok | skip | error
    message: str = ""
    registro_id: int | None = None


@dataclass
class ImportPreview:
    total: int = 0
    valid: int = 0
    skipped: int = 0
    errors: int = 0
    rows: list[ImportRowResult] = field(default_factory=list)


@dataclass
class ImportApplyResult:
    total: int = 0
    created: int = 0
    skipped: int = 0
    errors: int = 0
    rows: list[ImportRowResult] = field(default_factory=list)


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename_map = {col: str(col).strip() for col in df.columns}
    df = df.rename(columns=rename_map)
    return df


def parse_import_file(file_bytes: bytes) -> list[ImportRowParsed]:
    buffer = BytesIO(file_bytes)
    try:
        df = pd.read_excel(buffer, sheet_name=IMPORT_SHEET_NAME, engine="openpyxl")
    except ValueError:
        df = pd.read_excel(buffer, sheet_name=0, engine="openpyxl")
    df = _normalize_columns(df)

    missing = [col for col in ("Linha_ID", "Protocolo") if col not in df.columns]
    if missing:
        raise ValueError(f"Colunas obrigatórias ausentes na aba {IMPORT_SHEET_NAME}: {', '.join(missing)}")

    rows: list[ImportRowParsed] = []
    for idx, series in df.iterrows():
        row_number = int(idx) + 2  # header + 1-based excel
        linha_id = _cell_str(series.get("Linha_ID"))
        protocolo = _cell_str(series.get("Protocolo"))
        titulo = _cell_str(series.get("Titulo"))
        protocolos_raw = _cell_str(series.get("Protocolos"))
        comentarios_protocolo = _cell_str(series.get("Comentarios protocolo"))
        if not linha_id and not protocolo and not titulo and not protocolos_raw:
            continue
        if linha_id.lower() in ("linha_id", "exemplo") or protocolo.upper().startswith("PROT-EXEMPLO"):
            continue

        rows.append(
            ImportRowParsed(
                row_number=row_number,
                linha_id=linha_id,
                titulo=titulo,
                protocolo=protocolo,
                protocolos_raw=protocolos_raw,
                comentarios_protocolo=comentarios_protocolo,
                origem=_cell_str(series.get("Canal")),
                received_at_raw=_cell_str(series.get("Recebido em")),
                sent_by=_cell_str(series.get("Quem enviou")),
                irregularidade=_cell_str(series.get("Irregularidade (cliente)")),
                status=_cell_str(series.get("Status")),
                avaliacao=_cell_str(series.get("Retorno do suporte")),
            )
        )
    return rows


def _validate_row(parsed: ImportRowParsed, seen_linha_ids: set[str]) -> ImportRowResult:
    base = ImportRowResult(
        row=parsed.row_number,
        linha_id=parsed.linha_id,
        protocolo=parsed.protocolo,
        status="error",
    )

    if not parsed.linha_id:
        base.message = "Linha_ID obrigatório."
        return base
    if parsed.linha_id in seen_linha_ids:
        base.message = f"Linha_ID duplicado na planilha: {parsed.linha_id}."
        return base
    seen_linha_ids.add(parsed.linha_id)

    existing = SuporteClaroImportRef.objects.filter(linha_id=parsed.linha_id).select_related("registro").first()
    if existing:
        base.status = "skip"
        base.message = f"Já importado (demanda #{existing.registro_id})."
        base.registro_id = existing.registro_id
        return base

    if not parsed.titulo and not parsed.protocolo and not parsed.protocolos_raw:
        base.message = "Titulo ou Protocolo/Protocolos obrigatorio."
        return base

    titulo = parsed.titulo or parsed.protocolo or (parsed.protocolos_raw.split(",")[0].strip() if parsed.protocolos_raw else "")
    if not titulo:
        base.message = "Titulo ou Protocolo obrigatorio."
        return base

    origem = _parse_canal(parsed.origem)
    if not origem:
        base.message = "Canal inválido. Use Teams, E-mail ou Ligação."
        return base

    received_at = parse_received_at(parsed.received_at_raw)
    if received_at is None:
        base.message = "Recebido em inválido. Use DD/MM/AAAA HH:MM."
        return base

    reg_status = _parse_status_label(parsed.status)
    if not reg_status:
        base.message = "Status inválido. Use Não iniciado, Em andamento ou Concluído."
        return base

    if not parsed.irregularidade:
        base.message = "Irregularidade (cliente) obrigatória."
        return base

    if reg_status == SuporteClaroRegistro.STATUS_CONCLUIDO and not parsed.avaliacao:
        base.message = "Retorno do suporte obrigatório quando Status = Concluído."
        return base

    base.status = "ok"
    base.message = "Pronto para importar."
    return base


def preview_import(file_bytes: bytes) -> ImportPreview:
    parsed_rows = parse_import_file(file_bytes)
    seen: set[str] = set()
    results: list[ImportRowResult] = []
    valid = skipped = errors = 0
    for parsed in parsed_rows:
        result = _validate_row(parsed, seen)
        results.append(result)
        if result.status == "ok":
            valid += 1
        elif result.status == "skip":
            skipped += 1
        else:
            errors += 1
    return ImportPreview(
        total=len(results),
        valid=valid,
        skipped=skipped,
        errors=errors,
        rows=results,
    )


def apply_import(file_bytes: bytes, user) -> ImportApplyResult:
    parsed_rows = parse_import_file(file_bytes)
    seen: set[str] = set()
    results: list[ImportRowResult] = []
    created = skipped = errors = 0

    with transaction.atomic():
        for parsed in parsed_rows:
            validation = _validate_row(parsed, seen)
            if validation.status == "skip":
                results.append(validation)
                skipped += 1
                continue
            if validation.status == "error":
                results.append(validation)
                errors += 1
                continue

            origem = _parse_canal(parsed.origem)
            received_at = parse_received_at(parsed.received_at_raw)
            reg_status = _parse_status_label(parsed.status)

            protocolos_source = parsed.protocolos_raw or parsed.protocolo
            numeros = parse_protocolos_text(protocolos_source)
            comentarios = [c.strip() for c in parsed.comentarios_protocolo.split("|")] if parsed.comentarios_protocolo else []
            protocolo_items = [
                (numero, comentarios[idx] if idx < len(comentarios) else "")
                for idx, numero in enumerate(numeros)
            ]

            titulo = parsed.titulo or parsed.protocolo or (protocolo_items[0][0] if protocolo_items else "")
            if not titulo:
                results.append(
                    ImportRowResult(
                        row=parsed.row_number,
                        linha_id=parsed.linha_id,
                        protocolo=parsed.protocolo or parsed.protocolos_raw,
                        status="error",
                        message="Titulo ou Protocolo obrigatorio.",
                    )
                )
                errors += 1
                continue

            limit_err = (
                f"Maximo de {MAX_PROTOCOLOS} protocolos por demanda."
                if len(protocolo_items) > MAX_PROTOCOLOS
                else None
            )
            if limit_err:
                results.append(
                    ImportRowResult(
                        row=parsed.row_number,
                        linha_id=parsed.linha_id,
                        protocolo=parsed.protocolo or parsed.protocolos_raw,
                        status="error",
                        message=limit_err,
                    )
                )
                errors += 1
                continue

            registro = SuporteClaroRegistro.objects.create(
                titulo=titulo,
                protocolo=format_protocolo_legacy(protocolo_items),
                irregularidade=parsed.irregularidade,
                avaliacao=parsed.avaliacao,
                received_at=received_at,
                sent_by=parsed.sent_by,
                origem=origem or "",
                status=reg_status or SuporteClaroRegistro.STATUS_ABERTO,
                created_by=user,
            )
            if protocolo_items:
                save_protocolos(registro, protocolo_items, user=user)
            SuporteClaroImportRef.objects.create(
                linha_id=parsed.linha_id,
                registro=registro,
                imported_by=user,
            )
            SuporteClaroHistorico.objects.create(
                registro=registro,
                user=user,
                action=SuporteClaroHistorico.ACTION_IMPORT,
                field_name="import",
                old_value="",
                new_value=f"Planilha offline ({parsed.linha_id})",
            )
            results.append(
                ImportRowResult(
                    row=parsed.row_number,
                    linha_id=parsed.linha_id,
                    protocolo=parsed.protocolo,
                    status="ok",
                    message=f"Demanda #{registro.id} criada.",
                    registro_id=registro.id,
                )
            )
            created += 1

    return ImportApplyResult(
        total=len(results),
        created=created,
        skipped=skipped,
        errors=errors,
        rows=results,
    )


def serialize_preview(preview: ImportPreview) -> dict:
    return {
        "total": preview.total,
        "valid": preview.valid,
        "skipped": preview.skipped,
        "errors": preview.errors,
        "rows": [
            {
                "row": row.row,
                "linha_id": row.linha_id,
                "protocolo": row.protocolo,
                "status": row.status,
                "message": row.message,
                "registro_id": row.registro_id,
            }
            for row in preview.rows
        ],
    }


def serialize_apply_result(result: ImportApplyResult) -> dict:
    return {
        "total": result.total,
        "created": result.created,
        "skipped": result.skipped,
        "errors": result.errors,
        "rows": [
            {
                "row": row.row,
                "linha_id": row.linha_id,
                "protocolo": row.protocolo,
                "status": row.status,
                "message": row.message,
                "registro_id": row.registro_id,
            }
            for row in result.rows
        ],
    }
