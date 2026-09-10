"""Indicador enxuto de falhas retiradas atribuídas ao auditor."""
from __future__ import annotations

from django.db.models import Count, F, Q
from django.db.models.functions import Lower, Trim

from apps.qualidade_operacional.models import QualidadeAuditado
from apps.qualidade_operacional.services.queries import (
    date_field_auditados,
    param_list,
)

WITHDRAWN_STATUS = "retirada"
UNIDENTIFIED_AUDITOR_KEY = "__nao_identificado__"
UNIDENTIFIED_AUDITOR_LABEL = "Auditor não identificado"


def _parse_date(raw):
    from datetime import date

    try:
        return date.fromisoformat(str(raw)) if raw else None
    except (TypeError, ValueError):
        return None


def _int_values(params, key: str) -> list[int]:
    values: list[int] = []
    for raw in param_list(params, key):
        try:
            values.append(int(raw))
        except (TypeError, ValueError):
            continue
    return values


def _withdrawn_audits(params):
    """População analítica; deliberadamente sem JOIN com contestação/auditoria."""
    date_field = date_field_auditados(params)
    qs = QualidadeAuditado.objects.filter(status=WITHDRAWN_STATUS)

    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    if start:
        qs = qs.filter(**{f"{date_field}__gte": start})
    if end:
        qs = qs.filter(**{f"{date_field}__lte": end})

    for field in ("id_cliente", "id_workflow"):
        values = _int_values(params, field)
        if values:
            qs = qs.filter(**{f"{field}__in": values})

    for field in ("tipo_analise", "etapa", "localidade_documento"):
        param = field if field != "localidade_documento" else "localidade"
        values = param_list(params, param)
        if values:
            qs = qs.filter(**{f"{field}__in": values})

    agents = param_list(params, "matricula")
    if agents:
        agent_filter = Q()
        for agent in agents:
            agent_filter |= Q(matricula__iexact=str(agent).strip())
        qs = qs.filter(agent_filter)

    auditors = param_list(params, "matricula_auditor")
    if auditors:
        auditor_filter = Q()
        for auditor in auditors:
            auditor_filter |= Q(matricula_auditor__iexact=str(auditor).strip())
        qs = qs.filter(auditor_filter)
    return qs


def build_auditor_dashboard(params) -> dict:
    """Agrupa retiradas por auditor em uma única consulta indexável."""
    auditor_key = Lower(Trim(F("matricula_auditor")))
    grouped = list(
        _withdrawn_audits(params)
        .annotate(auditor_key=auditor_key)
        .values("auditor_key")
        .annotate(erros=Count("id"))
        .order_by("-erros", "auditor_key")
    )

    results = []
    identified_count = 0
    unidentified_errors = 0
    for rank, row in enumerate(grouped, start=1):
        matricula = str(row["auditor_key"] or "").strip()
        identified = bool(matricula)
        errors = int(row["erros"] or 0)
        if identified:
            identified_count += 1
        else:
            unidentified_errors += errors
        results.append(
            {
                "key": matricula or UNIDENTIFIED_AUDITOR_KEY,
                "label": matricula or UNIDENTIFIED_AUDITOR_LABEL,
                "matricula_auditor": matricula,
                "identificado": identified,
                "erros": errors,
                "rank": rank,
            }
        )

    total_errors = sum(row["erros"] for row in results)
    return {
        "ok": True,
        "module": "auditores",
        "kpis": {
            "erros_auditoria": total_errors,
            "auditores_identificados": identified_count,
            "erros_nao_identificados": unidentified_errors,
        },
        "ranking": {
            "count": len(results),
            "results": results,
        },
    }
