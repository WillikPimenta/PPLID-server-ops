# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

from apps.replicacao_d1.services.secure_path import assert_path_under_root

FILE_PREFIX = "replicacao_aud_d1_relatorio_"
FILE_PATTERN = f"{FILE_PREFIX}*.xlsx"
RUN_ID_IN_NAME = re.compile(
    rf"^{re.escape(FILE_PREFIX)}(.+)\.xlsx$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SourceFileInfo:
    path: Path
    name: str
    run_id: str
    mtime: float
    size: int


def get_source_dir(override: str | None = None) -> Path:
    if override:
        return Path(override)
    configured = (getattr(settings, "REPLICACAO_D1_SOURCE_DIR", None) or "").strip()
    if configured:
        return Path(configured)
    env_path = (os.environ.get("REPLICACAO_D1_SOURCE_DIR") or "").strip()
    if env_path:
        return Path(env_path)
    raise FileNotFoundError(
        "REPLICACAO_D1_SOURCE_DIR não configurado. Defina em settings ou variável de ambiente."
    )


def parse_run_id_from_name(name: str) -> str:
    match = RUN_ID_IN_NAME.search(Path(name).name)
    return match.group(1) if match else ""


def list_source_files(source_dir: Path | None = None) -> list[SourceFileInfo]:
    directory = source_dir or get_source_dir()
    if not directory.is_dir():
        return []
    files: list[SourceFileInfo] = []
    for path in directory.rglob(FILE_PATTERN):
        if not path.is_file():
            continue
        run_id = parse_run_id_from_name(path.name)
        if not run_id:
            continue
        stat = path.stat()
        files.append(
            SourceFileInfo(
                path=path,
                name=path.name,
                run_id=run_id,
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
        )
    return files


def get_latest_source_file(source_dir: Path | None = None) -> SourceFileInfo | None:
    files = list_source_files(source_dir)
    if not files:
        return None
    return max(files, key=lambda info: (info.mtime, info.name))


def resolve_source_by_run_id(run_id: str, source_dir: Path | None = None) -> SourceFileInfo | None:
    run_id = (run_id or "").strip()
    if not run_id:
        return None
    directory = source_dir or get_source_dir()
    candidates = [
        directory / f"{FILE_PREFIX}{run_id}.xlsx",
        directory / "brflow" / f"{FILE_PREFIX}{run_id}.xlsx",
        directory / "case" / f"{FILE_PREFIX}{run_id}.xlsx",
    ]
    for path in candidates:
        if path.is_file():
            stat = path.stat()
            return SourceFileInfo(
                path=path,
                name=path.name,
                run_id=run_id,
                mtime=stat.st_mtime,
                size=stat.st_size,
            )
    for info in list_source_files(directory):
        if info.run_id == run_id:
            return info
    return None


def get_source_file(
    override_path: str | None = None,
    *,
    run_id: str | None = None,
) -> SourceFileInfo:
    if override_path:
        path = Path(override_path)
        if not path.is_file():
            raise FileNotFoundError("Arquivo não encontrado.")
        try:
            root = get_source_dir()
            path = assert_path_under_root(path, root)
        except FileNotFoundError:
            path = path.resolve()
        resolved_run = parse_run_id_from_name(path.name) or (run_id or "").strip()
        if not resolved_run:
            raise ValueError("Não foi possível extrair run_id do nome do arquivo.")
        stat = path.stat()
        return SourceFileInfo(
            path=path,
            name=path.name,
            run_id=resolved_run,
            mtime=stat.st_mtime,
            size=stat.st_size,
        )
    if run_id:
        found = resolve_source_by_run_id(run_id)
        if not found:
            raise FileNotFoundError(
                f"Nenhum relatório D-1 para run_id={run_id} em: {get_source_dir()}"
            )
        return found
    latest = get_latest_source_file()
    if not latest:
        raise FileNotFoundError(
            f"Nenhum arquivo {FILE_PATTERN} encontrado em: {get_source_dir()}"
        )
    return latest


def file_signature(info: SourceFileInfo) -> tuple[str, float, int]:
    return (str(info.path.resolve()), info.mtime, info.size)
