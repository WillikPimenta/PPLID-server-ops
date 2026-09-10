# -*- coding: utf-8 -*-
"""Classificação macro de falhas (categoria + severidade)."""
from __future__ import annotations

import pandas as pd

from report_brb.brb_normalize import normalize_text, strip_accents

CATEGORIA_LABELS = {
    "ADULTERACAO_VISUAL": "Adulteração visual",
    "FOTO_SELFIE": "Foto / Selfie",
    "SOBREPOSICAO_FOTO": "Sobreposição na foto",
    "DOCUMENTO_ILEGIVEL": "Documento ilegível",
    "TIPIFICACAO": "Tipificação incorreta",
    "DOCUMENTO_AUSENTE": "Documento ausente",
    "CPF_DIVERGENTE": "CPF divergente",
    "FRAUDE_NAO_IDENTIFICADA": "Fraude não identificada",
    "SINALIZACAO_INCORRETA": "Sinalização incorreta",
    "OUTROS": "Outros",
}

SEVERIDADE_LABELS = {
    "CRITICA": "Crítica",
    "ALTA": "Alta",
    "MEDIA": "Média",
    "BAIXA": "Baixa",
    "INDEFINIDA": "Indefinida",
}

# Regras de categoria — ordem importa (primeira correspondência vence).
_CATEGORIA_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("FRAUDE_NAO_IDENTIFICADA", ("FRAUDE NAO IDENTIFICADA", "FRAUDE NÃO IDENTIFICADA", "FALSO NEGATIVO")),
    ("ADULTERACAO_VISUAL", ("FORMATA", "FONTE", "DESALINHAMENTO", "ADULTERAD", "RASUR")),
    ("SOBREPOSICAO_FOTO", ("SOBREPOSIC", "SOBRE POSIC")),
    ("FOTO_SELFIE", ("SELFIE", "FOTO DIVERGENTE", "COMPARACAO FACIAL", "FACE ENCONTRADA")),
    ("DOCUMENTO_ILEGIVEL", ("ILEGIV", "BAIXA QUALIDADE", "CORTAD")),
    ("TIPIFICACAO", ("TIPIFIC", "TIPIFICAD")),
    ("DOCUMENTO_AUSENTE", ("DOCUMENTO AUSENTE", "DOC. AUSENTE", "DOC AUSENTE")),
    ("CPF_DIVERGENTE", ("CPF DIVERG", "REGRA DE CPF")),
    ("SINALIZACAO_INCORRETA", ("SINALIZACAO INCORRETA", "SINALIZAÇÃO INCORRETA", "FALSO POSITIVO", "SINALIZACAO VALIDADA")),
]

CATEGORIA_RECOMENDACOES: dict[str, str] = {
    "ADULTERACAO_VISUAL": (
        "Reforçar checklist de fonte, formatação e alinhamento; incluir exemplos visuais no treinamento; "
        "revisar regra automática de detecção de adulteração visual; monitorar reincidência por agente."
    ),
    "FOTO_SELFIE": (
        "Treinar captura de selfie e comparação facial; revisar iluminação e orientação ao cliente; "
        "auditar casos de foto divergente com amostragem semanal."
    ),
    "SOBREPOSICAO_FOTO": (
        "Revisar procedimento de captura sem sobreposição; alertar equipe sobre manipulação de imagem; "
        "priorizar casos com indício de fraude."
    ),
    "DOCUMENTO_ILEGIVEL": (
        "Orientar qualidade mínima de digitalização; recaptura quando ilegível; "
        "ajustar parâmetros de OCR/leitura automática."
    ),
    "TIPIFICACAO": (
        "Reciclagem sobre tipos de documento aceitos; checklist de classificação antes do envio."
    ),
    "DOCUMENTO_AUSENTE": (
        "Validar checklist de documentos obrigatórios por etapa; bloquear avanço sem anexo."
    ),
    "CPF_DIVERGENTE": (
        "Revisar regra de validação de CPF; dupla checagem em casos recorrentes."
    ),
    "FRAUDE_NAO_IDENTIFICADA": (
        "Plano emergencial de revisão de regras antifraude; ampliar amostragem manual; "
        "escalar casos críticos à auditoria."
    ),
    "SINALIZACAO_INCORRETA": (
        "Calibrar regras para reduzir falso positivo; feedback aos analistas que sinalizaram indevidamente."
    ),
    "OUTROS": (
        "Revisar manualmente amostra dos casos não classificados e atualizar dicionário de categorias."
    ),
}


def _text_blob(*parts) -> str:
    return strip_accents(" ".join(normalize_text(p, upper=True) for p in parts if normalize_text(p)))


def classificar_categoria_falha(texto: str, tendencia: str = "", fn_fp: str = "") -> str:
    """Retorna código macro da categoria (ex.: ADULTERACAO_VISUAL)."""
    blob = _text_blob(texto, tendencia, fn_fp)
    if not blob:
        return "OUTROS"
    if "NAO FRaude".upper().replace(" ", "") in blob.replace(" ", "") or "NAO FRAUDE" in blob:
        return "OUTROS"
    for code, keywords in _CATEGORIA_RULES:
        if any(k in blob for k in keywords):
            return code
    if "SELFIE" in blob:
        return "FOTO_SELFIE"
    if "ILEGIV" in blob:
        return "DOCUMENTO_ILEGIVEL"
    return "OUTROS"


def classificar_severidade(row: pd.Series) -> str:
    """Classifica severidade com base em múltiplos campos da linha."""
    blob = _text_blob(
        row.get("descricao_padrao", ""),
        row.get("Novo cenário", row.get("Cenário", "")),
        row.get("MOTIVO DA FALHA", ""),
        row.get("Tendência", ""),
    )
    possivel_ataque = bool(row.get("possivel_ataque", False))
    fn_fp = normalize_text(row.get("fn_fp", ""), upper=True)
    conforme = normalize_text(row.get("classificacao_conforme", ""), upper=True)

    if conforme == "NAO_FALHA" or conforme == "NAO FALHA":
        return "BAIXA"

    critica_kw = (
        "FRAUDE NAO IDENTIFICADA",
        "FRAUDADOR",
        "BASE DE FRAUDADOR",
        "MANIPULAD",
        "ADULTERAD",
        "ATAQUE",
        "QUADRILHA",
    )
    if possivel_ataque or any(k in blob for k in critica_kw):
        return "CRITICA"
    if "FRAUDE NAO IDENTIFICADA" in blob or (fn_fp == "FN" and "ADULTER" in blob):
        return "CRITICA"

    alta_kw = ("SOBREPOSIC", "FORMATA", "FONTE", "DESALINHAMENTO", "ADULTERAD", "SELFIE MANIPUL")
    if any(k in blob for k in alta_kw):
        return "ALTA"

    media_kw = ("TIPIFIC", "AUSENTE", "CPF", "INCOMPLETO", "ETAPA INCORRETA", "SINALIZACAO INCORRETA")
    if any(k in blob for k in media_kw):
        return "MEDIA"

    if not blob or blob in ("OUTROS", "(VAZIO)"):
        return "INDEFINIDA"

    if fn_fp == "FP":
        return "MEDIA"

    return "BAIXA"


def categoria_label(code: str) -> str:
    return CATEGORIA_LABELS.get(code, code)


def severidade_label(code: str) -> str:
    return SEVERIDADE_LABELS.get(code, code)
