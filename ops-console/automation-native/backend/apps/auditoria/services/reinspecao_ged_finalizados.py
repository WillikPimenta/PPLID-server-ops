"""Consulta de protocolos finalizados no GED para cadastro na fila de reinspeção."""
from __future__ import annotations

from typing import Iterable

from django.db import models

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    QualidadePendenteAuditoriaCompliance,
    QualidadePendenteReinspecao,
)
from apps.auditoria.models import ReinspecaoAuditorPresence
from apps.auditoria.services.reinspecao_fila import FILA_CONTEXTO_AUDITORIA_COMPLIANCE
from apps.rotina_bruto.models import RotinaGedIrregularidadeTratadoRecord
from apps.rotina_bruto.services.parsers import parse_protocolo

from apps.auditoria.services.reinspecao_import import (
    ParsedReinspecaoRow,
    avaliar_elegibilidade_reinspecao,
)
from apps.auditoria.services.reinspecao_import_dedupe import (
    ReinspecaoOccurrenceKey,
    build_reinspecao_occurrence_key,
)


def existing_protocolos_reinspecao(*, contexto: str | None = None) -> set[str]:
    """Protocolos já na fila ou tratados no PPLID (mesmo contexto de fila)."""
    ctx = contexto or ReinspecaoAuditorPresence.CONTEXTO_REINSPECAO
    if ctx == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        pendentes = QualidadePendenteAuditoriaCompliance.objects.values_list(
            "protocolo", flat=True
        )
    else:
        pendentes = QualidadePendenteReinspecao.objects.filter(contexto=ctx).values_list(
            "protocolo", flat=True
        )
    tratados = AuditoriaFalhaCadastro.objects.filter(
        origem=(
            AuditoriaFalhaCadastro.ORIGEM_AUDITORIA
            if ctx == FILA_CONTEXTO_AUDITORIA_COMPLIANCE
            else AuditoriaFalhaCadastro.ORIGEM_REINSPECAO
        )
    )
    if ctx == FILA_CONTEXTO_AUDITORIA_COMPLIANCE:
        tratados = tratados.filter(brflow_parsed__fila_contexto=ctx)
    tratado_protocolos = tratados.values_list("protocolo", flat=True)
    return {str(p).strip() for p in pendentes if str(p).strip()} | {
        str(p).strip() for p in tratado_protocolos if str(p).strip()
    }


def protocolos_finalizados_ged(protocolos: Iterable[str]) -> set[str]:
    """Protocolos com Data da Resposta preenchida no snapshot GED (rotina)."""
    normalized: dict[str, int] = {}
    for raw in protocolos:
        text = str(raw or "").strip()
        if not text:
            continue
        parsed = parse_protocolo(text)
        if parsed is not None:
            normalized[text] = parsed
    if not normalized:
        return set()

    finalized_ints = set(
        RotinaGedIrregularidadeTratadoRecord.objects.filter(
            protocolo__in=list(normalized.values()),
            data_resposta__isnull=False,
        ).values_list("protocolo", flat=True)
    )
    return {text for text, proto_int in normalized.items() if proto_int in finalized_ints}


def ocorrencias_finalizadas_ged(
    rows: Iterable[ParsedReinspecaoRow],
    *,
    contexto: str,
) -> set[ReinspecaoOccurrenceKey]:
    """Retorna apenas finalizações GED com identidade completa e inequívoca.

    O snapshot legado armazena ``data_contestacao`` como ``DateField``. Ele não
    distingue duas contestações no mesmo dia e, portanto, não pode bloquear uma
    ocorrência identificada por data/hora.
    """
    contestacao_field = RotinaGedIrregularidadeTratadoRecord._meta.get_field(
        "data_contestacao"
    )
    if not isinstance(contestacao_field, models.DateTimeField):
        return set()

    candidate_keys: set[ReinspecaoOccurrenceKey] = set()
    protocolos: set[int] = set()
    for row in rows:
        key = build_reinspecao_occurrence_key(
            contexto=contexto,
            protocolo=row.protocolo,
            descricao_irregularidades=row.descricao_irregularidades,
            data_contestacao=row.data_contestacao,
        )
        candidate_keys.add(key)
        protocolo = parse_protocolo(str(row.protocolo or ""))
        if protocolo is not None:
            protocolos.add(protocolo)

    if not protocolos:
        return set()

    finalized: set[ReinspecaoOccurrenceKey] = set()
    snapshots = RotinaGedIrregularidadeTratadoRecord.objects.filter(
        protocolo__in=protocolos,
        data_resposta__isnull=False,
    ).only(
        "protocolo",
        "descricao_irregularidades",
        "data_contestacao",
        "data_resposta",
    )
    for snapshot in snapshots.iterator(chunk_size=500):
        elegivel, _ = avaliar_elegibilidade_reinspecao(
            descricao_irregularidades=snapshot.descricao_irregularidades,
            data_contestacao=snapshot.data_contestacao,
            data_resposta=snapshot.data_resposta,
        )
        if elegivel:
            continue
        key = build_reinspecao_occurrence_key(
            contexto=contexto,
            protocolo=str(snapshot.protocolo or ""),
            descricao_irregularidades=snapshot.descricao_irregularidades,
            data_contestacao=snapshot.data_contestacao,
        )
        if key in candidate_keys:
            finalized.add(key)
    return finalized
