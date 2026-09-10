from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

from apps.auditoria.models import AuditoriaFalhaCadastro
from apps.auditoria.services.catalog_items import get_valid_tipo_falha_values
from apps.auditoria.services.text_format import (
    is_tipo_falha_automatico,
    is_tipo_falha_colaborador,
    is_tipo_falha_sem_falha,
)

VALID_TIPO_REGISTRO = {
    AuditoriaFalhaCadastro.REGISTRO_AUDITORIA,
    AuditoriaFalhaCadastro.REGISTRO_CONTESTACAO,
}


def normalize_tipo_registro(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in VALID_TIPO_REGISTRO:
        return normalized
    return AuditoriaFalhaCadastro.REGISTRO_AUDITORIA


def validate_falha_payload(data: dict) -> dict[str, str]:
    errors: dict[str, str] = {}

    protocolo = (data.get("protocolo") or "").strip()
    if not protocolo:
        errors["protocolo"] = "Informe o protocolo."

    tipo_falha = (data.get("tipo_falha") or "").strip()
    valid_types = get_valid_tipo_falha_values()
    if not tipo_falha:
        errors["tipo_falha"] = "Selecione o tipo de falha."
    elif tipo_falha not in valid_types:
        errors["tipo_falha"] = "Tipo de falha inválido."

    usuario = (data.get("usuario") or "").strip()
    if not is_tipo_falha_automatico(tipo_falha) and not usuario:
        errors["usuario"] = "Selecione o usuário."

    if is_tipo_falha_colaborador(tipo_falha) and not (data.get("observacao") or "").strip():
        errors["observacao"] = "Informe a observação para falhas do tipo Colaborador."

    demanda_url = (data.get("demanda_url") or "").strip()
    if demanda_url:
        validator = URLValidator()
        try:
            validator(demanda_url)
        except ValidationError:
            errors["demanda_url"] = "Informe um link válido para a demanda."

    return errors


def validate_falha_finalize_record(record) -> dict[str, str]:
    """Valida no servidor os mesmos campos exigidos pela grade ao finalizar."""
    if is_tipo_falha_sem_falha(record.tipo_falha):
        return {}

    labels = {
        "modulo": "módulo",
        "novo_resultado": "novo resultado",
        "sinalizacao": "sinalização",
        "motivo_falha": "cenário",
        "nivel_dificuldade": "nível",
        "tipo_documento": "tipo de documento",
        "uf_documento": "UF",
        "qualidade_imagem": "qualidade da imagem",
    }
    errors = {
        field: f"Preencha {label}."
        for field, label in labels.items()
        if not str(getattr(record, field, "") or "").strip()
    }
    if not is_tipo_falha_automatico(record.tipo_falha) and not str(record.etapa_falha or "").strip():
        errors["etapa_falha"] = "Preencha etapa da falha."
    return errors
