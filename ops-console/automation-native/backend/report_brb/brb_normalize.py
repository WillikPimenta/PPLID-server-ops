# -*- coding: utf-8 -*-
"""Normalização de textos, protocolos, matrículas e demandas."""
from __future__ import annotations

import re
import unicodedata

import pandas as pd

from report_brb.brb_filters import norm_matricula, norm_protocolo, safe_str

EMPTY_MARKERS = {"", "-", "—", "–", "nan", "none", "null", "n/a", "na", "0", "00/00/0000"}


def strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_text(value, upper: bool = False) -> str:
    """Remove espaços extras, trata vazios e padroniza texto."""
    s = safe_str(value)
    if not s:
        return ""
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Cf")
    s = re.sub(r"\s+", " ", s).strip()
    if s.lower() in EMPTY_MARKERS:
        return ""
    if upper:
        s = strip_accents(s).upper()
    return s


def is_empty_value(value) -> bool:
    return normalize_text(value) == ""


def norm_demanda(value) -> str:
    s = normalize_text(value, upper=True)
    if not s:
        return ""
    m = re.search(r"(QI[\s\-]?\d+)", s, re.I)
    if m:
        return re.sub(r"[\s\-]", "-", m.group(1).upper())
    return s


# Mapeamento de descrições longas para rótulos curtos padronizados.
# Chaves são comparadas sem acento e em maiúsculas (com ou sem prefixo de sinalização).
DESC_ALIASES: dict[str, str] = {
    "FORMATACAO/FONTE E DESALINHAMENTO ADULTERADA": "Formatação/fonte adulterada",
    "FORMATACAO/FONTE NO DOCUMENTO DE IDENTIFICACAO ADULTERADA": "Doc. identificação adulterado",
    "DOC. ILEGIVEL": "Documento ilegível",
    "DOC. POSSUI SOBREPOSICAO NA FOTO": "Sobreposição na foto",
    "DOCUMENTO DE IDENTIFICACAO POSSUI SOBREPOSICAO NA FOTO": "Sobreposição na foto",
    "DOC. ADULTERADO": "Documento adulterado",
    "DOC. IDENTIFICACAO ADULTERADO": "Documento adulterado",
    "DOC. IDENTIFICACAO ADULTERADA": "Documento adulterado",
    "DOCUMENTO DE IDENTIFICACAO ADULTERADO": "Documento adulterado",
    "DOCUMENTO DE IDENTIFICACAO ADULTERADA": "Documento adulterado",
    "DOC. IDENTIFICACAO INCOMPLETO": "Documento incompleto",
    "DOCUMENTO DE IDENTIFICACAO INCOMPLETO": "Documento incompleto",
    "DOCUMENTO DE IDENTIFICACAO INCOMPLETA": "Documento incompleto",
    "DOC. IDENTIFICACAO ILEGIVEL": "Documento ilegível",
    "DOCUMENTO DE IDENTIFICACAO ILEGIVEL": "Documento ilegível",
    "SELFIE DO CLIENTE MANIPULADA": "Selfie manipulada",
    "DOCUMENTO AUSENTE": "Documento ausente",
    "DOC. INCOMPLETO": "Documento incompleto",
    "FOTO DIVERGENTE": "Foto divergente",
    "CPF DIVERGENTE": "CPF divergente",
    "FACE ENCONTRADA NA BASE DE FRAUDADORES": "Face em base de fraudadores",
    "FOTO DO CLIENTE INVÁLIDA": "Foto do cliente inválida",
    "FOTO DO CLIENTE INVALIDA": "Foto do cliente inválida",
    "FACE A DO DOCUMENTO DE IDENTIFICACAO INCOMPATIVEL COM A FACE B": "Face A incompatível com Face B",
    "FOTO DO CLIENTE DIVERGENTE COM A FOTO DO DOCUMENTO DE IDENTIFICACAO": "Foto do cliente divergente do documento",
}

_SINALIZACAO_PREFIXES: tuple[tuple[str, str], ...] = (
    ("NAO SINALIZADO", "Não sinalizado"),
    ("SINALIZACAO INCORRETA", "Sinalização incorreta"),
    ("SINALIZACAO VALIDADA", "Sinalização validada"),
)


def _split_sinalizacao_prefix(key: str, raw: str) -> tuple[str | None, str, str]:
    """Separa prefixo operacional (FN/FP) do restante do cenário."""
    for pattern, label in _SINALIZACAO_PREFIXES:
        if key.startswith(pattern):
            suffix_key = re.sub(rf"^{pattern}\s*[-–—:]?\s*", "", key).strip()
            suffix_raw = re.sub(rf"(?i)^{pattern}\s*[-–—:]?\s*", "", raw).strip()
            return label, suffix_key or key, suffix_raw or raw
    if key.startswith("SINALIZACAO CORRETA"):
        suffix_key = re.sub(r"^SINALIZACAO CORRETA\s*[-–—:]?\s*", "", key).strip()
        suffix_raw = re.sub(
            r"(?i)^sinaliza[cç][aã]o correta\s*[-–—:]?\s*", "", raw
        ).strip()
        return None, suffix_key or key, suffix_raw or raw
    return None, key, raw


