from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auditoria.models import AuditoriaFalhaAlteracao, AuditoriaFalhaCadastro
from apps.auditoria.services.agent_links import is_system_agent, resolve_agent_for_user
from apps.workforce.models import Agent


@dataclass(frozen=True)
class EditableField:
    label: str
    kind: str = "text"
    required: bool = False
    choices: tuple[tuple[str, str], ...] = ()


EDITABLE_FIELDS: dict[str, EditableField] = {
    "agente_id": EditableField("Agente", "agent", True),
    "auditor_id": EditableField("Auditor", "auditor", True),
    "protocolo": EditableField("Protocolo", "readonly"),
    "modulo": EditableField("Modulo"),
    "demanda_url": EditableField("URL da demanda"),
    "tipo_falha": EditableField("Tipo de falha", required=True),
    "resultado_qualidade": EditableField(
        "Resultado da qualidade",
        "choice",
        required=True,
        choices=tuple(AuditoriaFalhaCadastro.RESULTADO_QUALIDADE_CHOICES),
    ),
    "resultado_cliente": EditableField("Resultado do cliente"),
    "novo_resultado": EditableField("Novo resultado"),
    "sinalizacao": EditableField("Sinalizacao"),
    "motivo_falha": EditableField("Motivo da falha"),
    "etapa_falha": EditableField("Etapa da falha"),
    "tempo_analise": EditableField("Tempo de analise"),
    "cruzamento_bases": EditableField("Cruzamento de bases"),
    "nivel_dificuldade": EditableField("Nivel de dificuldade"),
    "tipo_documento": EditableField("Tipo de documento"),
    "uf_documento": EditableField("UF do documento"),
    "qualidade_imagem": EditableField("Qualidade da imagem"),
    "descricao_irregularidades": EditableField("Descricao das irregularidades"),
    "cliente": EditableField("Cliente"),
    "status": EditableField("Status"),
    "status_falha": EditableField(
        "Status da falha",
        "readonly",
        choices=tuple(AuditoriaFalhaCadastro.STATUS_FALHA_CHOICES),
    ),
    "observacao": EditableField("Observacao"),
    "data_contestacao": EditableField("Data da contestacao", "datetime"),
    "data_analise": EditableField("Data da analise", "datetime"),
    "data_analise_intranet": EditableField("Data da analise na Intranet", "datetime"),
    "data_recepcao_contestacao": EditableField("Recepcao da contestacao", "datetime"),
    "data_encerramento_atividade_intranet": EditableField(
        "Encerramento da atividade na Intranet", "datetime"
    ),
    "data_resposta": EditableField("Data da resposta", "datetime"),
    "atribuido_em": EditableField("Atribuido em", "datetime"),
    "analise_iniciada_em": EditableField("Analise iniciada em", "datetime"),
    "analise_concluida_em": EditableField("Analise concluida em", "datetime"),
}


PROFILE_FRAUD = "fraud"
PROFILE_COMPLIANCE = "compliance"
AUDITORIA_COMPLIANCE_CONTEXT = "auditoria_compliance"
REINSPECAO_CONTEXT = "reinspecao"

ALL_PROFILE_FIELDS = tuple(EDITABLE_FIELDS)
READONLY_FIELDS = {"protocolo", "status_falha"}
PROFILE_FIELDS: dict[str, tuple[str, ...]] = {
    PROFILE_FRAUD: ALL_PROFILE_FIELDS,
    PROFILE_COMPLIANCE: ALL_PROFILE_FIELDS,
}

PROFILE_LABELS: dict[str, dict[str, str]] = {
    PROFILE_FRAUD: {
        "agente_id": "Usuario auditado",
        "motivo_falha": "Cenario",
        "resultado_qualidade": "Resultado da qualidade",
    },
    PROFILE_COMPLIANCE: {
        "agente_id": "Matricula auditada",
        "auditor_id": "Auditor responsavel",
        "descricao_irregularidades": "Tipo de conferencia",
        "status": "Status da analise",
        "motivo_falha": "Irregularidade",
        "etapa_falha": "Status da irregularidade",
        "resultado_qualidade": "Resultado consolidado",
    },
}


