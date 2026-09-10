# -*- coding: utf-8 -*-
"""Normalização de campos dos TSV de Qualidade Operacional."""
from __future__ import annotations

import unicodedata
from datetime import date, datetime

_SENTINELS = frozenset(
    {
        "",
        "-",
        "não se aplica",
        "nao se aplica",
        "n/a",
        "na",
        "null",
        "none",
        "#n/d",
        "#nd",
    }
)

# Tipificação canônica (auditados.tipo_conclusao ↔ falhas.tipo_falha)
# Mapeamento / Sistema ≡ Automático (mesma regra de negócio)
# Residual (em branco, Biometria, etc.) ≡ Manual — só existem 3 cards EO
TIPO_CONCLUSAO_CANONICAL = {
    "MANUAL": "Manual",
    "AUTOMATICO": "Automático",
    "PROCESSUAL": "Processual",
    "MAPEAMENTO": "Automático",
    "SISTEMA": "Automático",
}

TIPO_FALHA_CANONICOS = frozenset({"Manual", "Automático", "Processual"})

# Valores que equivalem a cada canônico (filtros SQL)
TIPO_CONCLUSAO_MATCH_VALUES: dict[str, tuple[str, ...]] = {
    "Manual": ("Manual", "MANUAL"),
    "Automático": (
        "Automático",
        "Automatico",
        "AUTOMATICO",
        "AUTOMÁTICO",
        "Mapeamento",
        "MAPEAMENTO",
        "mapeamento",
        "Sistema",
        "SISTEMA",
        "sistema",
    ),
    "Processual": ("Processual", "PROCESSUAL"),
}


NIVEL_DIFICULDADE_NAO_INFORMADO = "Não informado"


def clean_text(value: object | None, *, max_len: int | None = None) -> str:
    if value is None:
        return ""
    text = str(value).replace("\ufeff", "").strip()
    if text.lower() in _SENTINELS:
        return ""
    if max_len is not None and len(text) > max_len:
        return text[:max_len]
    return text


def nivel_dificuldade_efetivo(
    nivel_dificuldade_confer: object | None,
    nivel_dificuldade: object | None,
) -> str:
    """Resolve a dificuldade exibida e filtrada usando uma única precedência."""
    return (
        clean_text(nivel_dificuldade_confer, max_len=128)
        or clean_text(nivel_dificuldade, max_len=128)
        or NIVEL_DIFICULDADE_NAO_INFORMADO
    )


def fold_ascii_upper(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


def normalize_tipo_conclusao(value: object | None) -> str:
    """Normaliza Tipo de conclusão / Tipo de Falha para Manual | Automático | Processual.

    Qualquer valor residual (em branco, Biometria, Regra De Negócio, …) vira **Manual**.
    Não existe bucket Outros na tipificação EO.
    """
    text = clean_text(value, max_len=64)
    if not text:
        return "Manual"
    folded = fold_ascii_upper(text)
    if folded in TIPO_CONCLUSAO_CANONICAL:
        return TIPO_CONCLUSAO_CANONICAL[folded]
    return "Manual"


def is_processual_tipificacao(value: object | None) -> bool:
    """Indica se a tipificação representa uma falha processual."""
    return normalize_tipo_conclusao(value) == "Processual"


def tipo_conclusao_match_values(canon_or_raw: str) -> tuple[str, ...]:
    """Valores a usar em filtros iexact para cobrir aliases (ex.: Mapeamento → Automático)."""
    canon = normalize_tipo_conclusao(canon_or_raw) or (canon_or_raw or "").strip()
    if not canon:
        return ()
    return TIPO_CONCLUSAO_MATCH_VALUES.get(canon, (canon,))


def is_manual_tipificacao_bucket(canon_or_raw: str) -> bool:
    return normalize_tipo_conclusao(canon_or_raw) == "Manual"


def is_contestacao_tipo_analise(value: object | None) -> bool:
    """True quando Tipo de análise (ou Módulo) indica contestação.

    Na base de Qualidade, contestações aparecem como tipos de análise
    (ex.: Contestação Compliance, Contestação Externa, Contestação Claro)
    e, nas falhas, também como ``modulo='Contestação'``.
    """
    folded = fold_ascii_upper(clean_text(value))
    return "CONTEST" in folded


def parse_date_br(value: object | None) -> date | None:
    text = clean_text(value)
    if not text:
        return None
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def parse_int(value: object | None) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        return int(float(text.replace(",", ".")))
    except (TypeError, ValueError):
        return None


def normalize_matricula(value: object | None) -> str:
    text = clean_text(value, max_len=64).lower()
    if text in {"risk manager", "riskmanager"}:
        return text
    return text


_PUNCT_TO_HYPHEN = str.maketrans({
    "?": "-",
    "–": "-",
    "—": "-",
    "−": "-",
})


def normalize_nome_key_punct_variant(key: str) -> str:
    """Fallback: unifica travessão/en-dash/? em hífen. Usar só com match único."""
    if not key:
        return ""
    collapsed = " ".join(key.translate(_PUNCT_TO_HYPHEN).split())
    return collapsed
