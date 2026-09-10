from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model

from apps.access.registry import (
    QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
    QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
)
from apps.access.services.agents_with_permission import agents_with_any_permission
from apps.auditoria.models import AuditoriaControleRegistro
from apps.auditoria.services.atividade_sla import compute_controle_sla_seconds, format_sla_label

User = get_user_model()

TIPO_REMOCAO_BASE_NEGATIVA = AuditoriaControleRegistro.TIPO_REMOCAO_BASE_NEGATIVA
TIPO_REMOCAO_BASE_POSITIVA = AuditoriaControleRegistro.TIPO_REMOCAO_BASE_POSITIVA
TIPO_SOLICITACOES_IDAS_BIO = AuditoriaControleRegistro.TIPO_SOLICITACOES_IDAS_BIO

VALID_TIPOS = {
    TIPO_REMOCAO_BASE_NEGATIVA,
    TIPO_REMOCAO_BASE_POSITIVA,
    TIPO_SOLICITACOES_IDAS_BIO,
}

SCHEMAS: dict[str, list[dict[str, str]]] = {
    TIPO_REMOCAO_BASE_NEGATIVA: [
        {"key": "tipo_acao", "label": "Tipo de ação", "kind": "select", "catalog": "tipo_acao_controle", "group": "tipo"},
        {"key": "cpf", "label": "CPF", "kind": "cpf", "group": "cpf"},
        {"key": "cliente", "label": "Cliente", "kind": "text", "group": "identificacao"},
        {"key": "protocolo", "label": "Protocolo", "kind": "text", "group": "identificacao"},
        {"key": "workflow", "label": "Workflow", "kind": "select", "catalog": "workflow", "group": "identificacao"},
        {"key": "demanda_origem", "label": "Demanda de origem", "kind": "link", "group": "origem"},
        {"key": "motivo", "label": "Motivo", "kind": "select", "catalog": "motivo_base_negativa", "group": "origem"},
        {"key": "observacoes", "label": "Observações", "kind": "textarea", "group": "origem"},
        {"key": "selfie_higienizada", "label": "Selfie a ser higienizada", "kind": "image", "group": "origem"},
        {"key": "demanda_bio", "label": "Demanda de BIO", "kind": "link", "group": "andamento"},
        {"key": "data_abertura", "label": "Data de abertura", "kind": "date", "group": "andamento"},
        {"key": "data_retorno", "label": "Data de retorno", "kind": "date", "group": "andamento"},
        {
            "key": "situacao",
            "label": "Situação",
            "kind": "select",
            "options": "Nova|Finalizada",
            "group": "andamento",
        },
    ],
    TIPO_REMOCAO_BASE_POSITIVA: [
        {"key": "tipo_acao", "label": "Tipo de ação", "kind": "select", "catalog": "tipo_acao_controle", "group": "tipo"},
        {"key": "cpf", "label": "CPF", "kind": "cpf", "group": "cpf"},
        {"key": "cliente", "label": "Cliente", "kind": "text", "group": "identificacao"},
        {"key": "protocolo", "label": "Protocolo/ID Transação", "kind": "text", "group": "identificacao"},
        {"key": "workflow", "label": "Workflow", "kind": "select", "catalog": "workflow", "group": "identificacao"},
        {"key": "data_criacao", "label": "Data da criação", "kind": "date", "group": "identificacao"},
        {"key": "demanda_origem", "label": "Demanda de origem", "kind": "link", "group": "origem"},
        {"key": "cliente_origem", "label": "Cliente de origem", "kind": "text", "group": "origem"},
        {"key": "motivo", "label": "Motivo", "kind": "select", "catalog": "motivo_base_negativa", "group": "origem"},
        {"key": "detalhamento", "label": "Detalhamento", "kind": "textarea", "group": "origem"},
        {"key": "observacoes", "label": "Observações", "kind": "textarea", "group": "origem"},
        {"key": "selfie_higienizada", "label": "Selfie a ser higienizada", "kind": "image", "group": "origem"},
        {"key": "demanda_bio", "label": "Demanda de BIO", "kind": "link", "group": "andamento"},
        {"key": "data_abertura", "label": "Data de abertura", "kind": "date", "group": "andamento"},
        {"key": "data_retorno", "label": "Data de retorno / higienização", "kind": "date", "group": "andamento"},
        {
            "key": "situacao",
            "label": "Situação",
            "kind": "select",
            "options": "Nova|Finalizada",
            "group": "andamento",
        },
    ],
    TIPO_SOLICITACOES_IDAS_BIO: [
        {"key": "cliente", "label": "Cliente", "kind": "text", "group": "identificacao"},
        {"key": "workflow", "label": "Workflow", "kind": "select", "catalog": "workflow", "group": "identificacao"},
        {
            "key": "solicitante",
            "label": "Solicitante",
            "kind": "select",
            "catalog": "solicitantes_idas",
            "group": "identificacao",
        },
        {"key": "responsavel", "label": "Responsável", "kind": "text", "group": "identificacao"},
        {"key": "demanda_origem", "label": "Demanda de origem", "kind": "link", "group": "origem"},
        {"key": "demanda_idas", "label": "Demanda IDAS", "kind": "link", "group": "origem"},
        {"key": "observacoes", "label": "Observações", "kind": "textarea", "group": "origem"},
        {"key": "data_abertura", "label": "Data abertura", "kind": "date", "group": "andamento"},
        {"key": "data_conclusao", "label": "Data conclusão", "kind": "date", "group": "andamento"},
        {
            "key": "situacao",
            "label": "Situação",
            "kind": "select",
            "options": "Nova|Em andamento|Finalizada",
            "group": "andamento",
        },
    ],
}

