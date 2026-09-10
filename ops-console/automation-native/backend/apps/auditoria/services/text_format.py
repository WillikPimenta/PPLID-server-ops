"""Formatação de rótulos dos catálogos de auditoria."""

from __future__ import annotations

UF_CODES = {
    "ac", "al", "ap", "am", "ba", "ce", "df", "es", "go", "ma", "mt", "ms", "mg",
    "pa", "pb", "pr", "pe", "pi", "rj", "rn", "rs", "ro", "rr", "sc", "sp", "se", "to",
}

TIPO_FALHA_AUTOMATICO = "Automático"
TIPO_FALHA_SEM_FALHA = "Sem Falha"
TIPO_FALHA_COLABORADOR = "Colaborador"


def format_auditoria_label(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return text
    if text == "-":
        return text
    if text.isdigit():
        return text
    lowered = text.lower()
    if lowered in UF_CODES:
        return text.upper()
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return text.title()


def is_tipo_falha_automatico(value: str) -> bool:
    return format_auditoria_label((value or "").strip()) == TIPO_FALHA_AUTOMATICO


def is_tipo_falha_sem_falha(value: str) -> bool:
    return format_auditoria_label((value or "").strip()) == TIPO_FALHA_SEM_FALHA


def is_tipo_falha_colaborador(value: str) -> bool:
    return format_auditoria_label((value or "").strip()) == TIPO_FALHA_COLABORADOR
