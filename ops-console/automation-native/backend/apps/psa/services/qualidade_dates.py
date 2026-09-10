# -*- coding: utf-8 -*-
"""Diagnóstico somente leitura de datas futuras em Qualidade (Console Ops)."""
from __future__ import annotations

from typing import Any

from django.db.models import Count, Max, Q
from django.utils import timezone

from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha

SAMPLE_LIMIT = 50

_CHECKS: tuple[tuple[type, str, str], ...] = (
    (QualidadeAuditado, "qualidade_auditado", "data"),
    (QualidadeAuditado, "qualidade_auditado", "data_analise"),
    (QualidadeFalha, "qualidade_falha", "data"),
    (QualidadeFalha, "qualidade_falha", "data_analise"),
)

_SAMPLE_FIELDS = ("id", "source_file", "data", "data_analise", "tipo_analise")


def _serialize_sample(row) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _SAMPLE_FIELDS:
        value = getattr(row, field, None)
        if hasattr(value, "isoformat"):
            out[field] = value.isoformat()
        else:
            out[field] = value if value is not None else ""
    return out


def check_qualidade_future_dates(*, sample_limit: int = SAMPLE_LIMIT) -> dict[str, Any]:
    """Analisa datas estritamente maiores que ``timezone.localdate()``.

    Não altera registros, não bloqueia o indicador nem importações.
    Status com casos futuros = ``attention`` (não erro destrutivo).
    """
    reference = timezone.localdate()
    checked_at = timezone.now()
    fields: list[dict[str, Any]] = []
    total_future = 0

    limit = max(1, min(int(sample_limit or SAMPLE_LIMIT), 100))

    for model, table, field in _CHECKS:
        future_q = Q(**{f"{field}__gt": reference}) & Q(**{f"{field}__isnull": False})
        agg = model.objects.aggregate(
            max_date=Max(field),
            future_count=Count("id", filter=future_q),
        )
        future_count = int(agg["future_count"] or 0)
        total_future += future_count
        max_date = agg["max_date"]
        status = "attention" if future_count > 0 else "ok"
        sample_qs = (
            model.objects.filter(future_q)
            .order_by(f"-{field}", "-id")
            .only(*_SAMPLE_FIELDS)[:limit]
        )
        fields.append(
            {
                "table": table,
                "field": field,
                "max_date": max_date.isoformat() if max_date else None,
                "future_count": future_count,
                "status": status,
                "sample": [_serialize_sample(row) for row in sample_qs],
            }
        )

    overall = "attention" if total_future > 0 else "ok"
    return {
        "ok": True,
        "status": overall,
        "checked_at": checked_at.isoformat(),
        "reference_date": reference.isoformat(),
        "sample_limit": limit,
        "total_future": total_future,
        "fields": fields,
        "note": (
            "Diagnóstico somente leitura. Não modifica dados, não bloqueia "
            "a Métrica oficial nem importações."
        ),
    }
