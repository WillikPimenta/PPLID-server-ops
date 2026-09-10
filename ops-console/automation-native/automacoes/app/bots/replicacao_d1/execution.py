# -*- coding: utf-8 -*-
"""Helpers de consolidação de resultado da execução D-1."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.bots.replicacao_d1.domain import (
    ReplicationRunResult,
    RunStatus,
    RunWorkflowResult,
    WorkflowStatus,
    consolidate_run_status,
    map_brflow_status,
)
from app.bots.replicacao_d1.manifest import (
    MANIFEST_SCHEMA_VERSION,
    build_manifest_payload,
    emit_manifest_event,
    write_manifest_atomic,
)


MOTIVOS_PULADO_BLOQUEANTE = frozenset(
    {
        "CSV_AUSENTE",
        "REGRA_AUSENTE",
        "WORKFLOW_AUSENTE",
        "WORKFLOW_AUSENTE_BRFLOW",
        "REGRA_DIVERGENTE",
        "SALVAMENTO_NAO_CONFIRMADO",
    }
)


def workflow_result_from_entry(
    workflow: str,
    entry: dict[str, Any],
    *,
    fila: str = "",
    protocolos_planejados: int = 0,
) -> RunWorkflowResult:
    raw_status = str(entry.get("status", "") or "")
    canonical = map_brflow_status(raw_status)
    motivo_codigo = str(entry.get("motivo_codigo", "") or "").strip().upper()
    if canonical == WorkflowStatus.SKIPPED and motivo_codigo in MOTIVOS_PULADO_BLOQUEANTE:
        canonical = WorkflowStatus.FAILED
    started_raw = entry.get("started_at") or entry.get("atualizado_em")
    finished_raw = entry.get("finished_at") or entry.get("atualizado_em")
    started_at = _parse_dt(started_raw)
    finished_at = _parse_dt(finished_raw)
    error_summary = str(entry.get("motivo", "") or "")
    error_code = motivo_codigo
    if canonical == WorkflowStatus.FAILED:
        error_code = motivo_codigo or raw_status or "ERRO"
    return RunWorkflowResult(
        workflow=workflow,
        workflow_brflow=str(entry.get("workflow_brflow", "") or ""),
        fila=fila or str(entry.get("fila", "") or ""),
        status=canonical,
        status_brflow_raw=raw_status,
        protocolos_planejados=protocolos_planejados,
        protocolos_enviados=(
            protocolos_planejados
            if canonical == WorkflowStatus.UPLOADED and raw_status.strip().upper() != "SEM_ALTERACAO"
            else 0
        ),
        arquivo=str(entry.get("csv", "") or ""),
        started_at=started_at,
        finished_at=finished_at,
        error_code=error_code,
        error_summary=error_summary[:255],
    )


def collect_workflow_results(plano: Any, estado: dict | None) -> list[RunWorkflowResult]:
    """Agrega resultados canônicos a partir do estado JSON por workflow."""
    workflows = list(getattr(plano, "workflows", None) or [])
    protocolos_map = getattr(plano, "protocolos_por_workflow", None) or {}
    qtd_map = getattr(plano, "qtd_por_workflow", None) or {}
    modo_map = getattr(plano, "workflow_modo_replicacao", None) or {}
    entries = (estado or {}).get("workflows", {})
    results: list[RunWorkflowResult] = []
    for workflow in workflows:
        entry = entries.get(workflow, {})
        modo = str(entry.get("modo") or modo_map.get(workflow) or "").strip().casefold()
        protocolos_planejados = (
            int(entry.get("quantidade_alvo") or entry.get("qtd_calculada") or qtd_map.get(workflow) or 0)
            if modo == "qtd"
            else len(protocolos_map.get(workflow, []))
        )
        if not entry:
            results.append(
                RunWorkflowResult(
                    workflow=workflow,
                    status=WorkflowStatus.PENDING,
                    protocolos_planejados=protocolos_planejados,
                )
            )
            continue
        results.append(
            workflow_result_from_entry(
                workflow,
                entry,
                protocolos_planejados=protocolos_planejados,
            )
        )
    return results


def build_replication_run_result(
    run_id: str,
    workflow_results: list[RunWorkflowResult],
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    planning_error: str = "",
) -> ReplicationRunResult:
    success = sum(1 for r in workflow_results if r.status == WorkflowStatus.UPLOADED)
    failed = sum(1 for r in workflow_results if r.status == WorkflowStatus.FAILED)
    skipped = sum(
        1
        for r in workflow_results
        if r.status in (WorkflowStatus.SKIPPED, WorkflowStatus.INACTIVE, WorkflowStatus.CANCELLED)
    )
    partial = sum(
        1
        for r in workflow_results
        if r.status not in (
            WorkflowStatus.UPLOADED,
            WorkflowStatus.FAILED,
            WorkflowStatus.SKIPPED,
            WorkflowStatus.INACTIVE,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.PENDING,
        )
    )
    result = ReplicationRunResult(
        run_id=run_id,
        workflows_total=len(workflow_results),
        workflows_success=success,
        workflows_partial=partial,
        workflows_failed=failed,
        workflows_skipped=skipped,
        started_at=started_at,
        finished_at=finished_at,
        results=workflow_results,
        planning_error=planning_error,
    )
    result.status = consolidate_run_status(result)
    return result


def write_run_manifest(plano: Any, result: ReplicationRunResult, settings: dict | None = None) -> Path | None:
    """Grava manifesto JSON versionado e emite evento stdout."""
    run_id = str(getattr(plano, "run_id", "") or result.run_id or "").strip()
    if not run_id:
        return None
    if bool((settings or {}).get("fonte_banco_ativa")):
        from app.bots.replicacao_d1_db_bridge import append_execution_event_db

        append_execution_event_db(
            run_id,
            phase="manifest",
            status=result.status.value,
            payload={
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "result": result.to_dict(),
                "config_version": (settings or {}).get("config_version"),
                "config_hash": (settings or {}).get("config_hash"),
            },
        )
        return None
    base = _manifest_dir(plano)
    if base is None:
        return None
    manifest_path = base / "manifest.json"
    snapshot_meta = {}
    if settings:
        snapshot_meta = {
            "config_version": settings.get("config_version"),
            "config_hash": settings.get("config_hash"),
        }
    counts = {
        "workflows_total": result.workflows_total,
        "workflows_success": result.workflows_success,
        "workflows_failed": result.workflows_failed,
        "workflows_skipped": result.workflows_skipped,
    }
    payload = build_manifest_payload(
        run_id=run_id,
        run_result=result.to_dict(),
        snapshot=snapshot_meta,
        counts=counts,
    )
    write_manifest_atomic(manifest_path, payload)
    emit_manifest_event(run_id, manifest_path)
    return manifest_path


def run_status_allows_retention(status: RunStatus) -> bool:
    """Retenção automática só após conclusão plena."""
    return status == RunStatus.COMPLETED


def run_status_allows_consumo(status: RunStatus) -> bool:
    """Consumo mensal apenas quando houve upload aceito."""
    return status in (RunStatus.COMPLETED, RunStatus.PARTIAL)


def _manifest_dir(plano: Any) -> Path | None:
    estado_path = getattr(plano, "estado_execucao_path", None)
    if estado_path:
        return Path(estado_path).parent
    pasta = getattr(plano, "pasta_protocolos", None)
    if pasta:
        return Path(pasta)
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
