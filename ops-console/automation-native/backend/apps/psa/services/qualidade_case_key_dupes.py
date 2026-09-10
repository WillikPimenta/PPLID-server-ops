# -*- coding: utf-8 -*-
"""Diagnóstico somente leitura de duplicatas case_key em Qualidade (Console Ops)."""
from __future__ import annotations

from typing import Any

from django.db.models import Count

from apps.qualidade_operacional.models import QualidadeFalha
from apps.qualidade_operacional.services.case_key import canonical_falha_ordering
from apps.qualidade_operacional.services.source_config import INTRANET_SOURCE_FILE

SAMPLE_LIMIT = 50


def _source_bucket(source_file: str) -> str:
    if source_file == INTRANET_SOURCE_FILE:
        return "intranet"
    if source_file:
        return "tsv"
    return "unknown"


def check_qualidade_case_key_dupes(*, sample_limit: int = SAMPLE_LIMIT) -> dict[str, Any]:
    """Lista grupos com case_key repetida; indica registro canônico (auditoria mais antiga)."""
    limit = max(1, min(int(sample_limit or SAMPLE_LIMIT), 100))

    dup_keys = (
        QualidadeFalha.objects.exclude(case_key="")
        .values("case_key")
        .annotate(total=Count("id"))
        .filter(total__gt=1)
        .order_by("-total")
    )

    duplicate_groups = dup_keys.count()
    duplicate_rows = 0
    by_source: dict[str, int] = {"intranet": 0, "tsv": 0, "mixed": 0, "unknown": 0}
    sample: list[dict[str, Any]] = []

    for row in dup_keys.iterator():
        duplicate_rows += int(row["total"]) - 1
        if len(sample) >= limit:
            continue

        case_key = row["case_key"]
        falhas = list(
            canonical_falha_ordering(
                QualidadeFalha.objects.filter(case_key=case_key).only(
                    "id", "protocolo", "matricula", "data", "data_analise", "source_file"
                )
            )
        )
        if len(falhas) < 2:
            continue

        kept = falhas[0]
        removed = falhas[1:]
        buckets = {_source_bucket(f.source_file or "") for f in falhas}
        if len(buckets) > 1 or "mixed" in buckets:
            by_source["mixed"] += 1
        elif "intranet" in buckets:
            by_source["intranet"] += 1
        elif "tsv" in buckets:
            by_source["tsv"] += 1
        else:
            by_source["unknown"] += 1

        sample.append(
            {
                "case_key": case_key,
                "kept_id": kept.pk,
                "kept_data": kept.data.isoformat() if kept.data else None,
                "kept_source_file": kept.source_file or "",
                "removed_ids": [f.pk for f in removed],
                "removed_summary": [
                    {
                        "id": f.pk,
                        "data": f.data.isoformat() if f.data else None,
                        "source_file": f.source_file or "",
                    }
                    for f in removed
                ],
            }
        )

    status = "attention" if duplicate_groups > 0 else "ok"
    return {
        "ok": True,
        "status": status,
        "duplicate_groups": duplicate_groups,
        "duplicate_rows": duplicate_rows,
        "by_source_file": by_source,
        "sample_limit": limit,
        "sample": sample,
    }
