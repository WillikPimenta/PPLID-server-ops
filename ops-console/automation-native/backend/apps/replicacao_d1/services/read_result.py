# -*- coding: utf-8 -*-
"""Contrato comum de leitura/validação de artefatos D-1."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeVar

SCHEMA_VERSION = "1"
MAX_ERRORS_SAMPLE = 20

T = TypeVar("T")


@dataclass
class ReadError:
    line: int
    column: str
    code: str
    message: str

    def to_dict(self) -> dict:
        return {
            "line": self.line,
            "column": self.column,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class ReadResult(Generic[T]):
    source_rows: int = 0
    valid_rows: int = 0
    duplicate_rows: int = 0
    rejected_rows: int = 0
    records: list[T] = field(default_factory=list)
    errors_sample: list[ReadError] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    metadata: dict = field(default_factory=dict)

    @property
    def is_empty_source(self) -> bool:
        return self.source_rows == 0

    @property
    def is_structurally_valid(self) -> bool:
        return not any(e.code == "STRUCTURE" for e in self.errors_sample)

    @property
    def accounting_ok(self) -> bool:
        return self.source_rows == self.valid_rows + self.duplicate_rows + self.rejected_rows

    def add_error(self, *, line: int, column: str, code: str, message: str) -> None:
        if len(self.errors_sample) >= MAX_ERRORS_SAMPLE:
            return
        self.errors_sample.append(
            ReadError(line=line, column=column, code=code, message=(message or "")[:255])
        )


def should_replace_partition(*, valid_rows: int, existing_count: int, structurally_valid: bool) -> bool:
    """Evita apagar partição válida por carga vazia ou estruturalmente inválida."""
    if not structurally_valid:
        return False
    if valid_rows == 0 and existing_count > 0:
        return False
    return True