def resolve_analysis_context(falha: AuditoriaFalhaCadastro) -> str:
    parsed = falha.brflow_parsed if isinstance(falha.brflow_parsed, dict) else {}
    source_context = {}
    if falha.analise_origem_id and falha.analise_origem is not None:
        source_context = (
            falha.analise_origem.contexto
            if isinstance(falha.analise_origem.contexto, dict)
            else {}
        )
    fila_contexto = str(
        parsed.get("fila_contexto") or source_context.get("fila_contexto") or ""
    ).strip().lower()
    if fila_contexto in {AUDITORIA_COMPLIANCE_CONTEXT, REINSPECAO_CONTEXT}:
        return fila_contexto
    if falha.origem == AuditoriaFalhaCadastro.ORIGEM_REINSPECAO:
        return REINSPECAO_CONTEXT
    return PROFILE_FRAUD


def resolve_field_profile(falha: AuditoriaFalhaCadastro) -> str:
    context = resolve_analysis_context(falha)
    if (
        context in {AUDITORIA_COMPLIANCE_CONTEXT, REINSPECAO_CONTEXT}
        or falha.tipo_registro == AuditoriaFalhaCadastro.REGISTRO_REINSPECAO
    ):
        return PROFILE_COMPLIANCE
    return PROFILE_FRAUD


def related_analysis_records(falha: AuditoriaFalhaCadastro):
    """Retorna as linhas do mesmo grid concluido sem misturar fluxos homonimos."""
    queryset = AuditoriaFalhaCadastro.objects.select_related(
        "agente_ref", "auditor_ref", "analise_origem"
    )
    if falha.atividade_id:
        queryset = queryset.filter(
            atividade_id=falha.atividade_id,
            protocolo=falha.protocolo,
        )
    elif falha.protocolo_origem_id:
        queryset = queryset.filter(protocolo_origem_id=falha.protocolo_origem_id)
    else:
        context = resolve_analysis_context(falha)
        if context in {AUDITORIA_COMPLIANCE_CONTEXT, REINSPECAO_CONTEXT}:
            queryset = queryset.filter(
                protocolo=falha.protocolo,
                brflow_parsed__fila_contexto=context,
            )
        elif falha.analise_origem_id:
            queryset = queryset.filter(analise_origem_id=falha.analise_origem_id)
        else:
            queryset = queryset.filter(
                protocolo=falha.protocolo,
                origem=falha.origem,
                tipo_registro=falha.tipo_registro,
            )
    return queryset.order_by("ordem_etapa", "id")


def analysis_definitions(falha: AuditoriaFalhaCadastro) -> list[dict[str, Any]]:
    rows = []
    for position, item in enumerate(related_analysis_records(falha), start=1):
        rows.append(
            {
                "id": item.id,
                "ordem": position,
                "tipo_falha": item.tipo_falha,
                "motivo_falha": item.motivo_falha,
                "etapa_falha": item.etapa_falha,
                "resultado_qualidade": item.resultado_qualidade,
                "resultado_qualidade_label": item.get_resultado_qualidade_display(),
                "status_falha": item.status_falha,
                "profile": resolve_field_profile(item),
                "updated_at": item.updated_at.isoformat() if item.updated_at else None,
            }
        )
    return rows


def profile_definition(falha: AuditoriaFalhaCadastro) -> dict[str, str]:
    profile = resolve_field_profile(falha)
    if profile == PROFILE_COMPLIANCE:
        return {
            "key": profile,
            "label": "Compliance",
            "description": "Campos da analise de Compliance selecionada.",
        }
    return {
        "key": profile,
        "label": "Fraud",
        "description": "Campos da auditoria Fraud selecionada.",
    }


def field_definitions(falha: AuditoriaFalhaCadastro) -> list[dict[str, Any]]:
    profile = resolve_field_profile(falha)
    rows = []
    for key in PROFILE_FIELDS[profile]:
        field = EDITABLE_FIELDS[key]
        rows.append(
            {
                "key": key,
                "label": PROFILE_LABELS[profile].get(key, field.label),
                "type": field.kind,
                "required": field.required,
                "choices": [{"value": value, "label": label} for value, label in field.choices],
            }
        )
    return rows


