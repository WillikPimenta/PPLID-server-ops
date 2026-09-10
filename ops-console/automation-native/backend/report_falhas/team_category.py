# -*- coding: utf-8 -*-
"""Mapeamento Team (HC) → Team/Category para relatórios."""

from __future__ import annotations

from report_falhas.io.data_loader import normalize_text, safe_str

CATEGORIA_OUTROS = "Outros"
CATEGORIA_OPERACIONAL = "Operacional"
CATEGORIA_QUALIDADE = "Qualidade"
CATEGORIA_NAO_CLASSIFICADO = "Não classificado"

CATEGORIAS_ORDEM = (
    CATEGORIA_OPERACIONAL,
    CATEGORIA_QUALIDADE,
    CATEGORIA_OUTROS,
    CATEGORIA_NAO_CLASSIFICADO,
)

# Chaves normalizadas via normalize_text()
TEAM_TO_CATEGORIA: dict[str, str] = {
    "planejamento": CATEGORIA_OUTROS,
    "customer experience": CATEGORIA_OUTROS,
    "processos": CATEGORIA_OUTROS,
    "gerencia/fraud": CATEGORIA_OUTROS,
    "operacional/fraud": CATEGORIA_OPERACIONAL,
    "operacional/compliance": CATEGORIA_OPERACIONAL,
    "capacitacao/fraud": CATEGORIA_QUALIDADE,
    "auditoria/compliance": CATEGORIA_QUALIDADE,
    "auditoria/fraud": CATEGORIA_QUALIDADE,
    "contestacao/fraud": CATEGORIA_QUALIDADE,
}


def map_team_to_categoria(team_raw: str) -> str:
    """Retorna Outros / Operacional / Qualidade ou Não classificado."""
    raw = safe_str(team_raw).strip()
    if not raw:
        return CATEGORIA_NAO_CLASSIFICADO
    norm = normalize_text(raw)
    return TEAM_TO_CATEGORIA.get(norm, CATEGORIA_NAO_CLASSIFICADO)
