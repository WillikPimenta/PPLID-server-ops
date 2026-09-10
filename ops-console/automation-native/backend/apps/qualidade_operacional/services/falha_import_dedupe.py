# -*- coding: utf-8 -*-
"""Deduplicação de falhas na importação TSV (protocolo + matrícula)."""
from __future__ import annotations

from datetime import date
from typing import Any

from apps.qualidade_operacional.services.case_key import (
    build_case_key_str,
    compare_falha_priority,
    delete_non_canonical_falhas,
    find_canonical_falha,
    resolve_falha_conflict,
)


def _fields_from_falha_obj(obj: Any) -> dict[str, Any]:
    return {
        "protocolo": getattr(obj, "protocolo", ""),
        "matricula": getattr(obj, "matricula", ""),
        "case_key": getattr(obj, "case_key", "") or build_case_key_str(
            getattr(obj, "protocolo", ""),
            getattr(obj, "matricula", ""),
        ),
        "data": getattr(obj, "data", None),
        "data_analise": getattr(obj, "data_analise", None),
    }


def serialize_db_conflict(
    *,
    case_key: str,
    protocolo: str,
    matricula: str,
    incoming_data: date | None,
    existing: Any,
    action: str,
) -> dict[str, Any]:
    return {
        "case_key": case_key,
        "protocolo": protocolo,
        "matricula": matricula,
        "incoming_data": incoming_data.isoformat() if incoming_data else None,
        "existing_id": existing.pk,
        "existing_data": existing.data.isoformat() if existing.data else None,
        "existing_source_file": existing.source_file or "",
        "action": action,
    }


def dedupe_falha_objects_in_file(objects: list[Any]) -> tuple[list[Any], int]:
    """Mantém uma falha por ``case_key`` no arquivo (auditoria mais antiga)."""
    best_by_key: dict[str, Any] = {}
    no_key: list[Any] = []
    skipped = 0

    for obj in objects:
        fields = _fields_from_falha_obj(obj)
        key = fields["case_key"]
        if not key:
            no_key.append(obj)
            continue
        existing = best_by_key.get(key)
        if existing is None:
            best_by_key[key] = obj
            continue
        if compare_falha_priority(fields, _fields_from_falha_obj(existing)) < 0:
            best_by_key[key] = obj
        skipped += 1

    return no_key + list(best_by_key.values()), skipped


def resolve_falhas_against_db(objects: list[Any]) -> tuple[list[Any], list[dict[str, Any]], dict[str, int]]:
    """
    Filtra falhas que perdem para registro existente; remove não-canônicos quando incoming vence.

    Retorna (objetos a importar, conflitos amostra, estatísticas).
    """
    to_import: list[Any] = []
    conflicts: list[dict[str, Any]] = []
    stats = {
        "skipped_db": 0,
        "replaced_db": 0,
        "accepted": 0,
    }
    replace_ids: list[int] = []

    for obj in objects:
        fields = _fields_from_falha_obj(obj)
        key = fields["case_key"]
        if not key:
            to_import.append(obj)
            stats["accepted"] += 1
            continue

        existing = find_canonical_falha(case_key=key)
        if existing is None:
            to_import.append(obj)
            stats["accepted"] += 1
            continue

        action = resolve_falha_conflict(fields, existing)
        if len(conflicts) < 50:
            conflicts.append(
                serialize_db_conflict(
                    case_key=key,
                    protocolo=str(fields.get("protocolo") or ""),
                    matricula=str(fields.get("matricula") or ""),
                    incoming_data=fields.get("data"),
                    existing=existing,
                    action=action,
                )
            )

        if action == "skip":
            stats["skipped_db"] += 1
            continue

        if action == "replace":
            replace_ids.append(existing.pk)
            to_import.append(obj)
            stats["replaced_db"] += 1
            stats["accepted"] += 1
            continue

        to_import.append(obj)
        stats["accepted"] += 1

    if replace_ids:
        delete_non_canonical_falhas(replace_ids)

    return to_import, conflicts, stats


def prepare_falhas_for_import(objects: list[Any]) -> tuple[list[Any], list[dict[str, Any]], dict[str, int]]:
    """Pipeline completo: dedupe arquivo + resolução contra DB."""
    deduped, file_skipped = dedupe_falha_objects_in_file(objects)
    to_import, conflicts, stats = resolve_falhas_against_db(deduped)
    stats["skipped_file"] = file_skipped
    return to_import, conflicts, stats
