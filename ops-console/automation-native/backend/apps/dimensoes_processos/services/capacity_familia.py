"""Agrupamento de etapas em famílias para visão Capacity."""

from __future__ import annotations

from apps.dimensoes_processos.services.derivacao_etapa.auto_resolver import (
    ETAPA_AUTOMATIC_PREFIXES,
    etapa_bucket_label,
)

FAMILIA_SEPARATOR = " - "

# Aliases estáticos (casefold → família canônica). DB sobrescreve via CapacityFamiliaAlias.
FAMILIA_ALIAS_MAP: dict[str, str] = {
    "análise visual": "Análise Visual",
    "analise visual": "Análise Visual",
    "análise visual ii": "Análise Visual",
    "analise visual ii": "Análise Visual",
    "análise visual composta": "Análise Visual",
    "analise visual composta": "Análise Visual",
    "análise visual di": "Análise Visual",
    "analise visual di": "Análise Visual",
    "análise visual bl/gl": "Análise Visual",
    "analise visual bl/gl": "Análise Visual",
    "análise visual double check": "Análise Visual",
    "analise visual double check": "Análise Visual",
    "análise visual doc": "Análise Visual",
    "analise visual doc": "Análise Visual",
    "sobreposição": "Sobreposição",
    "sobreposicao": "Sobreposição",
    "validação cadastral ce": "Validação Cadastral CE",
    "validacao cadastral ce": "Validação Cadastral CE",
    "prequality doc id": "PreQuality DOC ID",
    "comparação de selfie- vivo": "Comparação de Selfie",
    "comparacao de selfie- vivo": "Comparação de Selfie",
}

_ALIAS_MAP_CACHE: dict[str, str] | None = None


def invalidate_familia_alias_cache() -> None:
    global _ALIAS_MAP_CACHE
    _ALIAS_MAP_CACHE = None


def familia_alias_key(alias_origem: str) -> str:
    return (alias_origem or "").strip().casefold()


def get_familia_alias_map() -> dict[str, str]:
    global _ALIAS_MAP_CACHE
    if _ALIAS_MAP_CACHE is not None:
        return _ALIAS_MAP_CACHE

    merged = dict(FAMILIA_ALIAS_MAP)
    from apps.dimensoes_processos.models import CapacityFamiliaAlias

    for row in CapacityFamiliaAlias.objects.filter(ativo=True).only(
        "alias_key", "familia_canonica"
    ):
        merged[row.alias_key] = row.familia_canonica
    _ALIAS_MAP_CACHE = merged
    return _ALIAS_MAP_CACHE


def extract_familia(nome: str) -> str:
    """Regra v1: prefixo antes do primeiro separador `` - ``."""
    normalized = (nome or "").strip()
    if not normalized:
        return ""
    head, _sep, _tail = normalized.partition(FAMILIA_SEPARATOR)
    return head.strip() or normalized


def normalize_familia(familia: str) -> str:
    """Aplica catálogo estático + aliases editáveis (DB)."""
    token = (familia or "").strip()
    if not token:
        return token
    return get_familia_alias_map().get(token.casefold(), token)


def extract_familia_normalized(nome: str) -> str:
    return normalize_familia(extract_familia(nome))


def extract_familia_catalog(nome: str) -> str:
    """Prefixo via catálogo longest-match (auto_resolver) + fallback v1."""
    return etapa_bucket_label(nome)


def familias_differ(nome: str) -> bool:
    """True quando v1 e catálogo produzem famílias distintas."""
    return extract_familia(nome) != extract_familia_catalog(nome)


def is_single_word_familia(familia: str) -> bool:
    """Família v1 sem espaços internos (potencialmente genérica)."""
    token = (familia or "").strip()
    return bool(token) and " " not in token


def has_familia_separator(nome: str) -> bool:
    return FAMILIA_SEPARATOR in (nome or "")


def is_automatic_stage_name(nome: str) -> bool:
    normalized = (nome or "").strip()
    if not normalized:
        return False
    for prefix, _bucket in ETAPA_AUTOMATIC_PREFIXES:
        if normalized.startswith(prefix):
            return True
    return False
