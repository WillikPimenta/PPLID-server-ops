from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.auditoria.models import (
    AuditoriaFalhaAlteracao,
    AuditoriaFalhaCadastro,
    AuditoriaFalhaValidacaoHistorico,
)
from apps.auditoria.services.agent_links import is_system_agent, resolve_agent_for_user
from apps.auditoria.services.falha_alteracoes import (
    alterar_falha,
    serialize_alteracao,
    snapshot_falha,
)


ALTERACAO_IDEMPOTENCY_NAMESPACE = uuid.UUID("48b92ad9-c5ba-4761-a897-af0f4388366c")


class FalhaValidacaoConflict(Exception):
    def __init__(self, errors: dict[str, str] | str):
        self.errors = errors if isinstance(errors, dict) else {"detail": errors}
        super().__init__(str(errors))


def _actor_for_user(user):
    actor = resolve_agent_for_user(user)
    if actor is None:
        raise ValidationError("O usuario autenticado nao possui relacionamento com AGENT.")
    if is_system_agent(actor):
        raise ValidationError("SISTEMA nao pode validar falhas.")
    return actor


def _idempotency_uuid(value: str | None) -> uuid.UUID:
    if not value:
        raise ValidationError({"idempotency_key": "Informe o identificador de idempotencia."})
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({"idempotency_key": "Identificador de idempotencia invalido."})


def _observation(value: str | None) -> str:
    observation = str(value or "").strip()
    if len(observation) < 5:
        raise ValidationError({"observacao": "Informe uma observacao com ao menos 5 caracteres."})
    return observation


