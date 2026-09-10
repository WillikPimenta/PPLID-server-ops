# -*- coding: utf-8 -*-
"""Normalização de nomes CSV ↔ Megazord (derivacao_etapa)."""
from __future__ import annotations

import re

from apps.controle_sla.services.sla_eval import _norm_name
from apps.dimensoes_processos.services.meta_etapa_lookup import normalize_etapa_nome

# Prefixos operacionais BrFlow antes do nome Megazord (DE07 - …, RMPLUS12H - …)
_WF_PREFIX_RE = re.compile(
    r"^(DE\d+|RMPLUS\d*|RMPLUS(?:\s*-\s*\d+)?|AMX-PreVenda|DOC DIGITAL BETS|"
    r"BMG - Credencial Unica|NOVOVIVO)$",
    re.IGNORECASE,
)


def fix_csv_dashes(text: str) -> str:
    """Corrige travessão cp1252 lido como latin-1 (\\x96 / U+0096)."""
    return (
        (text or "")
        .replace("\x96", "-")
        .replace("\u0096", "-")
    )


def normalize_csv_name(text: str) -> str:
    """Normaliza nome de cliente/workflow do CSV."""
    text = fix_csv_dashes(text)
    text = _norm_name(text)
    text = re.sub(r"\bGer\.\b", "Gerenciador", text, flags=re.IGNORECASE)
    return text.strip()


def normalize_csv_name_key(text: str) -> str:
    return normalize_csv_name(text).casefold()


def normalize_csv_etapa_key(text: str) -> str:
    text = fix_csv_dashes(text)
    text = re.sub(r"\bGer\.\b", "Gerenciador", text, flags=re.IGNORECASE)
    return normalize_etapa_nome(text)


def workflow_name_candidates(name: str) -> list[str]:
    """Gera sufixos candidatos para match de workflow (prefixo operacional removido)."""
    raw = normalize_csv_name(name)
    if not raw:
        return []

    parts = [p.strip() for p in raw.split(" - ") if p.strip()]
    candidates: list[str] = []
    seen: set[str] = set()

    def add(c: str) -> None:
        key = c.casefold()
        if key and key not in seen:
            seen.add(key)
            candidates.append(c)

    add(raw)
    for i in range(len(parts)):
        chunk = " - ".join(parts[i:])
        add(chunk)
        if i == 0 and _WF_PREFIX_RE.match(parts[0]) and len(parts) > 1:
            add(" - ".join(parts[1:]))

    return sorted(candidates, key=lambda x: len(x), reverse=True)