def _json_value(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def snapshot_falha(falha: AuditoriaFalhaCadastro) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for key in EDITABLE_FIELDS:
        if key == "agente_id":
            value = falha.agente_ref_id
        elif key == "auditor_id":
            value = falha.auditor_ref_id
        else:
            value = getattr(falha, key)
        snapshot[key] = _json_value(value)
    return snapshot


def _parse_datetime_value(value, *, key: str):
    if value in (None, ""):
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise ValidationError({key: "Informe uma data e hora valida no formato ISO 8601."})
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _agent_value(value, *, key: str, allow_system: bool) -> Agent:
    if not value:
        raise ValidationError({key: "Este relacionamento e obrigatorio."})
    try:
        agent = Agent.objects.get(pk=value)
    except (Agent.DoesNotExist, ValueError, TypeError):
        raise ValidationError({key: "Colaborador nao encontrado."})
    if not allow_system and is_system_agent(agent):
        raise ValidationError({key: "SISTEMA nao pode ser auditor ou autor da alteracao."})
    return agent


def _normalize_value(key: str, value):
    definition = EDITABLE_FIELDS[key]
    if definition.kind == "datetime":
        return _parse_datetime_value(value, key=key)
    if definition.kind == "agent":
        return _agent_value(value, key=key, allow_system=True)
    if definition.kind == "auditor":
        return _agent_value(value, key=key, allow_system=False)
    if definition.kind == "choice":
        normalized = str(value or "").strip()
        allowed = {choice for choice, _label in definition.choices}
        if normalized not in allowed:
            raise ValidationError({key: "Opcao invalida."})
        return normalized
    normalized = str(value or "").strip()
    if definition.required and not normalized:
        raise ValidationError({key: "Este campo e obrigatorio."})
    model_field = AuditoriaFalhaCadastro._meta.get_field(key)
    max_length = getattr(model_field, "max_length", None)
    if max_length and len(normalized) > max_length:
        raise ValidationError({key: f"Use no maximo {max_length} caracteres."})
    return normalized


def _status_falha_from_resultado(resultado: str) -> str:
    return {
        AuditoriaFalhaCadastro.RESULTADO_COM_FALHA: AuditoriaFalhaCadastro.STATUS_FALHA_MANTIDA,
        AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA: AuditoriaFalhaCadastro.STATUS_FALHA_RETIRADA,
        AuditoriaFalhaCadastro.RESULTADO_NAO_CLASSIFICADO: AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
    }[resultado]


def _serialize_agent(agent: Agent | None) -> dict[str, Any] | None:
    if not agent:
        return None
    return {
        "id": str(agent.id),
        "user_lan_id": agent.user_lan_id,
        "full_name": agent.full_name,
        "active": agent.active,
        "is_system": is_system_agent(agent),
    }


def serialize_alteracao(item: AuditoriaFalhaAlteracao) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "falha_id": item.falha_id,
        "origem": item.origem,
        "versao": item.versao,
        "dados_anteriores": item.dados_anteriores,
        "dados_alterados": item.dados_alterados,
        "campos_alterados": item.campos_alterados,
        "justificativa": item.justificativa,
        "schema_version": item.schema_version,
        "alterado_por": _serialize_agent(item.alterado_por),
        "created_at": item.created_at.isoformat(),
    }


