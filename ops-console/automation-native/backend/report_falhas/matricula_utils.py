# -*- coding: utf-8 -*-
"""Utilitários compartilhados de matrícula, cenário e normalização de colunas."""

from __future__ import annotations

import re
import unicodedata

from report_falhas.io.data_loader import normalize_text, safe_str


def clean_matricula_unified(raw: str) -> tuple[str, str]:
    """
    Limpeza centralizada de matrícula concatenada com nome.

    Retorna: (matricula_limpa, nome_extraido)
    """
    s = safe_str(raw).strip()
    if not s:
        return "", ""

    s = s.replace("\u00a0", " ")
    try:
        s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    except Exception:
        pass

    nome_raw = ""

    m = re.search(
        r"Nome\s+do\s+agente\s*[:\-]?\s*([A-Z][A-Za-záéíóúâêôãõ\s]*[A-Z]?)",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        nome_raw = safe_str(m.group(1)).strip()
        if nome_raw.lower() not in ("do agente", "agente"):
            s = s[: m.start()].strip()
        else:
            nome_raw = ""

    if not nome_raw:
        m = re.search(r"Nome\s*[:\-]\s*([A-Z][A-Za-záéíóúâêôãõ\s]*)", s, flags=re.IGNORECASE)
        if m:
            nome_raw = safe_str(m.group(1)).strip()
            if nome_raw.lower() not in ("agente", "do agente", ""):
                s = s[: m.start()].strip()
            else:
                nome_raw = ""

    mm = re.search(r"([A-Za-z]?\d{3,}[A-Za-z]?)", s)
    mat = mm.group(1) if mm else ""
    return mat, nome_raw


def resolve_agent_name(
    mat_n: str,
    mat_raw: str = "",
    nome_map: dict | None = None,
    row=None,
) -> str:
    """Resolve nome do agente: mapa HC/base, matrícula concatenada ou coluna da linha."""
    from report_falhas.io.data_loader import norm_matricula, safe_str

    nome_map = nome_map or {}
    _, nome_from_raw = clean_matricula_unified(safe_str(mat_raw))
    nome = safe_str(nome_map.get(norm_matricula(safe_str(mat_n or mat_raw)), ""))
    if not nome:
        nome = safe_str(nome_from_raw)
    if not nome and row is not None:
        try:
            getter = getattr(row, "get", None)
            if callable(getter):
                nome = safe_str(getter("Nome Agente", "") or getter("Nome do agente", ""))
        except Exception:
            pass
    return nome


def format_cenario_text(x) -> str:
    """Deixa o texto do cenário mais legível (insere espaços em padrões comuns)."""
    s = safe_str(x)
    if not s:
        return ""

    s = re.sub(r"(?i)^(n[aã]o\s*sinalizado|n[aã]osinalizado)", "NÃO SINALIZADO", s)
    s = re.sub(r"(?i)^(sinalizac[aã]o\s*incorreta|sinalizac[aã]oincorreta)", "SINALIZAÇÃO INCORRETA", s)
    s = re.sub(r"(?<=\w)-(?=\w)", " - ", s)
    s = re.sub(r",(?=\S)", ", ", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    return s


def normalize_categoria_falha(val: str) -> str:
    """Normaliza valores da coluna 'Categoria falha'."""
    raw = safe_str(val)
    if not raw:
        return ""
    norm = normalize_text(raw)
    if norm in ("critica", "falha critica", "falha crítica"):
        return "Crítica"
    return raw


def normalize_dificuldade(val: str) -> str:
    """Normaliza valores da coluna 'Nível de Dificuldade'."""
    raw = safe_str(val).strip()
    if not raw:
        return ""
    norm = normalize_text(raw)
    if norm in ("facil", "fácil"):
        return "Fácil"
    if norm in ("medio", "médio"):
        return "Médio"
    if norm in ("dificil", "difícil"):
        return "Difícil"
    if any(
        term in norm
        for term in (
            "cenarios avaliativos",
            "cenario avaliativo",
            "cenario avaliativos",
            "complexo",
        )
    ):
        return "Difícil"
    return raw
