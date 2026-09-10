# -*- coding: utf-8 -*-
"""Importa Excel suplemento (NA + Treinamentos) para tabelas persistentes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from django.db import transaction

from apps.brb_report.models import (
    ReportNaDemanda,
    ReportNaFalha,
    ReportSupplementImportBatch,
    ReportTreinamento,
)
from report_brb.brb_filters import norm_matricula, norm_protocolo, parse_excel_date, safe_str
from report_brb.brb_loaders import validate_workbook
from report_brb.client_registry import CLIENTS, resolve_id_cliente
from report_brb.na_falhas_normalize import read_na_falhas_excel, resolve_na_falhas_sheet_name


def _resolve_id_cliente_from_label(label: object) -> int | None:
    text = safe_str(label)
    if not text:
        return None
    for slug in CLIENTS:
        from report_brb.brb_filters import match_client

        if match_client(text, slug):
            try:
                return resolve_id_cliente(slug)
            except KeyError:
                continue
    return None


def _parse_date_val(val) -> Any:
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if val is None:
        return None
    if hasattr(val, "date") and not isinstance(val, pd.Timestamp):
        try:
            return val.date()
        except (AttributeError, ValueError):
            pass
    series = parse_excel_date(pd.Series([val]))
    ts = series.iloc[0]
    if pd.isna(ts):
        return None
    return ts.date()


def _int_val(val, default: int = 0) -> int:
    n = pd.to_numeric(val, errors="coerce")
    if pd.isna(n):
        return default
    return int(n)


def import_supplement_workbook(
    path: Path,
    *,
    user=None,
    source_filename: str = "",
) -> dict[str, Any]:
    """Upsert NA/Treinamentos — chave natural evita duplicata em re-upload."""
    path = Path(path)
    validate_workbook(path, mode="supplement")
    xl = pd.ExcelFile(path)
    batch = ReportSupplementImportBatch.objects.create(
        user=user,
        source_filename=source_filename or path.name,
        status=ReportSupplementImportBatch.STATUS_COMPLETED,
    )
    stats = {"upserted": 0, "skipped": 0, "na_demanda": 0, "na_falha": 0, "treinamento": 0, "treinamento_horas": 0}

    try:
        with transaction.atomic():
            if "NA_Demandas" in xl.sheet_names:
                stats["na_demanda"] = _import_na_demandas(
                    pd.read_excel(path, "NA_Demandas"), batch, stats
                )
            na_sheet = resolve_na_falhas_sheet_name(xl.sheet_names)
            if na_sheet:
                stats["na_falha"] = _import_na_falhas(
                    read_na_falhas_excel(path, sheet_name=na_sheet), batch, stats
                )
            if "Treinamentos" in xl.sheet_names:
                stats["treinamento"] = _import_treinamentos(
                    pd.read_excel(path, "Treinamentos"), batch, stats, with_horas=False
                )
            if "TreinamentosComHoras" in xl.sheet_names:
                stats["treinamento_horas"] = _import_treinamentos(
                    pd.read_excel(path, "TreinamentosComHoras"), batch, stats, with_horas=True
                )
            batch.rows_na_demanda = stats["na_demanda"]
            batch.rows_na_falha = stats["na_falha"]
            batch.rows_treinamento = stats["treinamento"]
            batch.rows_treinamento_horas = stats["treinamento_horas"]
            batch.upserted = stats["upserted"]
            batch.skipped = stats["skipped"]
            batch.save(
                update_fields=[
                    "rows_na_demanda",
                    "rows_na_falha",
                    "rows_treinamento",
                    "rows_treinamento_horas",
                    "upserted",
                    "skipped",
                ]
            )
    except Exception as exc:  # noqa: BLE001
        batch.status = ReportSupplementImportBatch.STATUS_FAILED
        batch.detail = str(exc)
        batch.save(update_fields=["status", "detail"])
        raise

    return {
        "batch_id": batch.pk,
        "status": batch.status,
        **stats,
    }


def _import_na_demandas(df: pd.DataFrame, batch: ReportSupplementImportBatch, stats: dict) -> int:
    if df.empty:
        return 0
    count = 0
    for _, row in df.iterrows():
        id_cliente = _resolve_id_cliente_from_label(row.get("Cliente"))
        if id_cliente is None:
            stats["skipped"] += 1
            continue
        demanda = safe_str(row.get("Demanda"))
        data_abertura = _parse_date_val(row.get("Data da Abertura"))
        defaults = {
            "data_retorno": _parse_date_val(row.get("Data do Retorno")),
            "mes": safe_str(row.get("Mês")),
            "quantidade_protocolos": _int_val(row.get("Quantidade de Protolocos")),
            "falhas_manuais": _int_val(row.get("Falhas Manuais")),
            "falhas_processuais": _int_val(row.get("Falhas Processuais")),
            "falhas_automaticas": _int_val(row.get("Falhas Automáticas")),
            "situacao": safe_str(row.get("Situação")),
            "cliente_label": safe_str(row.get("Cliente")),
            "import_batch": batch,
        }
        _, created = ReportNaDemanda.objects.update_or_create(
            id_cliente=id_cliente,
            demanda=demanda,
            data_abertura=data_abertura,
            defaults=defaults,
        )
        stats["upserted"] += 1
        count += 1
    return count


def _import_na_falhas(df: pd.DataFrame, batch: ReportSupplementImportBatch, stats: dict) -> int:
    from report_brb.na_falhas_normalize import normalize_na_falhas_raw

    df = normalize_na_falhas_raw(df)
    if df.empty:
        return 0
    count = 0
    for _, row in df.iterrows():
        id_cliente = _resolve_id_cliente_from_label(row.get("CLIENTE"))
        if id_cliente is None:
            stats["skipped"] += 1
            continue
        protocolo_norm = norm_protocolo(row.get("PROTOCOLO"))
        matricula_norm = norm_matricula(row.get("MATRÍCULA") or row.get("MATRICULA"))
        if not protocolo_norm:
            stats["skipped"] += 1
            continue
        defaults = {
            "protocolo": safe_str(row.get("PROTOCOLO")),
            "matricula": safe_str(row.get("MATRÍCULA") or row.get("MATRICULA")),
            "data_cadastro": _parse_date_val(row.get("DATA DE CADASTRO")),
            "data_notificacao": _parse_date_val(row.get("DATA DE NOTIFICAÇÃO")),
            "motivo_falha": safe_str(row.get("MOTIVO DA FALHA")),
            "resultado_cliente": safe_str(row.get("RESULTADO DO CLIENTE")),
            "resultado_auditoria": safe_str(row.get("RESULTADO DA AUDITORIA")),
            "demanda": safe_str(row.get("DEMANDA")),
            "cliente_label": safe_str(row.get("CLIENTE")),
            "import_batch": batch,
        }
        _, _created = ReportNaFalha.objects.update_or_create(
            id_cliente=id_cliente,
            protocolo_norm=protocolo_norm,
            matricula_norm=matricula_norm,
            defaults=defaults,
        )
        stats["upserted"] += 1
        count += 1
    return count


def _import_treinamentos(
    df: pd.DataFrame,
    batch: ReportSupplementImportBatch,
    stats: dict,
    *,
    with_horas: bool,
) -> int:
    if df.empty:
        return 0
    title_col = "Event: EventTitle"
    if title_col not in df.columns:
        return 0
    count = 0
    for _, row in df.iterrows():
        id_cliente = _resolve_id_cliente_from_label(row.get(title_col)) or 0
        scope = "cliente" if id_cliente else "corporativo"
        if id_cliente is None:
            stats["skipped"] += 1
            continue
        matricula = safe_str(row.get("Agent: UserLanID") or row.get("Matricula"))
        matricula_norm = norm_matricula(matricula)
        event_title = safe_str(row.get(title_col))
        horas = None
        if with_horas:
            session_start = _parse_date_val(row.get("StartDate"))
            session_final = _parse_date_val(row.get("FinalDate"))
            horas_raw = row.get("Event: EstimatedDuration") or row.get("horas")
            from report_brb.brb_loaders import parse_duration_hours

            horas = parse_duration_hours(horas_raw) if horas_raw is not None else None
        else:
            session_start = _parse_date_val(row.get("Session: StartDate") or row.get("StartDate"))
            session_final = _parse_date_val(row.get("Session: FinalDate") or row.get("FinalDate"))
            if session_start is None:
                session_start = _parse_date_val(row.get("AssignmentDate"))
        if session_start is None:
            stats["skipped"] += 1
            continue
        defaults = {
            "matricula": matricula,
            "assignment_date": _parse_date_val(row.get("AssignmentDate")),
            "signature_date": _parse_date_val(row.get("SignatureDate")),
            "session_final": _parse_date_val(session_final) if session_final is not None else None,
            "horas": horas,
            "status": safe_str(row.get("SessionStatus") or row.get("Status")),
            "cliente_label": event_title,
            "scope": scope,
            "import_batch": batch,
        }
        for key in ("assignment_date", "signature_date", "session_final"):
            if isinstance(defaults.get(key), pd.Timestamp) and pd.isna(defaults[key]):
                defaults[key] = None
        _, _created = ReportTreinamento.objects.update_or_create(
            id_cliente=id_cliente,
            matricula_norm=matricula_norm,
            event_title=event_title,
            session_start=session_start,
            defaults=defaults,
        )
        stats["upserted"] += 1
        count += 1
    return count