@transaction.atomic
def alterar_falha(
    *,
    falha_id: int,
    user,
    dados: dict[str, Any],
    justificativa: str,
    expected_updated_at: str | None = None,
    idempotency_key: str | None = None,
    status_falha_final: str | None = None,
) -> tuple[AuditoriaFalhaCadastro, AuditoriaFalhaAlteracao, bool]:
    actor = resolve_agent_for_user(user)
    if actor is None:
        raise ValidationError("O usuario autenticado nao possui relacionamento com AGENT.")
    if is_system_agent(actor):
        raise ValidationError("SISTEMA nao pode executar alteracoes.")

    justification = str(justificativa or "").strip()
    if len(justification) < 5:
        raise ValidationError({"justificativa": "Informe uma justificativa com ao menos 5 caracteres."})
    if not isinstance(dados, dict) or not dados:
        raise ValidationError({"dados": "Informe ao menos um campo para alterar."})
    idem_uuid = None
    if idempotency_key:
        try:
            idem_uuid = uuid.UUID(str(idempotency_key))
        except (ValueError, TypeError, AttributeError):
            raise ValidationError({"idempotency_key": "Identificador de idempotencia invalido."})
        existing = (
            AuditoriaFalhaAlteracao.objects.filter(idempotency_key=idem_uuid)
            .select_related("falha", "alterado_por")
            .first()
        )
        if existing:
            if existing.falha_id != falha_id:
                raise ValidationError(
                    {"idempotency_key": "Identificador ja utilizado em outra falha."}
                )
            return existing.falha, existing, False

    try:
        falha = (
            AuditoriaFalhaCadastro.objects.select_for_update()
            .get(pk=falha_id)
        )
    except AuditoriaFalhaCadastro.DoesNotExist:
        raise ValidationError("Falha nao encontrada.")

    allowed_fields = set(PROFILE_FIELDS[resolve_field_profile(falha)]) - READONLY_FIELDS
    unknown = sorted(set(dados) - allowed_fields)
    if unknown:
        raise ValidationError(
            {"dados": f"Campos nao permitidos para esta origem: {', '.join(unknown)}."}
        )

    if status_falha_final not in {
        None,
        AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        AuditoriaFalhaCadastro.STATUS_FALHA_NAO_CONFORME,
    }:
        raise ValidationError(
            {
                "status_falha_final": (
                    "Status final invalido para o fluxo interno de validacao."
                )
            }
        )

    if expected_updated_at:
        expected = _parse_datetime_value(expected_updated_at, key="expected_updated_at")
        if falha.updated_at and abs((falha.updated_at - expected).total_seconds()) > 0.001:
            raise ValidationError(
                {"expected_updated_at": "A falha foi alterada por outra pessoa. Recarregue os dados."}
            )

    before = snapshot_falha(falha)
    update_fields: set[str] = set()
    for key, raw_value in dados.items():
        value = _normalize_value(key, raw_value)
        if key == "agente_id":
            falha.agente_ref = value
            # Compatibilidade temporaria com consumidores legados.
            falha.usuario = value.user_lan_id
            update_fields.update({"agente_ref", "usuario"})
        elif key == "auditor_id":
            falha.auditor_ref = value
            falha.auditor = value.user_lan_id
            update_fields.update({"auditor_ref", "auditor"})
        elif key == "resultado_qualidade":
            falha.resultado_qualidade_override = value
            falha.status_falha = _status_falha_from_resultado(value)
            update_fields.update({"resultado_qualidade_override", "status_falha"})
        else:
            setattr(falha, key, value)
            update_fields.add(key)

    if status_falha_final is not None:
        falha.status_falha = status_falha_final
        update_fields.add("status_falha")

    falha.save(update_fields=sorted(update_fields | {"updated_at"}))
    falha.refresh_from_db()
    after = snapshot_falha(falha)
    changed_fields = [key for key in after if before.get(key) != after.get(key)]
    if not changed_fields:
        raise ValidationError("Nenhum valor foi alterado.")

    version = (
        AuditoriaFalhaAlteracao.objects.filter(falha=falha).aggregate(value=Max("versao"))["value"]
        or 0
    ) + 1
    change = AuditoriaFalhaAlteracao.objects.create(
        falha=falha,
        alterado_por=actor,
        origem=falha.origem or falha.tipo_registro,
        versao=version,
        dados_anteriores=before,
        dados_alterados=after,
        campos_alterados=changed_fields,
        justificativa=justification,
        idempotency_key=idem_uuid or uuid.uuid4(),
    )
    return falha, change, True
