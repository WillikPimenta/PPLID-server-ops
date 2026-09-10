# -*- coding: utf-8 -*-
"""Normalização de localidade (paridade com report_falhas/filters.py)."""
from __future__ import annotations

from django.db.models import Q

from report_falhas.filters import LOCALIDADES_ALVO
from report_falhas.io.data_loader import normalize_text, safe_str

CANONICAL_LOCALIDADES = tuple(LOCALIDADES_ALVO.keys())


def _alias_norms(canon: str) -> set[str]:
    norms = {normalize_text(canon)}
    for alias in LOCALIDADES_ALVO.get(canon, ()):
        norms.add(normalize_text(alias))
    return norms


def canonicalize_localidade(raw) -> str:
    """Mapeia texto da planilha para Brasília, São Carlos ou mantém valor original."""
    s = safe_str(raw).strip()
    if not s or normalize_text(s) in ('geral', '-', '—', 'n/a', 'na'):
        return 'Geral'
    n = normalize_text(s)
    for canon in CANONICAL_LOCALIDADES:
        if n in _alias_norms(canon):
            return canon
    return s


def db_values_for_canonical(canon: str) -> list[str]:
    """Valores possíveis no banco para filtro __in (inclui grafias conhecidas)."""
    if canon not in LOCALIDADES_ALVO:
        return [canon]
    variants = {canon}
    for alias in LOCALIDADES_ALVO[canon]:
        variants.add(alias)
        variants.add(alias.title())
        variants.add(alias.upper())
    if canon == 'Brasília':
        variants.update(['Brasília', 'Brasilia', 'BSB'])
    elif canon == 'São Carlos':
        variants.update(['São Carlos', 'Sao Carlos', 'SANCA', 'Sanca'])
    return sorted(variants)


def filter_qs_by_localidade(qs, field: str, localidade: str):
    """Filtra queryset por localidade canônica ou Geral (sem filtro)."""
    if localidade == '__BLOCKED__':
        return qs.none()
    if not localidade or localidade == 'Geral':
        return qs
    if localidade in CANONICAL_LOCALIDADES:
        values = db_values_for_canonical(localidade)
        q = Q()
        for v in values:
            q |= Q(**{f'{field}__iexact': v})
        return qs.filter(q)
    return qs.filter(**{f'{field}__iexact': localidade})
