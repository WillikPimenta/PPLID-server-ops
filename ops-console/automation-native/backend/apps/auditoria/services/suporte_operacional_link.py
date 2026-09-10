from __future__ import annotations

import logging
from typing import Any, Literal

from django.db import models

from apps.auditoria.models import (
    AuditoriaAtividadeProtocolo,
    AuditoriaFalhaCadastro,
    ContestacaoOperacional,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
)
from apps.auditoria.services.agent_links import resolve_agent_reference
from apps.suporte_operacional.models import OperationalSupportRequest
from apps.workforce.models import Agent

logger = logging.getLogger(__name__)

LinkKeyType = Literal["fraud", "compliance"]

_SUPPORT_SELECT = (
    "agent",
    "answered_by",
    "assignee",
)


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def resolve_agent_matricula(value: object) -> str:
    """Canonicaliza identificador legado para user_lan_id do agente."""
    text = _normalize_text(value)
    if not text:
        return ""
    resolution = resolve_agent_reference(text)
    if resolution.agent and (resolution.agent.user_lan_id or "").strip():
        return resolution.agent.user_lan_id.strip()
    for lookup in (
        {"user_lan_id__iexact": text},
        {"time_tracking_id__iexact": text},
        {"oracle_id__iexact": text},
    ):
        agent = Agent.objects.filter(**lookup).only("user_lan_id").first()
        if agent and (agent.user_lan_id or "").strip():
            return agent.user_lan_id.strip()
    return text


def lookup_answered_support_request(
    *,
    key_type: LinkKeyType,
    protocolo: str,
    matricula: str,
    workflow: str = "",
) -> OperationalSupportRequest | None:
    protocol = _normalize_text(protocolo)
    matricula_norm = resolve_agent_matricula(matricula)
    if not protocol or not matricula_norm:
        return None

    qs = OperationalSupportRequest.objects.filter(
        status=OperationalSupportRequest.Status.ANSWERED,
        protocol__iexact=protocol,
        agent__user_lan_id__iexact=matricula_norm,
    ).select_related(*_SUPPORT_SELECT)

    if key_type == "fraud":
        workflow_norm = _normalize_text(workflow)
        if not workflow_norm:
            return None
        qs = qs.filter(
            operation_origin=OperationalSupportRequest.OperationOrigin.FRAUD,
            workflow__iexact=workflow_norm,
        )
    else:
        qs = qs.filter(
            operation_origin=OperationalSupportRequest.OperationOrigin.CONFER,
        )

    matches = list(qs.order_by("-answered_at", "-created_at")[:2])
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning(
            "Múltiplas solicitações de suporte respondidas para a mesma chave "
            "(tipo=%s protocolo=%s matricula=%s workflow=%s); usando a mais recente.",
            key_type,
            protocol,
            matricula_norm,
            workflow,
        )
    return matches[0]


def _workflow_from_falha(falha: AuditoriaFalhaCadastro) -> str:
    parsed = falha.brflow_parsed if isinstance(falha.brflow_parsed, dict) else {}
    workflow = _normalize_text(parsed.get("workflow"))
    if workflow:
        return workflow
    if falha.atividade_id:
        from apps.auditoria.models import AuditoriaAtividade

        atividade_workflow = (
            AuditoriaAtividade.objects.filter(pk=falha.atividade_id)
            .values_list("workflow", flat=True)
            .first()
        )
        if atividade_workflow:
            return _normalize_text(atividade_workflow)
    return _normalize_text(falha.modulo)


