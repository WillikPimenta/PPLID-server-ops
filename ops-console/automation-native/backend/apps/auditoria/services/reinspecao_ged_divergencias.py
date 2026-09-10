"""Persistencia e consulta das divergencias entre GED e Reinspecao no PPLID."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.auditoria.models import (
    AuditoriaFalhaCadastro,
    ReinspecaoGedDivergencia,
    ReinspecaoGedDivergenciaEvento,
)
from apps.auditoria.services.reinspecao_import_dedupe import (
    build_reinspecao_occurrence_key,
    reinspecao_occurrence_key_hash,
)
from apps.auditoria.services.reinspecao_ocorrencias import (
    link_reinspecao_occurrence,
    reserve_reinspecao_occurrence,
)
from apps.common.models import BotDataIngestion, BotDbSyncJob


@dataclass(frozen=True)
class GedOccurrenceReconcileResult:
    action: str
    divergencia: ReinspecaoGedDivergencia | None


def _event(
    divergencia: ReinspecaoGedDivergencia,
    *,
    tipo: str,
    actor=None,
    source_file: str = "",
    source_hash: str = "",
    detalhe: dict[str, Any] | None = None,
) -> ReinspecaoGedDivergenciaEvento:
    return ReinspecaoGedDivergenciaEvento.objects.create(
        divergencia=divergencia,
        tipo=tipo,
        actor=actor,
        source_file=source_file,
        source_hash=source_hash,
        detalhe=detalhe or {},
    )


def _occurrence_identity(
    *,
    contexto: str,
    protocolo: str,
    descricao_irregularidades: str,
    data_contestacao: datetime | None,
) -> tuple[str, tuple[str, str, str, str]]:
    if data_contestacao is None:
        raise ValueError("Data da contestacao obrigatoria para conciliar a ocorrencia GED.")
    key = build_reinspecao_occurrence_key(
        contexto=contexto,
        protocolo=protocolo,
        descricao_irregularidades=descricao_irregularidades,
        data_contestacao=data_contestacao,
    )
    return reinspecao_occurrence_key_hash(key), key


@transaction.atomic
def reconcile_ged_occurrence(
    *,
    contexto: str,
    protocolo: str,
    descricao_irregularidades: str,
    data_contestacao: datetime | None,
    ged_elegivel: bool,
    tratado: AuditoriaFalhaCadastro | None = None,
    source_file: str = "",
    source_hash: str = "",
    actor=None,
    observed_at: datetime | None = None,
) -> GedOccurrenceReconcileResult:
    """Concilia uma ocorrencia exata; ausencia em uma janela nunca chama esta funcao."""
    if data_contestacao is not None and timezone.is_naive(data_contestacao):
        data_contestacao = timezone.make_aware(
            data_contestacao,
            timezone.get_current_timezone(),
        )
    occurrence_hash, occurrence_key = _occurrence_identity(
        contexto=contexto,
        protocolo=protocolo,
        descricao_irregularidades=descricao_irregularidades,
        data_contestacao=data_contestacao,
    )
    now = observed_at or timezone.now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, timezone.get_current_timezone())

    if not ged_elegivel:
        divergencia = (
            ReinspecaoGedDivergencia.objects.select_for_update()
            .filter(occurrence_key=occurrence_hash)
            .first()
        )
        if divergencia is None or divergencia.status == ReinspecaoGedDivergencia.STATUS_RESOLVIDA:
            return GedOccurrenceReconcileResult("unchanged", divergencia)
        divergencia.status = ReinspecaoGedDivergencia.STATUS_RESOLVIDA
        divergencia.resolvida_em = now
        divergencia.resolvida_por = actor
        divergencia.resolucao_motivo = "ged_atualizado"
        divergencia.last_source_file = source_file
        divergencia.last_source_hash = source_hash
        divergencia.save(
            update_fields=[
                "status",
                "resolvida_em",
                "resolvida_por",
                "resolucao_motivo",
                "last_source_file",
                "last_source_hash",
                "updated_at",
            ]
        )
        _event(
            divergencia,
            tipo=ReinspecaoGedDivergenciaEvento.TIPO_RESOLVIDA,
            actor=actor,
            source_file=source_file,
            source_hash=source_hash,
            detalhe={"motivo": "ged_atualizado"},
        )
        return GedOccurrenceReconcileResult("resolved", divergencia)

    if tratado is None:
        return GedOccurrenceReconcileResult("unchanged", None)

    tratado_key = build_reinspecao_occurrence_key(
        contexto=contexto,
        protocolo=tratado.protocolo,
        descricao_irregularidades=tratado.descricao_irregularidades,
        data_contestacao=tratado.data_contestacao,
    )
    if tratado_key != occurrence_key:
        raise ValueError("O registro tratado nao corresponde a ocorrencia exata do GED.")

    reservation = reserve_reinspecao_occurrence(
        contexto=contexto,
        protocolo=protocolo,
        descricao_irregularidades=descricao_irregularidades,
        data_contestacao=data_contestacao,
        source="ged_irregularidade",
        source_file=source_file,
        source_hash=source_hash,
        observed_at=now,
    )
    ocorrencia, _ = link_reinspecao_occurrence(
        reservation.ocorrencia,
        tratado=tratado,
    )

    defaults = {
        "contexto": occurrence_key[0],
        "protocolo": occurrence_key[1],
        "descricao_irregularidades": descricao_irregularidades,
        "irregularidade_normalizada": occurrence_key[2],
        "data_contestacao": data_contestacao,
        "ocorrencia": ocorrencia,
        "tratado": tratado,
        "status": ReinspecaoGedDivergencia.STATUS_ABERTA,
        "first_seen_at": now,
        "last_seen_at": now,
        "seen_count": 1,
        "last_source_file": source_file,
        "last_source_hash": source_hash,
    }
    divergencia, created = ReinspecaoGedDivergencia.objects.get_or_create(
        occurrence_key=occurrence_hash,
        defaults=defaults,
    )
    if created:
        _event(
            divergencia,
            tipo=ReinspecaoGedDivergenciaEvento.TIPO_DETECTADA,
            actor=actor,
            source_file=source_file,
            source_hash=source_hash,
        )
        return GedOccurrenceReconcileResult("created", divergencia)

    divergencia = ReinspecaoGedDivergencia.objects.select_for_update().get(pk=divergencia.pk)
    was_resolved = divergencia.status == ReinspecaoGedDivergencia.STATUS_RESOLVIDA
    divergencia.last_seen_at = now
    divergencia.seen_count += 1
    divergencia.last_source_file = source_file
    divergencia.last_source_hash = source_hash
    divergencia.ocorrencia = ocorrencia
    divergencia.tratado = tratado
    update_fields = [
        "last_seen_at",
        "seen_count",
        "last_source_file",
        "last_source_hash",
        "ocorrencia",
        "tratado",
        "updated_at",
    ]
    if was_resolved:
        divergencia.status = ReinspecaoGedDivergencia.STATUS_ABERTA
        divergencia.reopened_count += 1
        divergencia.reconhecida_por = None
        divergencia.reconhecida_em = None
        divergencia.reconhecimento_observacao = ""
        divergencia.resolvida_por = None
        divergencia.resolvida_em = None
        divergencia.resolucao_motivo = ""
        update_fields.extend(
            [
                "status",
                "reopened_count",
                "reconhecida_por",
                "reconhecida_em",
                "reconhecimento_observacao",
                "resolvida_por",
                "resolvida_em",
                "resolucao_motivo",
            ]
        )
    divergencia.save(update_fields=update_fields)
    if was_resolved:
        _event(
            divergencia,
            tipo=ReinspecaoGedDivergenciaEvento.TIPO_REABERTA,
            actor=actor,
            source_file=source_file,
            source_hash=source_hash,
        )
        return GedOccurrenceReconcileResult("reopened", divergencia)
    return GedOccurrenceReconcileResult("seen", divergencia)


@transaction.atomic
def reconhecer_divergencia(*, divergencia_id: int, user, observacao: str = ""):
    divergencia = ReinspecaoGedDivergencia.objects.select_for_update().get(pk=divergencia_id)
    if divergencia.status == ReinspecaoGedDivergencia.STATUS_RESOLVIDA:
        raise ValueError("Divergencia resolvida nao pode ser reconhecida.")
    if divergencia.status == ReinspecaoGedDivergencia.STATUS_RECONHECIDA:
        return divergencia, False
    now = timezone.now()
    divergencia.status = ReinspecaoGedDivergencia.STATUS_RECONHECIDA
    divergencia.reconhecida_por = user
    divergencia.reconhecida_em = now
    divergencia.reconhecimento_observacao = (observacao or "").strip()
    divergencia.save(
        update_fields=[
            "status",
            "reconhecida_por",
            "reconhecida_em",
            "reconhecimento_observacao",
            "updated_at",
        ]
    )
    _event(
        divergencia,
        tipo=ReinspecaoGedDivergenciaEvento.TIPO_RECONHECIDA,
        actor=user,
        detalhe={"observacao": divergencia.reconhecimento_observacao},
    )
    return divergencia, True


def serialize_divergencia(divergencia: ReinspecaoGedDivergencia) -> dict[str, Any]:
    tratado = divergencia.tratado
    tratado_em = tratado.data_analise_intranet or tratado.data_analise or tratado.created_at
    auditor_nome = (
        tratado.auditor_ref.full_name
        if tratado.auditor_ref_id and tratado.auditor_ref
        else tratado.auditor
    )
    return {
        "id": divergencia.pk,
        "contexto": divergencia.contexto,
        "occurrence_key": divergencia.occurrence_key,
        "protocolo": divergencia.protocolo,
        "descricao_irregularidades": divergencia.descricao_irregularidades,
        "data_contestacao": divergencia.data_contestacao.isoformat(),
        "status": divergencia.status,
        "first_seen_at": divergencia.first_seen_at.isoformat(),
        "last_seen_at": divergencia.last_seen_at.isoformat(),
        "seen_count": divergencia.seen_count,
        "reopened_count": divergencia.reopened_count,
        "last_source_file": divergencia.last_source_file,
        "last_source_hash": divergencia.last_source_hash,
        "tratado": {
            "id": tratado.pk,
            "status": tratado.status,
            "auditor": auditor_nome or "Auditor não informado",
            "tratado_em": tratado_em.isoformat() if tratado_em else None,
        },
        "reconhecida_por": (
            str(divergencia.reconhecida_por_id) if divergencia.reconhecida_por_id else None
        ),
        "reconhecida_em": (
            divergencia.reconhecida_em.isoformat() if divergencia.reconhecida_em else None
        ),
        "reconhecimento_observacao": divergencia.reconhecimento_observacao,
        "resolvida_em": divergencia.resolvida_em.isoformat() if divergencia.resolvida_em else None,
        "resolucao_motivo": divergencia.resolucao_motivo,
    }


def get_reinspecao_ged_ultima_ingestao_em() -> str | None:
    """Timestamp ISO da última ingestão concluída do bot GED de reinspeção."""
    latest_ingestion = (
        BotDataIngestion.objects.filter(
            domain=BotDbSyncJob.DOMAIN_REINSPECAO_GED,
            status=BotDataIngestion.STATUS_COMPLETED,
        )
        .order_by("-finished_at", "-id")
        .first()
    )
    if latest_ingestion and latest_ingestion.finished_at:
        return latest_ingestion.finished_at.isoformat()
    return None


def _reinspecao_ged_latest_reference_date():
    latest_ingestion = (
        BotDataIngestion.objects.filter(
            domain=BotDbSyncJob.DOMAIN_REINSPECAO_GED,
            status=BotDataIngestion.STATUS_COMPLETED,
        )
        .order_by("-finished_at", "-id")
        .first()
    )
    if latest_ingestion is None:
        return None, None
    latest_date = latest_ingestion.reference_date
    if latest_date is None and latest_ingestion.finished_at is not None:
        latest_date = timezone.localtime(latest_ingestion.finished_at).date()
    return latest_ingestion, latest_date


def resumo_divergencias(*, contexto: str = "reinspecao") -> dict[str, Any]:
    values = ReinspecaoGedDivergencia.objects.filter(contexto=contexto).aggregate(
        total=Count("id"),
        abertas=Count("id", filter=Q(status=ReinspecaoGedDivergencia.STATUS_ABERTA)),
        reconhecidas=Count(
            "id", filter=Q(status=ReinspecaoGedDivergencia.STATUS_RECONHECIDA)
        ),
        resolvidas=Count("id", filter=Q(status=ReinspecaoGedDivergencia.STATUS_RESOLVIDA)),
    )
    result = {key: int(value or 0) for key, value in values.items()}
    result["total_ativas"] = result["abertas"] + result["reconhecidas"]
    _, latest_date = _reinspecao_ged_latest_reference_date()
    result["ultima_ingestao_em"] = get_reinspecao_ged_ultima_ingestao_em()
    result["lacuna_cobertura"] = (
        latest_date is None or (timezone.localdate() - latest_date).days > 10
    )
    return result


def build_ged_portal_export_rows(*, contexto: str = "reinspecao") -> list[dict[str, Any]]:
    """Exportação das divergências GED × Portal (mesmos dados exibidos no painel)."""
    from apps.auditoria.services.reinspecao_sla import format_brasilia_datetime

    qs = (
        ReinspecaoGedDivergencia.objects.filter(contexto=contexto)
        .exclude(status=ReinspecaoGedDivergencia.STATUS_RESOLVIDA)
        .select_related("tratado", "tratado__auditor_ref", "tratado__responsavel")
        .order_by("-last_seen_at", "protocolo")
    )
    rows: list[dict[str, Any]] = []
    for item in qs.iterator(chunk_size=200):
        data = serialize_divergencia(item)
        tratado = data["tratado"]
        rows.append(
            {
                "protocolo": data["protocolo"],
                "descricao_irregularidades": data["descricao_irregularidades"],
                "data_contestacao": format_brasilia_datetime(data["data_contestacao"]),
                "seen_count": data["seen_count"],
                "status_divergencia": data["status"],
                "portal_status": tratado["status"],
                "portal_auditor": tratado["auditor"],
                "portal_auditoria_em": format_brasilia_datetime(tratado["tratado_em"]),
                "first_seen_at": format_brasilia_datetime(data["first_seen_at"]),
                "last_seen_at": format_brasilia_datetime(data["last_seen_at"]),
            }
        )
    return rows


def listar_divergencias(
    *,
    contexto: str = "reinspecao",
    status: str = "",
    protocolo: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    qs = ReinspecaoGedDivergencia.objects.filter(contexto=contexto).select_related(
        "tratado", "tratado__auditor_ref", "reconhecida_por", "resolvida_por"
    )
    if status == "ativas":
        qs = qs.exclude(status=ReinspecaoGedDivergencia.STATUS_RESOLVIDA)
    elif status in dict(ReinspecaoGedDivergencia.STATUS_CHOICES):
        qs = qs.filter(status=status)
    if protocolo.strip():
        qs = qs.filter(protocolo__icontains=protocolo.strip())
    if date_from:
        qs = qs.filter(last_seen_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(last_seen_at__date__lte=date_to)
    total = qs.count()
    safe_page_size = min(100, max(1, int(page_size)))
    safe_page = max(1, int(page))
    start = (safe_page - 1) * safe_page_size
    results = [serialize_divergencia(item) for item in qs[start : start + safe_page_size]]
    return {
        "results": results,
        "total": total,
        "page": safe_page,
        "page_size": safe_page_size,
    }
