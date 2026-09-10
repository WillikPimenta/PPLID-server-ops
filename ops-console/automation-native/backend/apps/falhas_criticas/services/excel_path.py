# -*- coding: utf-8 -*-
"""Caminho central da planilha Excel (sync automático)."""
from __future__ import annotations

import os
from pathlib import Path

_EXCEL_NOT_CONFIGURED_MSG = (
    "Configure EXCEL_SOURCE_PATH no .env ou use Planejamento > Importar Falhas "
    "para enviar o .xlsx."
)


def get_excel_source_path(override: str | None = None) -> str:
    """Resolve o Excel fonte: argumento > Django settings > env > config_report."""
    if override is not None:
        return str(override).strip()

    try:
        from django.conf import settings

        configured = (getattr(settings, "EXCEL_SOURCE_PATH", None) or "").strip()
        if configured:
            return configured
    except Exception:
        pass

    env_path = (
        os.environ.get("EXCEL_SOURCE_PATH") or os.environ.get("REPORT_EXCEL_PATH") or ""
    ).strip()
    if env_path:
        return env_path

    import report_falhas.config_report as cfg

    configured = (cfg.EXCEL_PATH or "").strip()
    if configured:
        return configured
    canon = cfg.canonical_falhas_excel_path()
    if canon.is_file():
        return str(canon)
    return ""


def require_excel_source_path(override: str | None = None) -> str:
    """Retorna caminho do Excel ou levanta erro com mensagem amigável."""
    path = get_excel_source_path(override)
    if not path:
        raise FileNotFoundError(_EXCEL_NOT_CONFIGURED_MSG)
    return path


def excel_source_exists(path: str | None = None) -> bool:
    resolved = get_excel_source_path(path)
    if not resolved:
        return False
    return Path(resolved).is_file()
