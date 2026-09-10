"""Vigência de ciclos (MetaEtapa / ProjecaoSla) sem gaps."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from typing import Any

from apps.dimensoes_processos.models import MetaEtapa, ProjecaoSla


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError("Data inválida. Use o formato AAAA-MM-DD.") from exc


def _require_vigente(instance: Model) -> None:
    if getattr(instance, "data_fim", None) is not None:
        raise ValidationError("Só é possível alterar ciclos vigentes (data fim vazia).")


def find_vigente_meta(*, etapa_id: int, servico_id: int | None) -> MetaEtapa | None:
    qs = MetaEtapa.objects.filter(etapa_id=etapa_id, data_fim__isnull=True)
    if servico_id is None:
        qs = qs.filter(servico_id__isnull=True)
    else:
        qs = qs.filter(servico_id=servico_id)
    return qs.order_by("-data_inicio").first()


def find_vigente_sla(
    *,
    cliente_id: int,
    workflow_id: int,
    nivel_hierarquico_id: int,
    dias_semana: str,
) -> ProjecaoSla | None:
    return (
        ProjecaoSla.objects.filter(
            cliente_id=cliente_id,
            workflow_id=workflow_id,
            nivel_hierarquico_id=nivel_hierarquico_id,
            dias_semana=str(dias_semana or "").strip(),
            data_fim__isnull=True,
        )
        .order_by("-data_inicio")
        .first()
    )


@transaction.atomic
def criar_meta_ciclo(
    *,
    data_inicio: date | str,
    etapa_id: int,
    meta_dia: Decimal | float | str | int,
    servico_id: int | None = None,
) -> MetaEtapa:
    inicio = _as_date(data_inicio)
    if find_vigente_meta(etapa_id=etapa_id, servico_id=servico_id):
        raise ValidationError(
            "Já existe um ciclo vigente para esta etapa/serviço. "
            "Use 'Alterar ciclo' ou 'Finalizar' antes de criar outro."
        )
    return MetaEtapa.objects.create(
        data_inicio=inicio,
        data_fim=None,
        etapa_id=etapa_id,
        servico_id=servico_id,
        meta_dia=meta_dia,
    )


@transaction.atomic
def criar_sla_ciclo(
    *,
    data_inicio: date | str,
    cliente_id: int,
    workflow_id: int,
    nivel_hierarquico_id: int,
    dias_semana: str,
    hora_inicio=None,
    hora_fim=None,
    duracao_atendimento=None,
    sla_segundos=None,
    flag_ajuste_sla=None,
    sla_ajuste=None,
    volume=None,
) -> ProjecaoSla:
    inicio = _as_date(data_inicio)
    dias = str(dias_semana or "").strip()
    if not dias:
        raise ValidationError("Informe dias_semana (ex.: {0..4}).")
    if find_vigente_sla(
        cliente_id=cliente_id,
        workflow_id=workflow_id,
        nivel_hierarquico_id=nivel_hierarquico_id,
        dias_semana=dias,
    ):
        raise ValidationError(
            "Já existe um ciclo vigente para esta chave (cliente/workflow/NH/dias). "
            "Use 'Alterar ciclo' ou 'Finalizar' antes de criar outro."
        )
    return ProjecaoSla.objects.create(
        data_inicio=inicio,
        data_fim=None,
        cliente_id=cliente_id,
        workflow_id=workflow_id,
        nivel_hierarquico_id=nivel_hierarquico_id,
        dias_semana=dias,
        hora_inicio=hora_inicio or None,
        hora_fim=hora_fim or None,
        duracao_atendimento=duracao_atendimento,
        sla_segundos=sla_segundos,
        flag_ajuste_sla=flag_ajuste_sla,
        sla_ajuste=sla_ajuste,
        volume=volume,
    )


@transaction.atomic
def finalizar_ciclo(instance: MetaEtapa | ProjecaoSla, data_fim: date | str) -> MetaEtapa | ProjecaoSla:
    _require_vigente(instance)
    fim = _as_date(data_fim)
    if fim < instance.data_inicio:
        raise ValidationError("Data fim deve ser maior ou igual à data início do ciclo.")
    instance.data_fim = fim
    instance.save(update_fields=["data_fim"])
    return instance


@transaction.atomic
def rotacionar_meta_ciclo(
    instance: MetaEtapa,
    *,
    data_inicio_novo: date | str,
    meta_dia: Decimal | float | str | int,
) -> tuple[MetaEtapa, MetaEtapa]:
    _require_vigente(instance)
    inicio_novo = _as_date(data_inicio_novo)
    if inicio_novo <= instance.data_inicio:
        raise ValidationError("A data início do novo ciclo deve ser posterior à do ciclo vigente.")
    fim_antigo = inicio_novo - timedelta(days=1)
    instance.data_fim = fim_antigo
    instance.save(update_fields=["data_fim"])
    novo = MetaEtapa.objects.create(
        data_inicio=inicio_novo,
        data_fim=None,
        etapa_id=instance.etapa_id,
        servico_id=instance.servico_id,
        meta_dia=meta_dia,
    )
    return instance, novo


@transaction.atomic
def rotacionar_sla_ciclo(
    instance: ProjecaoSla,
    *,
    data_inicio_novo: date | str,
    hora_inicio=None,
    hora_fim=None,
    duracao_atendimento=None,
    sla_segundos=None,
    flag_ajuste_sla=None,
    sla_ajuste=None,
    volume=None,
) -> tuple[ProjecaoSla, ProjecaoSla]:
    """Rotaciona mantendo a chave dimensional do vigente."""
    _require_vigente(instance)
    inicio_novo = _as_date(data_inicio_novo)
    if inicio_novo <= instance.data_inicio:
        raise ValidationError("A data início do novo ciclo deve ser posterior à do ciclo vigente.")
    fim_antigo = inicio_novo - timedelta(days=1)
    instance.data_fim = fim_antigo
    instance.save(update_fields=["data_fim"])

    novo = ProjecaoSla.objects.create(
        data_inicio=inicio_novo,
        data_fim=None,
        cliente_id=instance.cliente_id,
        workflow_id=instance.workflow_id,
        nivel_hierarquico_id=instance.nivel_hierarquico_id,
        dias_semana=instance.dias_semana,
        hora_inicio=hora_inicio if hora_inicio is not None else instance.hora_inicio,
        hora_fim=hora_fim if hora_fim is not None else instance.hora_fim,
        duracao_atendimento=(
            duracao_atendimento if duracao_atendimento is not None else instance.duracao_atendimento
        ),
        sla_segundos=sla_segundos if sla_segundos is not None else instance.sla_segundos,
        flag_ajuste_sla=flag_ajuste_sla if flag_ajuste_sla is not None else instance.flag_ajuste_sla,
        sla_ajuste=sla_ajuste if sla_ajuste is not None else instance.sla_ajuste,
        volume=volume if volume is not None else instance.volume,
    )
    return instance, novo
