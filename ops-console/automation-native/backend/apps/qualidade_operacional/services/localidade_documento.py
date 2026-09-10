# -*- coding: utf-8 -*-
"""UF do documento persistida em localidade_documento (filtros EO)."""
from __future__ import annotations

from apps.auditoria.services.text_format import UF_CODES
from apps.qualidade_operacional.services.normalize import clean_text


def normalize_localidade_documento(raw: str | None, *, warnings: list[str] | None = None) -> str:
    """Normaliza UF do documento para filtros/agrupamentos EO."""
    text = clean_text(raw, max_len=8)
    if not text:
        return ""
    lowered = text.lower()
    if lowered in UF_CODES:
        return text.upper()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    if warnings is not None:
        warnings.append(f"uf_nao_reconhecida:{text[:20]}")
    return text[:8]


def resolve_localidade_documento(*, uf: str = "", source_uf: str = "") -> str:
    """Prioriza uf já normalizado; senão normaliza source_uf."""
    clean_uf = clean_text(uf, max_len=8)
    if clean_uf:
        return normalize_localidade_documento(clean_uf)
    return normalize_localidade_documento(source_uf)
