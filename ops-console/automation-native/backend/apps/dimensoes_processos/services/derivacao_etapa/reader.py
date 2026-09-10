# -*- coding: utf-8 -*-
"""Leitura de FINALIZADO_YYYYMMDD.csv (derivacao_etapa)."""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

FILE_NAME_RE = re.compile(r"^FINALIZADO_(\d{8})\.csv$", re.IGNORECASE)

COL_DATA = "Data"
COL_CLIENTE = "Cliente"
COL_WF = "WF"
COL_ETAPAS = "Etapas"
COL_REGISTROS = "Registros"
COL_PERCENTUAL = "Percentual"


@dataclass(frozen=True)
class ParsedDerivacaoRow:
    data: date
    cliente: str
    workflow: str
    etapa: str
    registros: int
    percentual: Decimal
    source_file: str


@dataclass
class FileReadResult:
    path: Path
    file_date: date | None
    rows: list[ParsedDerivacaoRow]
    skipped_total: int
    skipped_invalid: int
    date_mismatch: bool


def parse_date_from_name(name: str) -> date | None:
    match = FILE_NAME_RE.match(Path(name).name)
    if not match:
        return None
    raw = match.group(1)
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def parse_br_date(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_br_decimal(value: str) -> Decimal | None:
    text = (value or "").strip().replace("%", "")
    if not text:
        return None
    text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_br_int(value: str) -> int | None:
    text = (value or "").strip().replace(".", "").replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _is_total_row(etapa: str) -> bool:
    return (etapa or "").strip().casefold() == "total"


def read_derivacao_csv(path: Path, *, encoding: str = "cp1252") -> FileReadResult:
    file_date = parse_date_from_name(path.name)
    rows: list[ParsedDerivacaoRow] = []
    skipped_total = 0
    skipped_invalid = 0
    date_mismatch = False
    seen_dates: set[date] = set()

    try:
        fh = path.open("r", encoding=encoding, newline="")
    except UnicodeDecodeError:
        fh = path.open("r", encoding="latin-1", newline="")

    with fh:
        reader = csv.DictReader(fh, delimiter=";")
        for raw in reader:
            etapa = (raw.get(COL_ETAPAS) or "").strip()
            if _is_total_row(etapa):
                skipped_total += 1
                continue

            row_date = parse_br_date(raw.get(COL_DATA) or "")
            if row_date is None:
                skipped_invalid += 1
                continue
            seen_dates.add(row_date)
            if file_date is not None and row_date != file_date:
                date_mismatch = True

            registros = parse_br_int(raw.get(COL_REGISTROS) or "")
            percentual = parse_br_decimal(raw.get(COL_PERCENTUAL) or "")
            if registros is None or percentual is None:
                skipped_invalid += 1
                continue

            cliente = (raw.get(COL_CLIENTE) or "").strip()
            workflow = (raw.get(COL_WF) or "").strip()
            if not cliente or not workflow or not etapa:
                skipped_invalid += 1
                continue

            rows.append(
                ParsedDerivacaoRow(
                    data=row_date,
                    cliente=cliente,
                    workflow=workflow,
                    etapa=etapa,
                    registros=registros,
                    percentual=percentual,
                    source_file=path.name,
                )
            )

    if file_date is not None and len(seen_dates) == 1 and file_date not in seen_dates:
        date_mismatch = True

    return FileReadResult(
        path=path,
        file_date=file_date,
        rows=rows,
        skipped_total=skipped_total,
        skipped_invalid=skipped_invalid,
        date_mismatch=date_mismatch,
    )


def list_derivacao_files(
    directory: Path,
    *,
    file_name: str = "",
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[Path]:
    if file_name:
        path = directory / file_name
        return [path] if path.is_file() else []

    files: list[Path] = []
    for path in sorted(directory.glob("FINALIZADO_*.csv")):
        file_date = parse_date_from_name(path.name)
        if from_date and file_date and file_date < from_date:
            continue
        if to_date and file_date and file_date > to_date:
            continue
        files.append(path)
    return files


def filter_derivacao_files(
    paths: list[Path],
    *,
    file_name: str = "",
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[Path]:
    """Filtra uma lista de paths (ex.: upload staging) pelo período/nome."""

    if file_name:
        wanted = Path(file_name).name
        return [path for path in paths if path.name == wanted and path.is_file()]

    files: list[Path] = []
    for path in sorted(paths, key=lambda item: item.name.casefold()):
        if not path.is_file():
            continue
        file_date = parse_date_from_name(path.name)
        if from_date and file_date and file_date < from_date:
            continue
        if to_date and file_date and file_date > to_date:
            continue
        files.append(path)
    return files