def _match_alias(suffix_key: str, full_key: str) -> str | None:
    for pattern, label in DESC_ALIASES.items():
        if pattern in suffix_key or suffix_key.startswith(pattern[: min(30, len(pattern))]):
            return label
        if pattern in full_key or full_key.startswith(pattern[: min(30, len(pattern))]):
            return label
    return None


def _sentence_case_label(value: str) -> str:
    value = normalize_text(value)
    if not value:
        return value
    value = value.casefold()
    value = value[:1].upper() + value[1:]
    for acronym in ("CPF", "RG", "CNH", "ID"):
        value = re.sub(rf"(?i)\b{acronym}\b", acronym, value)
    return value


def _label_from_suffix(suffix_key: str, suffix_raw: str) -> str:
    alias = _match_alias(suffix_key, suffix_key)
    if alias:
        return alias
    ku = suffix_key
    if "SOBREPOSIC" in ku:
        return "Sobreposição na foto"
    if "ILEGIV" in ku:
        return "Documento ilegível"
    if "FORMATA" in ku and ("ADULTER" in ku or "DESALINH" in ku or "FONTE" in ku):
        return "Formatação/fonte adulterada"
    if "SELFIE" in ku and "MANIPUL" in ku:
        return "Selfie manipulada"
    if "FOTO DIVERGENTE" in ku or ("FOTO" in ku and "DIVERG" in ku):
        return "Foto divergente"
    if "CPF" in ku and "DIVERG" in ku:
        return "CPF divergente"
    if "TIPIFIC" in ku:
        return "Tipificação incorreta"
    if "DOCUMENTO AUSENTE" in ku or "DOC. AUSENTE" in ku or "IDENTIFICACAO AUSENTE" in ku:
        return "Documento ausente"
    if "FRAUDE NAO IDENTIFICADA" in ku:
        return "Fraude não identificada"
    if "FACE A" in ku and "FACE B" in ku:
        return "Face A incompatível com Face B"
    if len(suffix_raw) > 60:
        return _sentence_case_label(suffix_raw[:57]) + "…"
    fallback = normalize_text(suffix_raw)
    fallback = re.sub(r"(?i)^doc\.\s*", "Documento ", fallback)
    if not fallback:
        return fallback
    # Alguns cenários não possuem alias e chegam inteiros em caixa alta.
    # Exiba como frase, preservando siglas usuais.
    return _sentence_case_label(fallback)


def _compose_scenario_label(prefix: str | None, label: str) -> str:
    if prefix:
        return f"{prefix} · {label}"
    return label


def padronizar_descricao(texto) -> str:
    """Padroniza descrições repetidas para leitura executiva."""
    raw = normalize_text(texto)
    if not raw:
        return ""
    full_key = strip_accents(raw).upper()
    # Algumas cargas repetem o prefixo operacional (ex.: "FP - FP - ...").
    # Remove a repetição antes de separar prefixo e cenário.
    for pattern, _ in _SINALIZACAO_PREFIXES:
        repeated_key = rf"^({pattern}\s*[-:]\s*){pattern}\s*[-:]\s*"
        repeated_raw = rf"^({pattern}\s*[-:]\s*){pattern}\s*[-:]\s*"
        full_key = re.sub(repeated_key, r"\1", full_key).strip()
        raw = re.sub(repeated_raw, r"\1", raw, flags=re.I).strip()
    prefix, suffix_key, suffix_raw = _split_sinalizacao_prefix(full_key, raw)

    alias = _match_alias(suffix_key, full_key)
    if alias:
        return _compose_scenario_label(prefix, alias)

    label = _label_from_suffix(suffix_key, suffix_raw)
    return _compose_scenario_label(prefix, label)


def add_traceability_columns(df: pd.DataFrame, proto_col: str, mat_col: str, demanda_col: str | None = None) -> pd.DataFrame:
    """Colunas auxiliares de rastreabilidade em qualquer base."""
    out = df.copy()
    out["_protocolo_norm"] = out[proto_col].map(norm_protocolo) if proto_col in out.columns else ""
    out["_matricula_norm"] = out[mat_col].map(norm_matricula) if mat_col in out.columns else ""
    out["chave_protocolo_matricula"] = out["_protocolo_norm"] + "|" + out["_matricula_norm"]
    out["chave_protocolo"] = out["_protocolo_norm"]
    if demanda_col and demanda_col in out.columns:
        out["chave_demanda"] = out[demanda_col].map(norm_demanda)
    else:
        out["chave_demanda"] = ""
    return out
