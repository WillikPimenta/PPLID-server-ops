# -*- coding: utf-8 -*-
"""Configuração de fonte do Indicador EO (TSV × Intranet)."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache

from django.conf import settings

SOURCE_MODE_LEGACY = "legacy"
SOURCE_MODE_HYBRID = "hybrid"
SOURCE_MODE_INTRANET = "intranet"
VALID_SOURCE_MODES = frozenset(
    {SOURCE_MODE_LEGACY, SOURCE_MODE_HYBRID, SOURCE_MODE_INTRANET}
)

INTRANET_SOURCE_FILE = "auditoria_falha_cadastro"
INTRANET_SOURCE_KIND = "intranet"
TSV_SOURCE_KIND = "tsv"
INTRANET_SOURCE_LABEL = "Intranet"
TSV_SOURCE_LABEL = "Arquivo TSV"
G_AUDITORIA_SOURCE_FILE = "g_auditoria_parquet"
G_AUDITORIA_SOURCE_KIND = "g_auditoria"
G_AUDITORIA_SOURCE_LABEL = "Parquet G Auditoria"
MAPPING_VERSION = 8


def intranet_source_enabled() -> bool:
    return bool(getattr(settings, "QUALIDADE_INTRANET_SOURCE_ENABLED", False))


def g_auditoria_projection_enabled() -> bool:
    return bool(
        getattr(settings, "QUALIDADE_G_AUDITORIA_PROJECTION_ENABLED", False)
    )


def get_source_mode() -> str:
    raw = str(getattr(settings, "QUALIDADE_SOURCE_MODE", SOURCE_MODE_LEGACY) or "").strip().lower()
    if raw not in VALID_SOURCE_MODES:
        return SOURCE_MODE_LEGACY
    return raw


def get_cutover_date() -> date | None:
    raw = str(getattr(settings, "QUALIDADE_INTRANET_CUTOVER_DATE", "") or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def tsv_override_allowed() -> bool:
    return bool(getattr(settings, "QUALIDADE_INTRANET_TSV_OVERRIDE", False))


def effective_source_active() -> bool:
    """True quando a Intranet pode alimentar o EO (não é modo legado puro)."""
    if not intranet_source_enabled():
        return False
    mode = get_source_mode()
    if mode == SOURCE_MODE_LEGACY:
        return False
    if mode == SOURCE_MODE_HYBRID and get_cutover_date() is None:
        return False
    return True


def should_project_date(event_date: date | None) -> bool:
    """Decide se uma data de auditoria Intranet deve gerar projeção."""
    if not effective_source_active():
        return False
    mode = get_source_mode()
    if mode == SOURCE_MODE_INTRANET:
        return True
    cutover = get_cutover_date()
    if mode == SOURCE_MODE_HYBRID and cutover is not None:
        if event_date is None:
            return False
        return event_date >= cutover
    return False


def tsv_competencia_blocked(competencia: date) -> bool:
    """Bloqueia import TSV para competências inteiramente cobertas pela Intranet.

    No modo hybrid, o mês do cutover permanece parcialmente aberto ao TSV:
    linhas com data >= cutover são rejeitadas por ``tsv_date_blocked``.
    """
    if tsv_override_allowed():
        return False
    if not effective_source_active():
        return False
    mode = get_source_mode()
    cutover = get_cutover_date()
    if mode == SOURCE_MODE_INTRANET:
        return True
    if mode == SOURCE_MODE_HYBRID and cutover is not None:
        month_start = competencia.replace(day=1)
        return month_start > cutover.replace(day=1)
    return False


def tsv_date_blocked(event_date: date | None) -> bool:
    if event_date is None:
        return False
    if tsv_override_allowed():
        return False
    if not effective_source_active():
        return False
    mode = get_source_mode()
    cutover = get_cutover_date()
    if mode == SOURCE_MODE_INTRANET:
        return True
    if mode == SOURCE_MODE_HYBRID and cutover is not None:
        return event_date >= cutover
    return False


@lru_cache(maxsize=1)
def _cached_config_fingerprint() -> tuple:
    return (
        intranet_source_enabled(),
        g_auditoria_projection_enabled(),
        get_source_mode(),
        get_cutover_date(),
        tsv_override_allowed(),
    )


def clear_source_config_cache() -> None:
    _cached_config_fingerprint.cache_clear()


def parse_optional_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None
