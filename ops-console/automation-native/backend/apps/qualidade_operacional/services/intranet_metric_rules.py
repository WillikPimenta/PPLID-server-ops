# -*- coding: utf-8 -*-
"""Regras canônicas auditado/falha para projeção Intranet → EO.

``resultado_qualidade`` é calculado na fonte central e identifica a existência
da falha. A contabilização exige também ``status_falha`` em ``ativa`` ou
``mantida``; vazio, retirada e qualquer outro estado ficam fora do numerador.
Registros ``em_validacao`` ficam também fora do denominador até a revisão.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.qualidade_operacional.services.normalize import clean_text, fold_ascii_upper

STATUS_ATIVA_CANON = AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA
STATUS_MANTIDA_CANON = AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA
STATUS_EM_VALIDACAO_CANON = AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO
STATUS_FALHA_CONTABILIZAVEL = frozenset({STATUS_ATIVA_CANON, STATUS_MANTIDA_CANON})
TIPO_SEM_FALHA_FOLD = "SEM FALHA"
PROCEDENTE_FOLD = "PROCEDENTE"
IMPROCEDENTE_FOLD = "IMPROCEDENTE"


def normalize_status_falha(value: object | None) -> str:
    """Normaliza caixa/espaços sem ampliar os estados aceitos pelo modelo."""
    text = clean_text(value).lower()
    if not text:
        return ""
    folded = fold_ascii_upper(text)
    if folded == "ATIVA":
        return STATUS_ATIVA_CANON
    if folded == "MANTIDA":
        return STATUS_MANTIDA_CANON
    return text


def is_status_falha_ativo(value: object | None) -> bool:
    return normalize_status_falha(value) == STATUS_ATIVA_CANON


def is_status_falha_contabilizavel(value: object | None) -> bool:
    """Somente falhas ativas ou mantidas entram no indicador de Qualidade."""
    return normalize_status_falha(value) in STATUS_FALHA_CONTABILIZAVEL


def normalize_tipo_falha_original(value: object | None) -> str:
    return clean_text(value)


def normalize_procedencia(value: object | None) -> str:
    """Procedência operacional (campo status do tratado)."""
    folded = fold_ascii_upper(clean_text(value))
    if folded == PROCEDENTE_FOLD:
        return "Procedente"
    if folded == IMPROCEDENTE_FOLD:
        return "Improcedente"
    return ""


def normalize_origem_tratado(value: object | None) -> str:
    text = clean_text(value).lower()
    if not text:
        return ""
    return text


def normalize_tipo_registro(value: object | None) -> str:
    text = clean_text(value).lower()
    if not text:
        return ""
    return text


def normalize_resultado_qualidade(value: object | None) -> str:
    text = clean_text(value).lower()
    valid = {
        AuditoriaFalhaCadastro.RESULTADO_COM_FALHA,
        AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA,
        AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO,
    }
    return text if text in valid else AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO


@dataclass(frozen=True)
class IntranetMetricClassification:
    is_auditado: bool
    is_falha: bool
    skip_reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)
    status_falha_canon: str = ""
    tipo_falha_original: str = ""
    tipo_registro: str = ""
    origem: str = ""
    procedencia: str = ""
    resultado_qualidade: str = ""


def classify_intranet_source(
    *,
    resultado_qualidade: object | None,
    status_falha: object | None = None,
    tipo_falha: object | None = None,
    procedencia_raw: object | None = None,
    tipo_registro: object | None = None,
    origem: object | None = None,
) -> IntranetMetricClassification:
    """Classifica pelo resultado consolidado e valida o estado contabilizável."""
    status_canon = normalize_status_falha(status_falha)
    tipo_original = normalize_tipo_falha_original(tipo_falha)
    procedencia = normalize_procedencia(procedencia_raw)
    registro = normalize_tipo_registro(tipo_registro)
    origem_norm = normalize_origem_tratado(origem)
    resultado = normalize_resultado_qualidade(resultado_qualidade)
    warnings: list[str] = []

    if status_canon == STATUS_EM_VALIDACAO_CANON:
        warnings.append("status_falha_em_validacao")
        return IntranetMetricClassification(
            is_auditado=False,
            is_falha=False,
            skip_reason="status_falha_em_validacao",
            warnings=tuple(warnings),
            status_falha_canon=status_canon,
            tipo_falha_original=tipo_original,
            tipo_registro=registro,
            origem=origem_norm,
            procedencia=procedencia or "",
            resultado_qualidade=resultado,
        )

    if resultado == AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO:
        warnings.append("resultado_qualidade_nao_classificado")
        return IntranetMetricClassification(
            is_auditado=False,
            is_falha=False,
            skip_reason="resultado_qualidade_nao_classificado",
            warnings=tuple(warnings),
            status_falha_canon=status_canon,
            tipo_falha_original=tipo_original,
            tipo_registro=registro,
            origem=origem_norm,
            procedencia=procedencia or "",
            resultado_qualidade=resultado,
        )

    is_falha = (
        resultado == AuditoriaFalhaCadastro.RESULTADO_COM_FALHA
        and is_status_falha_contabilizavel(status_canon)
    )
    if resultado == AuditoriaFalhaCadastro.RESULTADO_COM_FALHA and not is_falha:
        warnings.append("status_falha_nao_contabilizavel")

    return IntranetMetricClassification(
        is_auditado=True,
        is_falha=is_falha,
        warnings=tuple(warnings),
        status_falha_canon=status_canon,
        tipo_falha_original=tipo_original,
        tipo_registro=registro,
        origem=origem_norm,
        procedencia=procedencia or "",
        resultado_qualidade=resultado,
    )


def classify_source_record(source: AuditoriaFalhaCadastro) -> IntranetMetricClassification:
    return classify_intranet_source(
        resultado_qualidade=source.resultado_qualidade,
        status_falha=source.status_falha,
        tipo_falha=source.tipo_falha,
        procedencia_raw=source.status,
        tipo_registro=source.tipo_registro,
        origem=source.origem,
    )


def is_falha_efetiva(
    *,
    resultado_qualidade: str | None,
    status_falha: object | None,
) -> bool:
    return (
        normalize_resultado_qualidade(resultado_qualidade)
        == AuditoriaFalhaCadastro.RESULTADO_COM_FALHA
        and is_status_falha_contabilizavel(status_falha)
    )


def is_sem_falha(tipo_falha: str | None) -> bool:
    return fold_ascii_upper(clean_text(tipo_falha)) == TIPO_SEM_FALHA_FOLD
