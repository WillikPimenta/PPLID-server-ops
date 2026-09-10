# -*- coding: utf-8 -*-
"""Leitura do CSV brflow-replicadosd1-tratado_YYYYMMDD.csv."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from django.utils import timezone as dj_timezone

from apps.replicacao_d1.constants import REPLICADOS_FILE_PREFIX
from apps.replicacao_d1.normalization import normalize_protocolo
from apps.replicacao_d1.services.read_result import ReadResult

DATE_IN_NAME = re.compile(
    rf"^{re.escape(REPLICADOS_FILE_PREFIX)}(\d{{8}})\.csv$",
    re.IGNORECASE,
)

COL_MAP = {
    "Cliente Destino": "cliente_destino",
    "Data de Cadastro Destino": "data_cadastro_destino",
    "Protocolo Destino": "protocolo_destino",
    "Workflow Destino": "workflow_destino",
    "Protocolo Origem": "protocolo_origem",
    "Cliente Origem": "cliente_origem",
    "Workflow Origem": "workflow_origem",
    "Nível Hierárquico Origem": "nivel_hierarquico_origem",
    "Nivel Hierarquico Origem": "nivel_hierarquico_origem",
    "Data de Cadastro Origem": "data_cadastro_origem",
    "Tipo de Conclusão de Análise Origem": "tipo_conclusao_analise_origem",
    "Tipo de Conclusao de Analise Origem": "tipo_conclusao_analise_origem",
}


def _as_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = str(value).strip()
    if text.lower() in ("nan", "none", "nat"):
        return ""
    return text


def parse_report_date_from_name(name: str) -> date | None:
    match = DATE_IN_NAME.search(Path(name).name)
    if not match:
        return None
    raw = match.group(1)
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


@dataclass
class ParsedReplicado:
    protocolo_origem: str
    cliente_destino: str = ""
    data_cadastro_destino: datetime | None = None
    protocolo_destino: str = ""
    workflow_destino: str = ""
    cliente_origem: str = ""
    workflow_origem: str = ""
    nivel_hierarquico_origem: str = ""
    data_cadastro_origem: datetime | None = None
    tipo_conclusao_analise_origem: str = ""


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    for col in df.columns:
        key = str(col).strip()
        if key in COL_MAP:
            rename[col] = COL_MAP[key]
    return df.rename(columns=rename)


def _string_values(df: pd.DataFrame, column: str) -> list[str]:
    if column not in df.columns:
        return [""] * len(df)
    return [_as_str(value) for value in df[column].tolist()]


def _datetime_values(df: pd.DataFrame, column: str) -> list[datetime | None]:
    """Converte uma coluna inteira, evitando parser Python por celula."""
    if column not in df.columns:
        return [None] * len(df)
    parsed = pd.to_datetime(df[column], errors="coerce", format="mixed", dayfirst=True)
    values: list[datetime | None] = []
    for value in parsed.tolist():
        if pd.isna(value):
            values.append(None)
            continue
        dt = value.to_pydatetime() if hasattr(value, "to_pydatetime") else value
        if isinstance(dt, datetime) and dj_timezone.is_naive(dt):
            dt = dj_timezone.make_aware(dt, dj_timezone.get_current_timezone())
        values.append(dt if isinstance(dt, datetime) else None)
    return values


def read_replicados_csv(
    path: Path,
    *,
    report_date: date | None = None,
) -> tuple[date, ReadResult[ParsedReplicado]]:
    path = Path(path)
    stats = ReadResult[ParsedReplicado]()
    resolved_date = report_date or parse_report_date_from_name(path.name)
    if resolved_date is None:
        stats.add_error(
            line=0,
            column="filename",
            code="STRUCTURE",
            message="data do relatório ausente no nome",
        )
        raise ValueError(f"data do relatório ausente no nome: {path.name}")

    try:
        df = pd.read_csv(path, sep=";", encoding="utf-8-sig", dtype=str)
    except Exception:
        df = pd.read_csv(path, sep=";", encoding="cp1252", dtype=str)

    stats.metadata = {"report_date": resolved_date.isoformat()}

    if df.empty:
        return resolved_date, stats

    df = _normalize_columns(df)
    if "protocolo_origem" not in df.columns:
        stats.add_error(
            line=0,
            column="Protocolo Origem",
            code="STRUCTURE",
            message=f"Coluna Protocolo Origem ausente em {path.name}",
        )
        return resolved_date, stats

    stats.source_rows = len(df)
    protocolos = _string_values(df, "protocolo_origem")
    cliente_destino = _string_values(df, "cliente_destino")
    cadastro_destino = _datetime_values(df, "data_cadastro_destino")
    protocolo_destino = _string_values(df, "protocolo_destino")
    workflow_destino = _string_values(df, "workflow_destino")
    cliente_origem = _string_values(df, "cliente_origem")
    workflow_origem = _string_values(df, "workflow_origem")
    nivel_origem = _string_values(df, "nivel_hierarquico_origem")
    cadastro_origem = _datetime_values(df, "data_cadastro_origem")
    tipo_conclusao = _string_values(df, "tipo_conclusao_analise_origem")

    seen_raw: set[str] = set()
    seen_norm: set[str] = set()
    for idx, protocolo in enumerate(protocolos):
        line_no = idx + 2
        if not protocolo:
            stats.rejected_rows += 1
            stats.add_error(
                line=line_no,
                column="Protocolo Origem",
                code="EMPTY",
                message="protocolo origem vazio",
            )
            continue
        norm = normalize_protocolo(protocolo)
        if protocolo in seen_raw:
            stats.duplicate_rows += 1
            continue
        if norm and norm in seen_norm:
            stats.duplicate_rows += 1
            continue
        seen_raw.add(protocolo)
        if norm:
            seen_norm.add(norm)
        item = ParsedReplicado(
            protocolo_origem=protocolo,
            cliente_destino=cliente_destino[idx],
            data_cadastro_destino=cadastro_destino[idx],
            protocolo_destino=protocolo_destino[idx],
            workflow_destino=workflow_destino[idx],
            cliente_origem=cliente_origem[idx],
            workflow_origem=workflow_origem[idx],
            nivel_hierarquico_origem=nivel_origem[idx],
            data_cadastro_origem=cadastro_origem[idx],
            tipo_conclusao_analise_origem=tipo_conclusao[idx],
        )
        stats.records.append(item)
        stats.valid_rows += 1

    return resolved_date, stats
