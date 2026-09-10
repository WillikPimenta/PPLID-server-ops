"""Sincronização de cores entre tipos de status e tipos de ocorrência."""

from __future__ import annotations

import unicodedata

from ..models import OccurrenceType, StatusType


def _normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().strip().split())


def names_match_occurrence_status(occurrence_name: str, status_name: str) -> bool:
    occ = _normalize_name(occurrence_name)
    status = _normalize_name(status_name)
    if not occ or not status:
        return False
    if occ == status:
        return True
    if occ in status or status in occ:
        return True

    keyword_pairs = (
        (("portais", "acesso"), ("portais", "elevate")),
        (("indisponivel", "sistema"), ("problemas sistemicos",)),
        (("suporte tecnico",), ("problemas sistemicos",)),
    )
    for occ_keys, status_keys in keyword_pairs:
        if any(key in occ for key in occ_keys) and any(key in status for key in status_keys):
            return True
    return False


def find_matching_status(
    occurrence_name: str,
    statuses: list[StatusType] | None = None,
) -> StatusType | None:
    statuses = statuses if statuses is not None else list(StatusType.objects.all())
    for status in statuses:
        if names_match_occurrence_status(occurrence_name, status.name):
            return status
    return None


def find_matching_occurrence_types(
    status_name: str,
    occurrences: list[OccurrenceType] | None = None,
) -> list[OccurrenceType]:
    occurrences = occurrences if occurrences is not None else list(OccurrenceType.objects.all())
    return [
        occurrence
        for occurrence in occurrences
        if names_match_occurrence_status(occurrence.name, status_name)
    ]


def resolve_occurrence_color(
    occurrence_name: str,
    statuses: list[StatusType] | None = None,
) -> str:
    status = find_matching_status(occurrence_name, statuses)
    return status.color if status and status.color else ""


def sync_occurrence_colors_for_status(status: StatusType) -> int:
    updated = 0
    for occurrence in find_matching_occurrence_types(status.name):
        if occurrence.color != status.color:
            occurrence.color = status.color
            occurrence.save(update_fields=["color"])
            updated += 1
    return updated


def sync_status_colors_for_occurrence(occurrence: OccurrenceType) -> int:
    status = find_matching_status(occurrence.name)
    if not status or not occurrence.color or status.color == occurrence.color:
        return 0
    status.color = occurrence.color
    status.save(update_fields=["color"])
    return 1


def seed_occurrence_colors_from_statuses() -> int:
    statuses = list(StatusType.objects.all())
    updated = 0
    for occurrence in OccurrenceType.objects.all():
        color = resolve_occurrence_color(occurrence.name, statuses)
        if color and occurrence.color != color:
            occurrence.color = color
            occurrence.save(update_fields=["color"])
            updated += 1
    return updated