def _expected_datetime(value: str | None):
    if not value:
        raise ValidationError(
            {"expected_updated_at": "Informe a versao atual da falha no formato ISO 8601."}
        )
    parsed = parse_datetime(str(value))
    if parsed is None:
        raise ValidationError(
            {"expected_updated_at": "Informe uma data e hora valida no formato ISO 8601."}
        )
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _payload_hash(
    *,
    falha_id: int,
    resultado: str,
    observacao: str,
    dados: dict[str, Any] | None,
) -> str:
    canonical = json.dumps(
        {
            "falha_id": falha_id,
            "resultado": resultado,
            "observacao": observacao,
            "dados": dados or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_actor(actor) -> dict[str, Any]:
    return {
        "id": str(actor.id),
        "user_lan_id": actor.user_lan_id,
        "full_name": actor.full_name,
    }


def serialize_validacao(item: AuditoriaFalhaValidacaoHistorico) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "falha_id": item.falha_id,
        "status_de": item.status_de,
        "status_para": item.status_para,
        "resultado": item.resultado,
        "resultado_label": item.get_resultado_display(),
        "observacao": item.observacao,
        "snapshot_inicial": item.snapshot_inicial,
        "snapshot_final": item.snapshot_final,
        "ator": _serialize_actor(item.ator),
        "alteracao": serialize_alteracao(item.alteracao) if item.alteracao_id else None,
        "created_at": item.created_at.isoformat(),
        "updated_at": item.updated_at.isoformat(),
    }


def _replay_or_conflict(
    *,
    idempotency_key: uuid.UUID,
    payload_hash: str,
) -> tuple[AuditoriaFalhaCadastro, AuditoriaFalhaValidacaoHistorico, AuditoriaFalhaAlteracao | None, bool] | None:
    existing = (
        AuditoriaFalhaValidacaoHistorico.objects.select_related(
            "falha", "ator", "alteracao", "alteracao__alterado_por"
        )
        .filter(idempotency_key=idempotency_key)
        .first()
    )
    if existing is None:
        return None
    if existing.idempotency_payload_hash != payload_hash:
        raise FalhaValidacaoConflict(
            {"idempotency_key": "Identificador ja utilizado com outro payload."}
        )
    return existing.falha, existing, existing.alteracao, False


def _assert_current_version(falha: AuditoriaFalhaCadastro, expected_updated_at: str | None):
    expected = _expected_datetime(expected_updated_at)
    if falha.updated_at and abs((falha.updated_at - expected).total_seconds()) > 0.001:
        raise FalhaValidacaoConflict(
            {"expected_updated_at": "A falha foi alterada por outra pessoa. Recarregue os dados."}
        )


def _projected_result(
    falha: AuditoriaFalhaCadastro,
    dados: dict[str, Any],
) -> str:
    override = dados.get("resultado_qualidade", falha.resultado_qualidade_override)
    if override:
        return str(override)
    return falha.inferir_resultado_qualidade(
        status=dados.get("status", falha.status),
        tipo_falha=dados.get("tipo_falha", falha.tipo_falha),
        origem=falha.origem,
        tipo_registro=falha.tipo_registro,
        status_falha=AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA,
        brflow_parsed=falha.brflow_parsed,
    )


@transaction.atomic
def decidir_falha(
    *,
    falha_id: int,
    user,
    resultado: str,
    observacao: str,
    expected_updated_at: str | None,
    idempotency_key: str | None,
    dados: dict[str, Any] | None = None,
) -> tuple[AuditoriaFalhaCadastro, AuditoriaFalhaValidacaoHistorico, AuditoriaFalhaAlteracao | None, bool]:
    actor = _actor_for_user(user)
    if resultado not in {
        AuditoriaFalhaValidacaoHistorico.RESULTADO_CONFORME,
        AuditoriaFalhaValidacaoHistorico.RESULTADO_NAO_CONFORME,
    }:
        raise ValidationError({"resultado": "Resultado de validacao invalido."})
    observation = _observation(observacao)
    idem_uuid = _idempotency_uuid(idempotency_key)
    if dados is not None and not isinstance(dados, dict):
        raise ValidationError({"dados": "Informe um objeto com os campos editados."})
    clean_data = dict(dados or {})
    payload_hash = _payload_hash(
        falha_id=falha_id,
        resultado=resultado,
        observacao=observation,
        dados=clean_data,
    )
    replay = _replay_or_conflict(idempotency_key=idem_uuid, payload_hash=payload_hash)
    if replay:
        return replay

    try:
        falha = AuditoriaFalhaCadastro.objects.select_for_update().get(pk=falha_id)
    except AuditoriaFalhaCadastro.DoesNotExist:
        raise ValidationError("Falha nao encontrada.")

    replay = _replay_or_conflict(idempotency_key=idem_uuid, payload_hash=payload_hash)
    if replay:
        return replay
    if falha.status_falha != AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO:
        raise FalhaValidacaoConflict(
            {"status_falha": "A falha ja foi validada ou nao esta em validacao."}
        )
    _assert_current_version(falha, expected_updated_at)
    before = snapshot_falha(falha)
    change = None

    try:
        with transaction.atomic():
            if resultado == AuditoriaFalhaValidacaoHistorico.RESULTADO_CONFORME:
                if clean_data:
                    raise ValidationError(
                        {"dados": "A decisao Conforme nao aceita alteracoes na falha."}
                    )
                status_to = AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA
                falha.status_falha = status_to
                falha.save(update_fields=["status_falha", "updated_at"])
                falha.refresh_from_db()
            else:
                if not clean_data:
                    raise ValidationError(
                        {"dados": "Informe os dados corrigidos para a decisao Nao Conforme."}
                    )
                if "status_falha" in clean_data:
                    raise ValidationError(
                        {"dados": "status_falha e definido pelo fluxo de validacao."}
                    )
                projected_result = _projected_result(falha, clean_data)
                if projected_result == AuditoriaFalhaCadastro.RESULTADO_COM_FALHA:
                    status_to = AuditoriaFalhaCadastro.STATUS_FALHA_ATIVA
                elif projected_result == AuditoriaFalhaCadastro.RESULTADO_SEM_FALHA:
                    status_to = AuditoriaFalhaCadastro.STATUS_FALHA_NAO_CONFORME
                else:
                    raise ValidationError(
                        {
                            "dados": (
                                "A correcao deve definir um resultado final Com falha ou Sem falha."
                            )
                        }
                    )
                alteration_idem = uuid.uuid5(
                    ALTERACAO_IDEMPOTENCY_NAMESPACE,
                    f"falha-validacao:{idem_uuid}",
                )
                falha, change, _change_created = alterar_falha(
                    falha_id=falha_id,
                    user=user,
                    dados=clean_data,
                    justificativa=observation,
                    expected_updated_at=expected_updated_at,
                    idempotency_key=str(alteration_idem),
                    status_falha_final=status_to,
                )

            history = AuditoriaFalhaValidacaoHistorico.objects.create(
                falha=falha,
                status_de=AuditoriaFalhaCadastro.STATUS_FALHA_EM_VALIDACAO,
                status_para=status_to,
                resultado=resultado,
                observacao=observation,
                snapshot_inicial=before,
                snapshot_final=snapshot_falha(falha),
                ator=actor,
                alteracao=change,
                idempotency_key=idem_uuid,
                idempotency_payload_hash=payload_hash,
            )
    except IntegrityError:
        replay = _replay_or_conflict(idempotency_key=idem_uuid, payload_hash=payload_hash)
        if replay:
            return replay
        raise

    return falha, history, change, True


def decidir_conforme(**kwargs):
    return decidir_falha(
        resultado=AuditoriaFalhaValidacaoHistorico.RESULTADO_CONFORME,
        dados=None,
        **kwargs,
    )


def decidir_nao_conforme(*, dados: dict[str, Any] | None, **kwargs):
    return decidir_falha(
        resultado=AuditoriaFalhaValidacaoHistorico.RESULTADO_NAO_CONFORME,
        dados=dados,
        **kwargs,
    )