TITLES = {
    TIPO_REMOCAO_BASE_NEGATIVA: "Remoção - Base negativa",
    TIPO_REMOCAO_BASE_POSITIVA: "Remoção - Base positiva",
    TIPO_SOLICITACOES_IDAS_BIO: "Solicitações IDAS e BIO",
}

DESCRIPTIONS = {
    TIPO_REMOCAO_BASE_NEGATIVA: "Controle de remoções e ações na base negativa.",
    TIPO_REMOCAO_BASE_POSITIVA: "Controle de remoções e higienizações na base positiva.",
    TIPO_SOLICITACOES_IDAS_BIO: "Controle de solicitações IDAS e BIO.",
}


def _serialize_user(user) -> str | None:
    if not user:
        return None
    full = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
    return full or getattr(user, "username", None) or str(user.pk)


def schema_for(tipo: str) -> list[dict[str, str]]:
    return SCHEMAS[tipo]


IMAGE_FIELD_KEYS = {"selfie_higienizada"}


def _extract_dados(payload: dict) -> dict:
    data = payload.get("dados")
    if isinstance(data, dict):
        return data
    if isinstance(data, str) and data.strip():
        import json

        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return payload if isinstance(payload, dict) else {}


def normalize_payload(tipo: str, raw: dict[str, Any] | None) -> dict[str, str]:
    schema = schema_for(tipo)
    source = raw if isinstance(raw, dict) else {}
    result: dict[str, str] = {}
    for field in schema:
        key = field["key"]
        if field.get("kind") == "image" or key in IMAGE_FIELD_KEYS:
            continue
        value = source.get(key)
        if value is None:
            result[key] = ""
        else:
            result[key] = str(value).strip()
    return result


def validate_controle_payload(tipo: str, payload: dict, *, has_selfie: bool = False) -> dict[str, str]:
    if tipo not in VALID_TIPOS:
        return {"tipo": "Tipo de controle inválido."}
    dados = normalize_payload(tipo, _extract_dados(payload))
    if not any(dados.values()) and not has_selfie:
        return {"dados": "Preencha ao menos um campo do registro."}
    return {}


def _schema_has_image(tipo: str) -> bool:
    return any(field.get("kind") == "image" for field in schema_for(tipo))


def serialize_controle(item: AuditoriaControleRegistro) -> dict:
    dados = normalize_payload(item.tipo, item.dados)
    selfie_url = item.selfie.url if item.selfie else None
    if _schema_has_image(item.tipo):
        dados["selfie_higienizada"] = selfie_url or ""
    sla_seconds = compute_controle_sla_seconds(dados, situacao=item.situacao or "")
    return {
        "id": item.id,
        "tipo": item.tipo,
        "dados": dados,
        "selfie_url": selfie_url,
        "situacao": item.situacao,
        "sla_seconds": sla_seconds,
        "sla_label": format_sla_label(sla_seconds),
        "created_by": _serialize_user(item.created_by),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
    }


def create_controle(tipo: str, payload: dict, user, *, selfie=None) -> AuditoriaControleRegistro:
    dados = normalize_payload(tipo, _extract_dados(payload))
    item = AuditoriaControleRegistro(
        tipo=tipo,
        dados=dados,
        situacao=dados.get("situacao", ""),
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    if selfie is not None:
        item.selfie = selfie
    item.save()
    return item


def update_controle(
    item: AuditoriaControleRegistro,
    payload: dict,
    *,
    selfie=None,
    remove_selfie: bool = False,
) -> AuditoriaControleRegistro:
    dados = normalize_payload(item.tipo, _extract_dados(payload))
    item.dados = dados
    item.situacao = dados.get("situacao", "")
    update_fields = ["dados", "situacao", "updated_at"]
    if remove_selfie and item.selfie:
        item.selfie.delete(save=False)
        item.selfie = None
        update_fields.append("selfie")
    elif selfie is not None:
        if item.selfie:
            item.selfie.delete(save=False)
        item.selfie = selfie
        update_fields.append("selfie")
    item.save(update_fields=update_fields)
    return item


def delete_controle(item: AuditoriaControleRegistro) -> None:
    if item.selfie:
        item.selfie.delete(save=False)
    item.delete()


def meta_for(tipo: str) -> dict:
    meta = {
        "tipo": tipo,
        "title": TITLES[tipo],
        "description": DESCRIPTIONS[tipo],
        "fields": schema_for(tipo),
    }
    if tipo == TIPO_SOLICITACOES_IDAS_BIO:
        meta["solicitantes"] = agents_with_any_permission(
            QUAL_CONTESTACAO_INTERNA_FRAUD_VIEW,
            QUAL_CONTESTACAO_INTERNA_FRAUD_ANALYZE,
        )
    return meta
