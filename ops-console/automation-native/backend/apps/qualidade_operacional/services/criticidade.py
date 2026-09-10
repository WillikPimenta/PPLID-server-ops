# -*- coding: utf-8 -*-
"""Regras de classificação efetiva da criticidade."""
from __future__ import annotations

from typing import Any
import unicodedata

CLIENTE_CLARO_FORMALIZACAO_ID = 9999
CLIENTE_CLARO_FORMALIZACAO_NOME = "CLARO - FORMALIZAÇÃO"
CLIENTE_GAQ_ID = 41
CLIENTE_GAQ_NOME = "GAQ"
WORKFLOW_CLARO_CONFER_ID = 9999
WORKFLOW_CLARO_CONFER_NOME = "CLARO - CONFER"

# População padrão EO/report executivo — excluído salvo filtro explícito id_cliente.
EXCLUDED_DEFAULT_REPORT_CLIENTE_IDS: frozenset[int] = frozenset(
    {CLIENTE_CLARO_FORMALIZACAO_ID, CLIENTE_GAQ_ID}
)


def normalize_criticidade_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip())
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def is_procedimento_forcado(id_cliente: Any) -> bool:
    try:
        return int(id_cliente) == CLIENTE_CLARO_FORMALIZACAO_ID
    except (TypeError, ValueError):
        return False


def is_excluded_default_report_client(id_cliente: Any = None, nome: str = "") -> bool:
    """Clientes fora do escopo padrão do report executivo / contestação operacional."""
    try:
        if int(id_cliente) in EXCLUDED_DEFAULT_REPORT_CLIENTE_IDS:
            return True
    except (TypeError, ValueError):
        pass
    normalized = normalize_criticidade_text(nome)
    if "formaliza" in normalized:
        return True
    return normalized == "gaq"


def is_reinspecao_tipo_registro(value: Any) -> bool:
    return normalize_criticidade_text(value) == "reinspecao"


def criticidade_label(
    value: Any,
    *,
    id_cliente: Any = None,
    tipo_registro: Any = None,
) -> str:
    """Classifica a falha; todos os cenários do cliente 9999 são Procedimento (peso 1)."""
    if is_reinspecao_tipo_registro(tipo_registro):
        return "Procedimento"
    if is_procedimento_forcado(id_cliente):
        return "Procedimento"

    normalized = normalize_criticidade_text(value)
    if "procedimento" in normalized:
        return "Procedimento"
    if "nao critica" in normalized or "nao critico" in normalized:
        return "Não Crítica"
    if "critica" in normalized or "critico" in normalized:
        return "Crítica"
    return "Não informada"
