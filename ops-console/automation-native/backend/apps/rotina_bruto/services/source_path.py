# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from django.conf import settings

from apps.rotina_bruto.models import RotinaBrutoSyncLog

logger = logging.getLogger(__name__)

FILE_PATTERNS = {
    RotinaBrutoSyncLog.REPORT_DETALHADO: re.compile(
        r"brflow-detalhado-(?:bruto|tratado)_(\d{8})\.parquet$", re.I
    ),
    RotinaBrutoSyncLog.REPORT_PROD: re.compile(
        r"brflow-prod-bruto_(\d{8})\.parquet$", re.I
    ),
    RotinaBrutoSyncLog.REPORT_MONITOR: re.compile(
        r"brflow-monitor-tratado_(\d{8})\.parquet$", re.I
    ),
    RotinaBrutoSyncLog.REPORT_CONFER_BUSCA: re.compile(
        r"confer-buscarpIrregularidade-tratado_(\d{6})\.csv$", re.I
    ),
    RotinaBrutoSyncLog.REPORT_GED_DETALHADO: re.compile(
        r"ged-detalhado-tratado_(\d{6})\.parquet$", re.I
    ),
    RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE: re.compile(
        r"ged-irregularidade-tratado_(\d{6})_(\d)\.csv$", re.I
    ),
    RotinaBrutoSyncLog.REPORT_G_AUDITORIA: re.compile(
        r"brflow-gauditoria_tratado_(\d{8})\.parquet$", re.I
    ),
}

_DEFAULT_DIRS = {
    RotinaBrutoSyncLog.REPORT_DETALHADO: (
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\brflow-detalhado-bruto"
    ),
    RotinaBrutoSyncLog.REPORT_PROD: (
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\brflow-prod-bruto"
    ),
    RotinaBrutoSyncLog.REPORT_MONITOR: (
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\brflow-monitor-tratado"
    ),
    RotinaBrutoSyncLog.REPORT_CONFER_BUSCA: (
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\confer-buscarpIrregularidade-tratado"
    ),
    RotinaBrutoSyncLog.REPORT_GED_DETALHADO: (
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\ged-detalhado-tratado"
    ),
    RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE: (
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\ged-irregularidade-tratado"
    ),
    RotinaBrutoSyncLog.REPORT_G_AUDITORIA: (
        r"C:\Users\c93123a\OneDrive - EXPERIAN SERVICES CORP"
        r"\Planejamento - IDF - Bases\Bots\brflow-gauditoria_tratado"
    ),
}

_SETTING_KEYS = {
    RotinaBrutoSyncLog.REPORT_DETALHADO: "ROTINA_DETALHADO_BRUTO_DIR",
    RotinaBrutoSyncLog.REPORT_PROD: "ROTINA_PROD_BRUTO_DIR",
    RotinaBrutoSyncLog.REPORT_MONITOR: "ROTINA_MONITOR_TRATADO_DIR",
    RotinaBrutoSyncLog.REPORT_CONFER_BUSCA: "ROTINA_CONFER_BUSCA_TRATADO_DIR",
    RotinaBrutoSyncLog.REPORT_GED_DETALHADO: "ROTINA_GED_DETALHADO_TRATADO_DIR",
    RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE: "ROTINA_GED_IRREGULARIDADE_TRATADO_DIR",
    RotinaBrutoSyncLog.REPORT_G_AUDITORIA: "ROTINA_G_AUDITORIA_TRATADO_DIR",
}

_PERIODIC_REPORT_TYPES = {
    RotinaBrutoSyncLog.REPORT_CONFER_BUSCA,
    RotinaBrutoSyncLog.REPORT_GED_DETALHADO,
    RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE,
}


@dataclass(frozen=True)
class SourceFileInfo:
    path: Path
    report_type: str
    report_date: date
    mtime: float
    size: int
    periodo: int | None = None


def _parse_yyyymm(yyyymm: str) -> date | None:
    if len(yyyymm) != 6 or not yyyymm.isdigit():
        return None
    try:
        return date(int(yyyymm[:4]), int(yyyymm[4:6]), 1)
    except ValueError:
        return None


def parse_file_meta(report_type: str, name: str) -> tuple[date, int | None] | None:
    pattern = FILE_PATTERNS.get(report_type)
    if not pattern:
        return None
    match = pattern.search(name)
    if not match:
        return None

    if report_type in _PERIODIC_REPORT_TYPES:
        report_date = _parse_yyyymm(match.group(1))
        if not report_date:
            return None
        if report_type == RotinaBrutoSyncLog.REPORT_CONFER_BUSCA:
            return report_date, None
        if report_type == RotinaBrutoSyncLog.REPORT_GED_IRREGULARIDADE:
            try:
                periodo = int(match.group(2))
            except (TypeError, ValueError):
                return None
            return report_date, periodo
        periodo_raw = match.group(2) if match.lastindex and match.lastindex >= 2 else None
        if periodo_raw is None or periodo_raw == "":
            return report_date, None
        try:
            return report_date, int(periodo_raw)
        except ValueError:
            return None

    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date(), None
    except ValueError:
        return None


