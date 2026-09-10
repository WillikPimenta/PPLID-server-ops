from __future__ import annotations

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from apps.auditoria.models import AuditoriaAtividade, AuditoriaAtividadeProtocolo
from apps.auditoria.services.contestacao_import import is_ignored_protocol_row


def validate_protocolo_create_payload(payload: dict, *, atividade: AuditoriaAtividade) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not isinstance(payload, dict):
        return {"detail": "Payload inválido."}

    protocolo = payload.get("protocolo")
    if not isinstance(protocolo, str):
        errors["protocolo"] = "Informe o número do protocolo."
        return errors

    protocolo = protocolo.strip()
    if not protocolo:
        errors["protocolo"] = "Informe o número do protocolo."
    elif len(protocolo) > 100:
        errors["protocolo"] = "O protocolo deve ter no máximo 100 caracteres."
    elif is_ignored_protocol_row(protocolo):
        errors["protocolo"] = "Este protocolo não pode ser incluído."
    elif atividade.protocolos.filter(protocolo=protocolo).exists():
        errors["protocolo"] = "Este protocolo já está vinculado à atividade."

    resultado = payload.get("resultado_contestado", "")
    if resultado is not None and not isinstance(resultado, str):
        errors["resultado_contestado"] = "O resultado contestado é inválido."

    return errors


def sync_atividade_protocolo_metrics(atividade: AuditoriaAtividade) -> AuditoriaAtividade:
    total = atividade.protocolos.count()
    has_pending = atividade.protocolos.exclude(
        status=AuditoriaAtividadeProtocolo.STATUS_CONCLUIDO,
    ).exists()
    atividade.total_protocolos = total
    if total == 0:
        atividade.status = AuditoriaAtividade.STATUS_PENDENTE
        atividade.encerrado_em = None
    elif not has_pending:
        # Protocolos permanecem vinculados à atividade após a consolidação.
        atividade.status = AuditoriaAtividade.STATUS_CONCLUIDA
        if atividade.encerrado_em is None:
            atividade.encerrado_em = timezone.now()
    else:
        if atividade.status in (AuditoriaAtividade.STATUS_PENDENTE, AuditoriaAtividade.STATUS_CONCLUIDA):
            atividade.status = AuditoriaAtividade.STATUS_EM_ANDAMENTO
        atividade.encerrado_em = None

    atividade.save(update_fields=["total_protocolos", "status", "encerrado_em", "updated_at"])
    return atividade


@transaction.atomic
def create_protocolo(atividade: AuditoriaAtividade, payload: dict) -> AuditoriaAtividadeProtocolo:
    protocolo = (payload.get("protocolo") or "").strip()
    resultado_contestado = (payload.get("resultado_contestado") or "").strip()
    workflow = (payload.get("workflow") or atividade.workflow or "").strip()
    nivel_hierarquico = (payload.get("nivel_hierarquico") or atividade.nivel_hierarquico or "").strip()
    brflow_raw = (payload.get("brflow_raw") or "").strip()
    brflow_parsed = payload.get("brflow_parsed") if isinstance(payload.get("brflow_parsed"), dict) else {}

    max_row = atividade.protocolos.aggregate(max_row=Max("excel_row"))["max_row"] or 0

    item = AuditoriaAtividadeProtocolo.objects.create(
        atividade=atividade,
        protocolo=protocolo,
        workflow=workflow,
        nivel_hierarquico=nivel_hierarquico,
        resultado_contestado=resultado_contestado,
        brflow_raw=brflow_raw,
        brflow_parsed=brflow_parsed,
        status=AuditoriaAtividadeProtocolo.STATUS_PENDENTE,
        excel_row=max_row + 1,
    )
    sync_atividade_protocolo_metrics(atividade)
    return item


@transaction.atomic
def update_protocolo_brflow(
    protocolo: AuditoriaAtividadeProtocolo,
    *,
    brflow_raw: str,
    brflow_parsed: dict | None = None,
) -> AuditoriaAtividadeProtocolo:
    protocolo.brflow_raw = (brflow_raw or "").strip()
    if isinstance(brflow_parsed, dict):
        protocolo.brflow_parsed = brflow_parsed
    protocolo.save(update_fields=["brflow_raw", "brflow_parsed", "updated_at"])
    return protocolo


@transaction.atomic
def delete_protocolo(protocolo: AuditoriaAtividadeProtocolo) -> AuditoriaAtividade:
    atividade = protocolo.atividade
    protocolo.delete()
    sync_atividade_protocolo_metrics(atividade)
    return atividade
