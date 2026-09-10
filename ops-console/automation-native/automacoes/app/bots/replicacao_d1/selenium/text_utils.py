# -*- coding: utf-8 -*-
"""Utilitários de texto para XPath, screenshots e classificação de linhas."""
from __future__ import annotations

import re


def xpath_escape_texto(texto: str) -> str:
    """Escapa texto para uso seguro em literais XPath entre aspas duplas."""
    if '"' not in str(texto):
        return f'"{texto}"'
    if "'" not in str(texto):
        return f"'{texto}'"
    partes = str(texto).split('"')
    return "concat(" + ', \'"\', '.join(f'"{p}"' for p in partes) + ")"


def sanitizar_sufixo_screenshot(nome: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', "_", str(nome).strip())[:80] or "workflow"


def classificar_situacao_texto(texto: str) -> str:
    """Classifica situação da linha BRFlow: ATIVO, INATIVO ou DESCONHECIDO."""
    bruto = str(texto or "").strip()
    if not bruto:
        return "DESCONHECIDO"
    t = bruto.casefold()
    if "inativ" in t:
        return "INATIVO"
    if t in ("n", "nao", "não", "0", "false", "no"):
        return "INATIVO"
    if "ativ" in t:
        return "ATIVO"
    if t in ("s", "sim", "1", "true", "yes"):
        return "ATIVO"
    return "DESCONHECIDO"


def linha_deve_ocultar_por_status_titulo(titulo: str | None) -> bool:
    """Espelha a regra do filtro JS: remover linha se title !== 'Ativo'."""
    return (titulo or "").strip() != "Ativo"
