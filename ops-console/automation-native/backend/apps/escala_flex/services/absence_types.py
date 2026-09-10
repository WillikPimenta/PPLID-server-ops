"""Catálogo de tipos de ausência (FOLGA, FÉRIAS, etc.)."""

from __future__ import annotations

import re
from functools import lru_cache

from ..models import AbsenceType

FALLBACK_ABSENCE_CODES = frozenset({"FOLGA", "FERIAS", "AFASTADO", "BH"})


def normalize_absence_code(value: str | None) -> str:
    """Preserva espaços internos; apenas trim e colapsa espaços repetidos."""
    return re.sub(r"\s+", " ", str(value or "").strip())


def absence_code_key(value: str | None) -> str:
    return normalize_absence_code(value).casefold()


@lru_cache(maxsize=1)
def _cached_active_code_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for code in AbsenceType.objects.filter(active=True).values_list("code", flat=True):
        canonical = normalize_absence_code(code)
        if canonical:
            mapping[absence_code_key(canonical)] = canonical
    if not mapping:
        for code in FALLBACK_ABSENCE_CODES:
            mapping[code.casefold()] = code
    return mapping


def clear_absence_type_cache() -> None:
    _cached_active_code_map.cache_clear()


def get_active_absence_codes() -> frozenset[str]:
    return frozenset(_cached_active_code_map().values())


def is_absence_code(value: str | None) -> bool:
    key = absence_code_key(value)
    if not key:
        return False
    return key in _cached_active_code_map()


def resolve_absence_code(value: str | None) -> str | None:
    return _cached_active_code_map().get(absence_code_key(value))


def get_absence_type_by_code(value: str | None) -> AbsenceType | None:
    canonical = resolve_absence_code(value)
    if not canonical:
        return None
    return AbsenceType.objects.filter(code__iexact=canonical, active=True).first()