def parse_report_date_from_name(report_type: str, name: str) -> date | None:
    meta = parse_file_meta(report_type, name)
    if not meta:
        return None
    return meta[0]


def get_source_dir(report_type: str, override: str | None = None) -> Path:
    if override:
        return Path(override)
    setting_key = _SETTING_KEYS.get(report_type, "")
    configured = (getattr(settings, setting_key, None) or "").strip() if setting_key else ""
    if configured:
        return Path(configured)
    env_path = (os.environ.get(setting_key) or "").strip() if setting_key else ""
    if env_path:
        return Path(env_path)
    if report_type == RotinaBrutoSyncLog.REPORT_MONITOR:
        legacy_key = "ROTINA_MONITOR_BRUTO_DIR"
        legacy = (getattr(settings, legacy_key, None) or os.environ.get(legacy_key) or "").strip()
        if legacy:
            logger.warning(
                "%s está obsoleto; configure ROTINA_MONITOR_TRATADO_DIR apontando para brflow-monitor-tratado",
                legacy_key,
            )
            return Path(legacy)
    return Path(_DEFAULT_DIRS[report_type])


def _build_source_info(path: Path, report_type: str) -> SourceFileInfo | None:
    meta = parse_file_meta(report_type, path.name)
    if not meta:
        return None
    report_date, periodo = meta
    stat = path.stat()
    return SourceFileInfo(
        path=path,
        report_type=report_type,
        report_date=report_date,
        mtime=stat.st_mtime,
        size=stat.st_size,
        periodo=periodo,
    )


def get_source_file(report_type: str, override_path: str | None = None) -> SourceFileInfo:
    if override_path:
        path = Path(override_path)
        if not path.is_file():
            raise FileNotFoundError(f"Arquivo não encontrado: {path}")
        info = _build_source_info(path, report_type)
        if not info:
            raise ValueError(f"Nome de arquivo inválido para {report_type}: {path.name}")
        return info

    directory = get_source_dir(report_type)
    if not directory.is_dir():
        raise FileNotFoundError(f"Pasta não encontrada: {directory}")

    candidates: list[SourceFileInfo] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        info = _build_source_info(path, report_type)
        if info:
            candidates.append(info)

    if not candidates:
        raise FileNotFoundError(f"Nenhum arquivo encontrado em: {directory}")

    if report_type in _PERIODIC_REPORT_TYPES:
        candidates = _dedupe_by_period(candidates)
    elif report_type == RotinaBrutoSyncLog.REPORT_MONITOR:
        candidates = _dedupe_monitor_by_date(candidates)

    return max(
        candidates,
        key=lambda info: (info.report_date, info.periodo or 0, info.mtime),
    )


def file_signature(info: SourceFileInfo) -> tuple[str, str, float, int]:
    return (
        info.report_type,
        str(info.path.resolve()),
        info.mtime,
        info.size,
    )


def _dedupe_monitor_by_date(files: list[SourceFileInfo]) -> list[SourceFileInfo]:
    """Mantém um arquivo por data (parquet tratado)."""
    by_date: dict[date, SourceFileInfo] = {}
    for info in files:
        existing = by_date.get(info.report_date)
        if existing is None or info.mtime >= existing.mtime:
            by_date[info.report_date] = info
    return list(by_date.values())


def _dedupe_by_period(files: list[SourceFileInfo]) -> list[SourceFileInfo]:
    """Mantém um arquivo por (report_date, periodo)."""
    by_key: dict[tuple[date, int | None], SourceFileInfo] = {}
    for info in files:
        key = (info.report_date, info.periodo)
        existing = by_key.get(key)
        if existing is None or info.mtime >= existing.mtime:
            by_key[key] = info
    return list(by_key.values())


def list_source_files(
    report_type: str,
    source_dir: Path | None = None,
    *,
    from_date: date | None = None,
    to_date: date | None = None,
) -> list[SourceFileInfo]:
    directory = source_dir or get_source_dir(report_type)
    if not directory.is_dir():
        return []

    files: list[SourceFileInfo] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        info = _build_source_info(path, report_type)
        if not info:
            continue
        if from_date and info.report_date < from_date:
            continue
        if to_date and info.report_date > to_date:
            continue
        files.append(info)

    if report_type in _PERIODIC_REPORT_TYPES:
        files = _dedupe_by_period(files)
    elif report_type == RotinaBrutoSyncLog.REPORT_MONITOR:
        files = _dedupe_monitor_by_date(files)

    return sorted(files, key=lambda info: (info.report_date, info.periodo or 0))
