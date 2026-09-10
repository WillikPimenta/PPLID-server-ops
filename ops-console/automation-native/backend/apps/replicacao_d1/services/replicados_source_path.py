# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from django.conf import settings

from apps.replicacao_d1.constants import REPLICADOS_FILE_PREFIX
from apps.replicacao_d1.services.replicados_reader import parse_report_date_from_name
from apps.replicacao_d1.services.secure_path import assert_path_under_root

FILE_PATTERN = f"{REPLICADOS_FILE_PREFIX}*.csv"


@dataclass(frozen=True)
class ReplicadosSourceFileInfo:
    path: Path
    name: str
    report_date: date
    mtime: float
    size: int


def get_replicados_source_dir(override: str | None = None) -> Path:
    if override:
        return Path(override)
    configured = (getattr(settings, "REPLICACAO_D1_REPLICADOS_DIR", None) or "").strip()
    if configured:
        return Path(configured)
    env_path = (os.environ.get("REPLICACAO_D1_REPLICADOS_DIR") or "").strip()
    if env_path:
        return Path(env_path)
    raise FileNotFoundError(
        "REPLICACAO_D1_REPLICADOS_DIR não configurado. Defina em settings ou variável de ambiente."
    )


def list_replicados_files(source_dir: Path | None = None) -> list[ReplicadosSourceFileInfo]:
    directory = source_dir or get_replicados_source_dir()
    if not directory.is_dir():
        return []
    files: list[ReplicadosSourceFileInfo] = []
    for path in directory.glob(FILE_PATTERN):
        if not path.is_file():
            continue
        report_date = parse_report_date_from_name(path.name)
        if not report_date:
            continue
        stat = path.stat()
        files.append(
            ReplicadosSourceFileInfo(
                path=path,
                name=path.name,
                report_date=report_date,
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
        )
    return files


def get_latest_replicados_file(source_dir: Path | None = None) -> ReplicadosSourceFileInfo | None:
    files = list_replicados_files(source_dir)
    if not files:
        return None
    return max(files, key=lambda info: (info.report_date, info.mtime))


def get_replicados_source_file(
    override_path: str | None = None,
    *,
    report_date: date | None = None,
) -> ReplicadosSourceFileInfo:
    if override_path:
        path = Path(override_path)
        if not path.is_file():
            raise FileNotFoundError("Arquivo não encontrado.")
        try:
            root = get_replicados_source_dir()
            path = assert_path_under_root(path, root)
        except FileNotFoundError:
            path = path.resolve()
        parsed = parse_report_date_from_name(path.name)
        if not parsed:
            raise ValueError("Nome inválido para replicados D-1.")
        stat = path.stat()
        return ReplicadosSourceFileInfo(
            path=path,
            name=path.name,
            report_date=parsed,
            mtime=stat.st_mtime,
            size=stat.st_size,
        )
    root = get_replicados_source_dir()
    if report_date:
        matches = [f for f in list_replicados_files(root) if f.report_date == report_date]
        if not matches:
            raise FileNotFoundError("Nenhum CSV de replicados para a data informada.")
        return max(matches, key=lambda info: info.mtime)
    latest = get_latest_replicados_file()
    if not latest:
        raise FileNotFoundError(
            f"Nenhum arquivo {FILE_PATTERN} em: {get_replicados_source_dir()}"
        )
    return latest


def replicados_file_signature(info: ReplicadosSourceFileInfo) -> tuple[str, float, int]:
    return (str(info.path.resolve()), info.mtime, info.size)