def _lookup_for_record(record: models.Model) -> OperationalSupportRequest | None:
    if isinstance(record, AuditoriaAtividadeProtocolo):
        return lookup_answered_support_request(
            key_type="fraud",
            protocolo=record.protocolo,
            workflow=record.workflow,
            matricula=record.agente,
        )
    if isinstance(record, QualidadePendenteReinspecao | QualidadePendenteAuditoriaCompliance):
        return lookup_answered_support_request(
            key_type="compliance",
            protocolo=record.protocolo,
            matricula=record.usuario,
        )
    if isinstance(record, AuditoriaFalhaCadastro):
        origem = (record.origem or "").strip()
        tipo = (record.tipo_registro or "").strip()
        if origem in {
            AuditoriaFalhaCadastro.ORIGEM_REINSPECAO,
        } or tipo == AuditoriaFalhaCadastro.REGISTRO_REINSPECAO:
            return lookup_answered_support_request(
                key_type="compliance",
                protocolo=record.protocolo,
                matricula=record.usuario,
            )
        return lookup_answered_support_request(
            key_type="fraud",
            protocolo=record.protocolo,
            workflow=_workflow_from_falha(record),
            matricula=record.usuario,
        )
    if isinstance(record, ContestacaoOperacional):
        if record.dominio == ContestacaoOperacional.DOMINIO_COMPLIANCE:
            return lookup_answered_support_request(
                key_type="compliance",
                protocolo=record.protocolo,
                matricula=record.agente_usuario,
            )
        workflow = ""
        if record.falha_id:
            workflow = _workflow_from_falha(record.falha)
        return lookup_answered_support_request(
            key_type="fraud",
            protocolo=record.protocolo,
            workflow=workflow,
            matricula=record.agente_usuario,
        )
    return None


def _user_display_name(user) -> str:
    if not user:
        return ""
    full = (user.get_full_name() or "").strip()
    return full or getattr(user, "username", "") or ""


def serialize_operational_support_link(
    request_obj: OperationalSupportRequest | None,
) -> dict[str, Any]:
    if request_obj is None:
        return {"vinculado": False}

    agent = request_obj.agent
    return {
        "vinculado": True,
        "request_id": str(request_obj.pk),
        "status": request_obj.status,
        "category": request_obj.category or "",
        "description": request_obj.description or "",
        "answer": request_obj.answer or "",
        "answer_option": request_obj.answer_option or "",
        "answered_by_nome": _user_display_name(request_obj.answered_by),
        "answered_at": (
            request_obj.answered_at.isoformat() if request_obj.answered_at else None
        ),
        "agente_matricula": (agent.user_lan_id or "").strip() if agent else "",
        "agente_nome": (agent.full_name or "").strip() if agent else "",
        "workflow": request_obj.workflow or "",
        "operation_origin": request_obj.operation_origin or "",
        "created_at": (
            request_obj.created_at.isoformat() if request_obj.created_at else None
        ),
    }


def resolve_and_link(record: models.Model, *, force: bool = False) -> OperationalSupportRequest | None:
    """Resolve solicitação respondida e grava FK no registro, se ainda vazia."""
    if not hasattr(record, "operational_support_request_id"):
        return None
    if record.operational_support_request_id and not force:
        return record.operational_support_request

    match = _lookup_for_record(record)
    if match is None:
        return None

    if record.operational_support_request_id != match.pk:
        record.operational_support_request = match
        record.save(update_fields=["operational_support_request", "updated_at"])

    return match


def copy_operational_support_link(
    *,
    source: models.Model,
    target: models.Model,
) -> None:
    """Copia FK de suporte do registro origem para o destino (ex.: promoção pendente → tratado)."""
    if not hasattr(source, "operational_support_request_id"):
        return
    if not hasattr(target, "operational_support_request_id"):
        return
    if target.operational_support_request_id:
        return
    if source.operational_support_request_id:
        target.operational_support_request_id = source.operational_support_request_id
        target.save(update_fields=["operational_support_request", "updated_at"])
        return
    match = resolve_and_link(source)
    if match:
        target.operational_support_request = match
        target.save(update_fields=["operational_support_request", "updated_at"])


def suporte_operacional_payload_for(record: models.Model) -> dict[str, Any]:
    """Retorna payload serializado; faz lazy link se FK ainda não preenchida."""
    if not hasattr(record, "operational_support_request_id"):
        return serialize_operational_support_link(None)

    request_obj = getattr(record, "operational_support_request", None)
    if request_obj is None and not record.operational_support_request_id:
        request_obj = resolve_and_link(record)
    elif request_obj is None and record.operational_support_request_id:
        request_obj = (
            OperationalSupportRequest.objects.filter(pk=record.operational_support_request_id)
            .select_related(*_SUPPORT_SELECT)
            .first()
        )

    return serialize_operational_support_link(request_obj)
