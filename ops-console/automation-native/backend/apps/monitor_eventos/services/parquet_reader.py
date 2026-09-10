# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from django.utils import timezone

from report_falhas.io.data_loader import norm_matricula, safe_to_datetime

COLUMN_ALIASES = {
    "data": ["data"],
    "hora": ["hora", "hour"],
    "matricula_usuario": [
        "usuario",
        "usuário",
        "user",
        "matricula",
        "matrícula",
        "user_lan_id",
    ],
    "data_evento": [
        "data do evento",
        "data evento",
        "data_evento",
        "datetime",
    ],
    "evento": ["evento", "event"],
    "data_segundo_evento": [
        "data segundo evento",
        "data_segundo_evento",
        "data do segundo evento",
    ],
    "segundo_evento": [
        "segundo evento",
        "segundo_evento",
    ],
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
    data: date
    hora: int
    matricula_usuario: str
    data_evento: datetime
    evento: str
    data_segundo_evento: datetime | None
    segundo_evento: str


def _to_datetime(value) -> datetime | None:
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        parsed = safe_to_datetime(pd.Series([value])).iloc[0]
        if parsed is None or (hasattr(pd, "isna") and pd.isna(parsed)):
            return None
        dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _to_date(value, fallback: datetime | None = None) -> date | None:
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return fallback.date() if fallback else None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    parsed = safe_to_datetime(pd.Series([value])).iloc[0]
    if parsed is None or (hasattr(pd, "isna") and pd.isna(parsed)):
        return fallback.date() if fallback else None
    return parsed.date() if hasattr(parsed, "date") else parsed


def _to_hora(value, fallback: datetime | None = None) -> int:
    if value is not None and not (hasattr(pd, "isna") and pd.isna(value)):
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
    return fallback.hour if fallback else 0


def _cell_str(value) -> str:
    if value is None or (hasattr(pd, "isna") and pd.isna(value)):
        return ""
    return str(value).strip()


def _safe_datetime_series(series: pd.Series) -> pd.Series:
    """Converte timestamps do monitor, inclusive formatos mistos na mesma coluna.

    O tratado pode combinar logouts reais (sem microssegundos) com o fechamento
    da janela (com microssegundos). Desde pandas 2, a inferencia usa um formato
    unico e transforma silenciosamente parte dessas linhas em NaT.
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    try:
        return pd.to_datetime(series, errors="coerce", dayfirst=True, format="mixed")
    except (TypeError, ValueError):
        # Compatibilidade com versoes do pandas anteriores ao format="mixed".
        parsed = safe_to_datetime(series)
        missing = parsed.isna() & series.notna()
        if missing.any():
            parsed.loc[missing] = series.loc[missing].map(_to_datetime)
        return parsed


def _read_dataframe(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        try:
            return pd.read_parquet(path)
        except OSError as exc:
            # OneDrive no Windows costuma falhar com Errno 22 em leitura direta.
            if getattr(exc, "errno", None) != 22:
                raise
            import shutil
            import tempfile

            with tempfile.TemporaryDirectory(prefix="monitor-eventos-") as tmp:
                local = Path(tmp) / path.name
                shutil.copy2(path, local)
                return pd.read_parquet(local)
    encodings = ["utf-8-sig", "utf-8", "cp1252", "latin-1"]
    last_error: Exception | None = None
    for encoding in encodings:
        try:
            return pd.read_csv(path, sep=None, engine="python", dtype=str, encoding=encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return pd.read_csv(path, sep=None, engine="python", dtype=str)


def read_monitor_parquet(path: str | Path) -> list[ParsedRow]:
    df = _read_dataframe(Path(path))
    if df is None or df.empty:
        return []

    header_map = _build_header_map(list(df.columns))
    required = {"matricula_usuario", "data_evento", "evento"}
    missing = required - set(header_map)
    if missing:
        raise ValueError(
            "Colunas obrigatórias ausentes no arquivo monitor: "
            + ", ".join(sorted(missing))
            + f". Colunas encontradas: {list(df.columns)}"
        )

    n = len(df)
    matriculas = [norm_matricula(v) for v in df[header_map["matricula_usuario"]].tolist()]
    eventos = [_cell_str(v) for v in df[header_map["evento"]].tolist()]
    data_eventos_ts = _safe_datetime_series(df[header_map["data_evento"]])

    data_segundo_ts = None
    if "data_segundo_evento" in header_map:
        data_segundo_ts = _safe_datetime_series(df[header_map["data_segundo_evento"]])

    data_raw = (
        df[header_map["data"]]
        if "data" in header_map
        else pd.Series([None] * n, index=df.index)
    )
    data_ts = safe_to_datetime(data_raw)

    hora_raw = (
        df[header_map["hora"]].tolist() if "hora" in header_map else [None] * n
    )

    segundo_eventos = (
        [_cell_str(v) for v in df[header_map["segundo_evento"]].tolist()]
        if "segundo_evento" in header_map
        else [""] * n
    )

    rows: list[ParsedRow] = []
    for i in range(n):
        matricula = matriculas[i]
        evento = eventos[i]
        ts = data_eventos_ts.iloc[i]
        if not matricula or not evento or ts is None or (hasattr(pd, "isna") and pd.isna(ts)):
            continue
        data_evento = _to_datetime(ts)
        if data_evento is None:
            continue

        data_segundo = None
        if data_segundo_ts is not None:
            data_segundo = _to_datetime(data_segundo_ts.iloc[i])

        data_ref = _to_date(data_ts.iloc[i], fallback=data_evento)
        if data_ref is None:
            continue
        hora = _to_hora(hora_raw[i], fallback=data_evento)

        rows.append(
            ParsedRow(
                data=data_ref,
                hora=hora,
                matricula_usuario=matricula,
                data_evento=data_evento,
                evento=evento,
                data_segundo_evento=data_segundo,
                segundo_evento=segundo_eventos[i],
            )
        )
    return rows
