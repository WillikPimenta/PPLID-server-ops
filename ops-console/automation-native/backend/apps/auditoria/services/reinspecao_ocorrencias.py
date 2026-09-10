"""Ledger transacional e idempotente das ocorrencias de Reinspecao."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteReinspecao,
    ReinspecaoOcorrencia,
)
from apps.auditoria.services.reinspecao_import_dedupe import (
    ReinspecaoOccurrenceKey,
    build_reinspecao_occurrence_key,
    reinspecao_occurrence_key_hash,
)


@dataclass(frozen=True)
class ReinspecaoOccurrenceReservation:
    ocorrencia: ReinspecaoOcorrencia
    action: str
    acquired: bool


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def _identity(
    *,
    contexto: str,
    protocolo: str,
    descricao_irregularidades: str,
    data_contestacao: datetime | None,
) -> tuple[str, ReinspecaoOccurrenceKey, datetime]:
    aware_date = _aware(data_contestacao)
    if aware_date is None:
        raise ValueError("Data da contestacao obrigatoria para reservar a ocorrencia.")
    key = build_reinspecao_occurrence_key(
        contexto=contexto,
        protocolo=protocolo,
        descricao_irregularidades=descricao_irregularidades,
        data_contestacao=aware_date,
    )
    return reinspecao_occurrence_key_hash(key), key, aware_date


def _assert_same_identity(
    ocorrencia: ReinspecaoOcorrencia,
    key: ReinspecaoOccurrenceKey,
) -> None:
    persisted_key = build_reinspecao_occurrence_key(
        contexto=ocorrencia.contexto,
        protocolo=ocorrencia.protocolo,
        descricao_irregularidades=ocorrencia.descricao_irregularidades,
        data_contestacao=ocorrencia.data_contestacao,
    )
    if persisted_key != key:
        raise ValueError("Conflito entre a chave e a identidade persistida da ocorrencia.")


@transaction.atomic
def reserve_reinspecao_occurrence(
    *,
    contexto: str,
    protocolo: str,
    descricao_irregularidades: str,
    data_contestacao: datetime | None,
    source: str,
    source_file: str = "",
    source_hash: str = "",
    observed_at: datetime | None = None,
) -> ReinspecaoOccurrenceReservation:
    """Reserva a chave; o chamador deve manter a transacao ate vincular o destino."""
    occurrence_hash, key, aware_date = _identity(
        contexto=contexto,
        protocolo=protocolo,
        descricao_irregularidades=descricao_irregularidades,
        data_contestacao=data_contestacao,
    )
    now = _aware(observed_at) or timezone.now()
    ocorrencia = (
        ReinspecaoOcorrencia.objects.select_for_update()
        .filter(occurrence_key=occurrence_hash)
        .first()
    )
    action = "seen"
    acquired = False
    if ocorrencia is None:
        try:
            # Savepoint isolado: uma violacao concorrente nao invalida a transacao externa.
            with transaction.atomic():
                ocorrencia = ReinspecaoOcorrencia.objects.create(
                    contexto=key[0],
                    occurrence_key=occurrence_hash,
                    protocolo=key[1],
                    descricao_irregularidades=descricao_irregularidades,
                    irregularidade_normalizada=key[2],
                    data_contestacao=aware_date,
                    source=(source or "").strip(),
                    last_source_file=(source_file or "").strip(),
                    last_source_hash=(source_hash or "").strip(),
                    first_seen_at=now,
                    last_seen_at=now,
                    seen_count=1,
                )
            action = "created"
            acquired = True
        except IntegrityError:
            ocorrencia = ReinspecaoOcorrencia.objects.select_for_update().get(
                occurrence_key=occurrence_hash
            )
            action = "conflict"

    _assert_same_identity(ocorrencia, key)
    if not acquired:
        ocorrencia.last_seen_at = now
        ocorrencia.seen_count += 1
        ocorrencia.last_source_file = (source_file or "").strip()
        ocorrencia.last_source_hash = (source_hash or "").strip()
        if not ocorrencia.source:
            ocorrencia.source = (source or "").strip()
        ocorrencia.save(
            update_fields=[
                "last_seen_at",
                "seen_count",
                "last_source_file",
                "last_source_hash",
                "source",
                "updated_at",
            ]
        )
    return ReinspecaoOccurrenceReservation(ocorrencia, action, acquired)


def _target_key(*, contexto: str, target) -> ReinspecaoOccurrenceKey:
    return build_reinspecao_occurrence_key(
        contexto=contexto,
        protocolo=target.protocolo,
        descricao_irregularidades=target.descricao_irregularidades,
        data_contestacao=target.data_contestacao,
    )


@transaction.atomic
def link_reinspecao_occurrence(
    ocorrencia: ReinspecaoOcorrencia | int,
    *,
    pendente: QualidadePendenteReinspecao | None = None,
    tratado: AuditoriaFalhaCadastro | None = None,
) -> tuple[ReinspecaoOcorrencia, bool]:
    """Vincula idempotentemente a reserva ao pendente ou ao tratado exato."""
    if (pendente is None) == (tratado is None):
        raise ValueError("Informe exatamente um destino: pendente ou tratado.")
    occurrence_id = ocorrencia.pk if isinstance(ocorrencia, ReinspecaoOcorrencia) else ocorrencia
    locked = ReinspecaoOcorrencia.objects.select_for_update().get(pk=occurrence_id)
    target = tratado or pendente
    occurrence_key = build_reinspecao_occurrence_key(
        contexto=locked.contexto,
        protocolo=locked.protocolo,
        descricao_irregularidades=locked.descricao_irregularidades,
        data_contestacao=locked.data_contestacao,
    )
    if _target_key(contexto=locked.contexto, target=target) != occurrence_key:
        raise ValueError("O destino nao corresponde a ocorrencia reservada.")

    now = timezone.now()
    if tratado is not None:
        if locked.tratado_id and locked.tratado_id != tratado.pk:
            raise ValueError("A ocorrencia ja esta vinculada a outro tratado.")
        changed = locked.tratado_id != tratado.pk or locked.status != ReinspecaoOcorrencia.STATUS_TRATADA
        locked.tratado = tratado
        locked.pendente = None
        locked.status = ReinspecaoOcorrencia.STATUS_TRATADA
    else:
        if locked.tratado_id:
            raise ValueError("Ocorrencia tratada nao pode voltar ao estado pendente.")
        if locked.pendente_id and locked.pendente_id != pendente.pk:
            raise ValueError("A ocorrencia ja esta vinculada a outro pendente.")
        changed = (
            locked.pendente_id != pendente.pk
            or locked.status != ReinspecaoOcorrencia.STATUS_PENDENTE
        )
        locked.pendente = pendente
        locked.status = ReinspecaoOcorrencia.STATUS_PENDENTE
    if changed:
        locked.linked_at = now
        locked.save(
            update_fields=["pendente", "tratado", "status", "linked_at", "updated_at"]
        )
    return locked, changed
