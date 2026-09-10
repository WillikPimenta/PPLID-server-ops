"""Helpers compartilhados para importação tabular (xlsx/csv)."""

from __future__ import annotations

import csv
from io import StringIO
from typing import Any


def is_csv_filename(filename: str | None) -> bool:
    return (filename or "").lower().endswith(".csv")


def is_xlsx_filename(filename: str | None) -> bool:
    return (filename or "").lower().endswith(".xlsx")


def is_supported_import_filename(filename: str | None) -> bool:
    return is_csv_filename(filename) or is_xlsx_filename(filename)


def decode_csv_rows(file_bytes: bytes) -> list[tuple[Any, ...]]:
    """Decodifica CSV (UTF-8/CP1252) com delimitador ; , ou tab."""
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = file_bytes.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = file_bytes.decode("latin-1", errors="replace")

    sample = text[:8192]
    delimiter = ";"
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
        delimiter = dialect.delimiter
    except csv.Error:
        comma = sample.count(",")
        semicolon = sample.count(";")
        tab = sample.count("\t")
        if tab > comma and tab > semicolon:
            delimiter = "\t"
        elif comma > semicolon:
            delimiter = ","

    reader = csv.reader(StringIO(text), delimiter=delimiter)
    return [tuple(cell.strip() if isinstance(cell, str) else cell for cell in row) for row in reader]
