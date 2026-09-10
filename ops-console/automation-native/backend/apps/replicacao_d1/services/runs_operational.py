# -*- coding: utf-8 -*-
from __future__ import annotations

from apps.replicacao_d1.models import ReplicacaoD1Run, ReplicacaoD1WorkflowDia
from apps.replicacao_d1.normalization import (
    RESULTADO_CANCELADO,
    RESULTADO_FALHOU,
    RESULTADO_INATIVO,
    RESULTADO_NAO_SALVO,
    RESULTADO_PROCESSANDO,
    RESULTADO_PULADO,
    RESULTADO_SALVO,
    RESULTADO_SEM_ALTERACAO,
    STATUS_FALHOU,
)

_OK_STATUSES = frozenset({"SALVO_OK", "UPLOAD_OK"})
_FAIL_STATUSES = frozenset({"ERRO", "FALHOU", "FALHA", "TIMEOUT", "NAO_SALVO"})
_SKIP_STATUSES = frozenset({"INATIVO", "PULADO", "CANCELADO"})


def _iso_datetime(value) -> str | None:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _status_bucket(
    status: str,
    *,
    resultado: str = "",
    status_operacional: str = "",
) -> str:
    upper = (status or "").strip().upper()
    if status_operacional == STATUS_FALHOU:
        return "falhou"
    if resultado == RESULTADO_SEM_ALTERACAO or upper == "SEM_ALTERACAO":
        return "sem_alteracao"
    if resultado == RESULTADO_SALVO or upper in _OK_STATUSES:
        return "salvo"
    if resultado in (RESULTADO_NAO_SALVO, RESULTADO_FALHOU) or upper in _FAIL_STATUSES:
        return "falhou"
    if resultado in (RESULTADO_PULADO, RESULTADO_INATIVO, RESULTADO_CANCELADO) or upper in _SKIP_STATUSES:
        return "pulado"
    if resultado == RESULTADO_PROCESSANDO or upper == "PROCESSANDO":
        return "pendente"
    return "pendente"


def build_run_operational_summaries(runs: list[ReplicacaoD1Run]) -> dict[str, dict[str, int]]:
    """Agrega contagens por run_id a partir de ReplicacaoD1WorkflowDia."""
    if not runs:
        return {}
    run_ids = [r.run_id for r in runs]
    counts: dict[str, dict[str, int]] = {
        run_id: {
            "total_executavel": 0,
            "salvo": 0,
            "sem_alteracao": 0,
            "pulado": 0,
            "falhou": 0,
            "pendente": 0,
            "avisos_total": 0,
            # Aliases mantidos para consumidores atuais.
            "salvo_ok": 0,
            "erro": 0,
        }
        for run_id in run_ids
    }
    for row in ReplicacaoD1WorkflowDia.objects.filter(
        run_id__in=run_ids,
        protocolos_planejados__gt=0,
    ).values("run_id", "status_brflow", "resultado", "status_operacional"):
        bucket = _status_bucket(
            str(row.get("status_brflow") or ""),
            resultado=str(row.get("resultado") or ""),
            status_operacional=str(row.get("status_operacional") or ""),
        )
        rid = str(row["run_id"])
        if rid not in counts:
            continue
        counts[rid]["total_executavel"] += 1
        counts[rid][bucket] += 1
    for summary in counts.values():
        summary["salvo_ok"] = summary["salvo"] + summary["sem_alteracao"]
        summary["erro"] = summary["falhou"]
        summary["avisos_total"] = (
            summary["sem_alteracao"]
            + summary["pulado"]
            + summary["falhou"]
            + summary["pendente"]
        )
    return counts


def serialize_run_operational(run: ReplicacaoD1Run, summary: dict[str, int] | None = None) -> dict:
    summary = summary or {}
    salvo = summary.get("salvo_ok")
    if salvo is None:
        salvo = int(run.workflows_salvo_ok or 0)
    return {
        "run_id": run.run_id,
        "has_plan": bool(run.plan_hash),
        "validation_status": run.validation_status,
        "status_canonical": run.status_canonical,
        "data_referencia_d1": _iso_datetime(run.data_referencia_d1),
        "data_execucao": _iso_datetime(run.data_execucao),
        "created_at": _iso_datetime(run.created_at),
        "referencia_dados": run.parquet_referencia or "",
        "protocolos_total": run.protocolos_total,
        "workflows_total": int(summary.get("total_executavel") or 0),
        "salvo_ok": salvo,
        "salvo": int(summary.get("salvo") or 0),
        "sem_alteracao": int(summary.get("sem_alteracao") or 0),
        "pulado": int(summary.get("pulado") or 0),
        "falhou": int(summary.get("falhou") or 0),
        "avisos_total": int(summary.get("avisos_total") or 0),
        "pendente": int(summary.get("pendente") or 0),
        "erro": int(summary.get("erro") or 0),
        "synced_at": _iso_datetime(run.synced_at),
    }
