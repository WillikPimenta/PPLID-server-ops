# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd
from django.utils import timezone

from report_falhas.io.data_loader import norm_matricula, safe_to_datetime

COLUMN_ALIASES = {
    "matricula": [
        "matricula",
        "matrícula",
        "matricula agente",
        "matrícula agente",
        "user_lan_id",
        "userlanid",
        "agent: userlanid",
        "agent userlanid",
        "id de rede",
    ],
    "etapa": ["etapa", "stage", "fase"],
    "analysis_seconds": [
        "tempo total",
        "soma de tempo de análise em segundos",
        "soma de tempo de analise em segundos",
        "soma tempo analise segundos",
        "soma tempo de analise em segundos",
        "tempo de analise em segundos",
        "tempo analise segundos",
        "analysis_seconds",
        "tempo_segundos",
    ],
    "analysis_count": [
        "total de analise",
        "total de análise",
        "total analise",
        "qtd analise",
    ],
    "stage_goal": [
        "meta",
        "meta da etapa",
        "meta etapa",
        "stage_goal",
        "goal",
    ],
    "recorded_at": [
        "data e hora",
        "data hora",
        "data/hora",
        "datetime",
        "recorded_at",
    ],
    "recorded_at_date": [
        "data de analise",
        "data de análise",
        "data analise",
        "data",
    ],
    "recorded_at_hour": ["hora"],
}


def _normalize_header(value: object) -> str:
    text = "" if value is None or (hasattr(pd, "isna") and pd.isna(value)) else str(value)
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text)
    return text


def _build_header_map(columns: list[object]) -> dict[str, str]:
    normalized = {_normalize_header(col): str(col) for col in columns}
    mapping: dict[str, str] = {}
    for canonical, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = _normalize_header(alias)
            if key in normalized:
                mapping[canonical] = normalized[key]
                break
    return mapping


@dataclass
class ParsedRow:
    matricula_norm: str
    etapa: str
    analysis_seconds: int
    analysis_count: int
    stage_goal: Decimal | None
    recorded_at: datetime


def _to_decimal(value) -> Decimal | None:
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return None
    try:
        dec = Decimal(str(value).replace(",", ".").strip())
        if dec < 0:
            return None
        return dec
    except (InvalidOperation, ValueError):
        return None


def _to_int(value) -> int:
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return 0
    try:
        num = float(str(value).replace(",", ".").strip())
        if not pd.notna(num):
            return 0
        return max(0, int(round(num)))
    except (TypeError, ValueError):
        return 0


def _to_datetime(value) -> datetime | None:
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        parsed = safe_to_datetime(value)
        if parsed is None or (hasattr(pd, "isna") and pd.isna(parsed)):
            return None
        dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _combine_date_hour(date_value, hour_value) -> datetime | None:
    if date_value is None or (hasattr(pd, "isna") and pd.isna(date_value)):
        return None
    if isinstance(date_value, pd.Timestamp):
        dt = date_value.to_pydatetime()
    elif isinstance(date_value, datetime):
        dt = date_value
    else:
        parsed = safe_to_datetime(date_value)
        if parsed is None or (hasattr(pd, "isna") and pd.isna(parsed)):
            return None
        dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
    try:
        if hour_value is None or (hasattr(pd, "isna") and pd.isna(hour_value)):
            hour = dt.hour
        else:
            hour = int(float(str(hour_value).strip()))
    except (TypeError, ValueError):
        hour = dt.hour
    hour = max(0, min(23, hour))
    combined = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    if timezone.is_naive(combined):
        return timezone.make_aware(combined, timezone.get_current_timezone())
    return combined


def _resolve_recorded_at(row, header_map: dict[str, str]) -> datetime | None:
    if "recorded_at" in header_map:
        return _to_datetime(row.get(header_map["recorded_at"]))
    if "recorded_at_date" in header_map:
        hour_col = header_map.get("recorded_at_hour")
        hour_value = row.get(hour_col) if hour_col else None
        return _combine_date_hour(row.get(header_map["recorded_at_date"]), hour_value)
    return None


def read_productivity_excel(path: str | Path) -> list[ParsedRow]:
    try:
        df = pd.read_excel(path)
    except PermissionError as exc:
        raise PermissionError(
            "Não foi possível ler o Excel — feche a planilha no Excel/OneDrive e tente novamente."
        ) from exc
    except OSError as exc:
        if getattr(exc, "errno", None) in (13, 22) or "Permission denied" in str(exc):
            raise PermissionError(
                "Não foi possível ler o Excel — feche a planilha no Excel/OneDrive e tente novamente."
            ) from exc
        raise

    if df is None or df.empty:
        return []

    header_map = _build_header_map(list(df.columns))
    has_recorded_at = "recorded_at" in header_map or "recorded_at_date" in header_map
    required = {"matricula", "etapa", "analysis_seconds"}
    missing = required - set(header_map)
    if missing or not has_recorded_at:
        if not has_recorded_at:
            missing = missing | {"recorded_at"}
        raise ValueError(
            "Colunas obrigatórias ausentes no Excel: "
            + ", ".join(sorted(missing))
            + f". Colunas encontradas: {list(df.columns)}"
        )

    rows: list[ParsedRow] = []
    for _, row in df.iterrows():
        matricula = norm_matricula(row.get(header_map["matricula"]))
        etapa = str(row.get(header_map["etapa"], "") or "").strip()
        recorded_at = _resolve_recorded_at(row, header_map)
        if not matricula or not etapa or not recorded_at:
            continue
        stage_goal = _to_decimal(row.get(header_map.get("stage_goal", ""), None))
        analysis_count = _to_int(row.get(header_map.get("analysis_count", ""), 0))
        rows.append(
            ParsedRow(
                matricula_norm=matricula,
                etapa=etapa,
                analysis_seconds=_to_int(row.get(header_map["analysis_seconds"])),
                analysis_count=analysis_count,
                stage_goal=stage_goal,
                recorded_at=recorded_at,
            )
        )
    return rows
