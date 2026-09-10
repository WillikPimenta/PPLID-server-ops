# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from django.conf import settings

FILE_PATTERN = "relatorio_produtividade_detalhado_*.xlsx"
DATE_IN_NAME = re.compile(r"relatorio_produtividade_detalhado_(\d{4}-\d{2}-\d{2})\.xlsx$", re.I)


@dataclass(frozen=True)
class SourceFileInfo:
    path: Path
    name: str
    file_date: date | None
    mtime: float
    size: int


def get_source_dir(override: str | None = None) -> Path:
    if override:
        return Path(override)
    configured = (getattr(settings, "PRODUTIVIDADE_SOURCE_DIR", None) or "").strip()
    if configured:
        return Path(configured)
    env_path = (os.environ.get("PRODUTIVIDADE_SOURCE_DIR") or "").strip()
    if env_path:
        return Path(env_path)
    return Path(
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bots\brflow-prod-hxh-bruto"
    )


def _parse_date_from_name(name: str) -> date | None:
    match = DATE_IN_NAME.search(name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def list_source_files(source_dir: Path | None = None) -> list[SourceFileInfo]:
    directory = source_dir or get_source_dir()
    if not directory.is_dir():
        return []
    files: list[SourceFileInfo] = []
    for path in directory.glob(FILE_PATTERN):
        if not path.is_file():
            continue
        stat = path.stat()
        files.append(
            SourceFileInfo(
                path=path,
                name=path.name,
                file_date=_parse_date_from_name(path.name),
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
        )
    return files


def get_latest_source_file(source_dir: Path | None = None) -> SourceFileInfo | None:
    files = list_source_files(source_dir)
    if not files:
        return None

    def sort_key(info: SourceFileInfo) -> tuple:
        file_date = info.file_date or date.min
        return (file_date, info.mtime)

    return max(files, key=sort_key)


def get_source_file(override_path: str | None = None) -> SourceFileInfo:
    if override_path:
        path = Path(override_path)
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {path}")
        stat = path.stat()
        return SourceFileInfo(
            path=path,
            name=path.name,
            file_date=_parse_date_from_name(path.name),
            mtime=stat.st_mtime,
            size=stat.st_size,
        )
    latest = get_latest_source_file()
    if not latest:
        directory = get_source_dir()
        raise FileNotFoundError(
            f"Nenhum arquivo {FILE_PATTERN} encontrado em: {directory}"
        )
    return latest


def file_signature(info: SourceFileInfo) -> tuple[str, float, int]:
    return (str(info.path.resolve()), info.mtime, info.size)
