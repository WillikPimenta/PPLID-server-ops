# -*- coding: utf-8 -*-
"""Chave composta protocolo + matrícula (paridade BRB ``chave_caso``)."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from django.db.models import QuerySet, Value
from django.db.models.functions import Coalesce

from apps.replicacao_d1.normalization import normalize_protocolo
from apps.qualidade_operacional.services.normalize import normalize_matricula

CASE_KEY_SEP = "|"

SKIP_REASON_DUPLICATE_PROTOCOLO_MATRICULA = "duplicate_protocolo_matricula"

ConflictAction = Literal["accept", "skip", "replace"]

# Data distante para nulls-last em ordenação ASC (nulls por último).
_NULL_DATE_SENTINEL = date(9999, 12, 31)
_MAX_PK_SENTINEL = 2**31 - 1


def build_case_key(
    protocolo: object | None,
    matricula: object | None,
) -> tuple[str, str]:
    """Retorna par normalizado (protocolo, matricula)."""
    return normalize_protocolo(
        None if protocolo is None else str(protocolo).strip() or None
    ), normalize_matricula(matricula)


def build_case_key_str(
    protocolo: object | None,
    matricula: object | None,
) -> str:
    """Chave única ``protocolo|matricula``; vazia quando incompleta."""
    proto, mat = build_case_key(protocolo, matricula)
    if not proto or not mat:
        return ""
    return f"{proto}{CASE_KEY_SEP}{mat}"


def is_case_key_eligible(
    protocolo: object | None,
    matricula: object | None,
) -> bool:
    return bool(build_case_key_str(protocolo, matricula))


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return None


def falha_audit_sort_key(
    *,
    data: date | None = None,
    data_analise: date | None = None,
    pk: int | None = None,
    falha: Any | None = None,
) -> tuple[date, date, int]:
    """Tupla de ordenação: auditoria mais antiga primeiro (nulls por último)."""
    if falha is not None:
        data = getattr(falha, "data", data)
        data_analise = getattr(falha, "data_analise", data_analise)
        pk = getattr(falha, "pk", pk)
    return (
        _coerce_date(data) or _NULL_DATE_SENTINEL,
        _coerce_date(data_analise) or _NULL_DATE_SENTINEL,
        int(pk or _MAX_PK_SENTINEL),
    )


def canonical_falha_ordering(qs: QuerySet) -> QuerySet:
    """Ordena falhas pela auditoria mais antiga (``data``, ``data_analise``, ``pk``)."""
    sentinel = Value(_NULL_DATE_SENTINEL)
    return qs.order_by(
        Coalesce("data", sentinel).asc(),
        Coalesce("data_analise", sentinel).asc(),
        "pk",
    )


def find_canonical_falha(
    protocolo: object | None = None,
    matricula: object | None = None,
    *,
    case_key: str | None = None,
    exclude_falha_id: int | None = None,
):
    """Retorna a falha canônica (auditoria mais antiga) para a chave composta."""
    from apps.qualidade_operacional.models import QualidadeFalha

    key = case_key or build_case_key_str(protocolo, matricula)
    if not key:
        return None
    qs = QualidadeFalha.objects.filter(case_key=key)
    if exclude_falha_id:
        qs = qs.exclude(pk=exclude_falha_id)
    return canonical_falha_ordering(qs).first()


def find_conflicting_falha_case(
    protocolo: object | None,
    matricula: object | None,
    *,
    exclude_falha_id: int | None = None,
):
    """Retorna falha existente com a mesma chave composta (canônica = mais antiga)."""
    return find_canonical_falha(
        protocolo=protocolo,
        matricula=matricula,
        exclude_falha_id=exclude_falha_id,
    )


def compare_falha_priority(
    incoming: dict[str, Any] | Any,
    existing: Any,
) -> int:
    """Compara prioridade: negativo = incoming vence, positivo = existing vence, 0 = empate."""
    if isinstance(incoming, dict):
        incoming_key = falha_audit_sort_key(
            data=incoming.get("data"),
            data_analise=incoming.get("data_analise"),
        )
    else:
        incoming_key = falha_audit_sort_key(falha=incoming)
    existing_key = falha_audit_sort_key(falha=existing)
    if incoming_key < existing_key:
        return -1
    if incoming_key > existing_key:
        return 1
    return 0


def resolve_falha_conflict(
    incoming_fields: dict[str, Any],
    existing: Any | None,
) -> ConflictAction:
    """Decide aceitar, pular ou substituir falha incoming frente à existente."""
    if existing is None:
        return "accept"
    priority = compare_falha_priority(incoming_fields, existing)
    if priority < 0:
        return "replace"
    if priority > 0:
        return "skip"
    return "skip"


def attach_case_key_to_falha_fields(fields: dict) -> dict:
    """Preenche ``case_key`` nos campos de ``QualidadeFalha``."""
    fields = dict(fields)
    fields["case_key"] = build_case_key_str(
        fields.get("protocolo"),
        fields.get("matricula"),
    )
    return fields


def delete_non_canonical_falhas(
    falha_ids: list[int],
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Remove falhas duplicadas e limpa projeções Intranet órfãs."""
    from apps.qualidade_operacional.models import QualidadeFalha, QualidadeIntranetProjection

    ids = [int(i) for i in falha_ids if i]
    if not ids:
        return {"deleted": 0, "projections_cleared": 0, "dry_run": dry_run}

    if dry_run:
        projection_count = QualidadeIntranetProjection.objects.filter(falha_id__in=ids).count()
        return {
            "deleted": len(ids),
            "projections_cleared": projection_count,
            "dry_run": True,
        }

    projection_count = QualidadeIntranetProjection.objects.filter(falha_id__in=ids).update(
        falha_id=None,
        sync_status=QualidadeIntranetProjection.STATUS_SKIPPED,
        sync_error="duplicate_protocolo_matricula: falha não-canônica removida",
    )
    deleted, _ = QualidadeFalha.objects.filter(pk__in=ids).delete()
    return {
        "deleted": deleted,
        "projections_cleared": projection_count,
        "dry_run": False,
    }


def dedupe_falha_fields_in_memory(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Dedupe in-memory mantendo registro com ``data`` mais antiga por ``case_key``."""
    best_by_key: dict[str, dict[str, Any]] = {}
    skipped = 0
    no_key: list[dict[str, Any]] = []

    for fields in items:
        key = fields.get("case_key") or build_case_key_str(
            fields.get("protocolo"),
            fields.get("matricula"),
        )
        if not key:
            no_key.append(fields)
            continue
        fields = dict(fields)
        fields["case_key"] = key
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = fields
            continue
        if compare_falha_priority(fields, existing) < 0:
            best_by_key[key] = fields
        skipped += 1

    return no_key + list(best_by_key.values()), skipped
