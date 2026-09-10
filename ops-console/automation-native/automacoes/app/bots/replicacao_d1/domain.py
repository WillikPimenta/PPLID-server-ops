# -*- coding: utf-8 -*-
"""DTOs e vocabulário canônico da execução D-1."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    UPLOADED = "uploaded"
    SKIPPED = "skipped"
    INACTIVE = "inactive"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class WorkflowActionResult:
    """Resultado estruturado de uma tentativa no painel BRFlow."""

    status: str
    resultado: str
    motivo_codigo: str = ""
    motivo_resumo: str = ""
    fase_execucao: str = ""
    quantidade_alvo: int | None = None
    quantidade_encontrada: int | None = None


@dataclass
class RunWorkflowResult:
    workflow: str
    workflow_brflow: str = ""
    fila: str = ""
    status: WorkflowStatus = WorkflowStatus.PENDING
    status_brflow_raw: str = ""
    protocolos_planejados: int = 0
    protocolos_enviados: int = 0
    arquivo: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error_code: str = ""
    error_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        if self.started_at:
            data["started_at"] = self.started_at.isoformat()
        if self.finished_at:
            data["finished_at"] = self.finished_at.isoformat()
        return data


@dataclass
class ReplicationRunResult:
    run_id: str
    status: RunStatus = RunStatus.PLANNED
    workflows_total: int = 0
    workflows_success: int = 0
    workflows_partial: int = 0
    workflows_failed: int = 0
    workflows_skipped: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    results: list[RunWorkflowResult] = field(default_factory=list)
    planning_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "workflows_total": self.workflows_total,
            "workflows_success": self.workflows_success,
            "workflows_partial": self.workflows_partial,
            "workflows_failed": self.workflows_failed,
            "workflows_skipped": self.workflows_skipped,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "planning_error": self.planning_error,
            "results": [r.to_dict() for r in self.results],
        }


def consolidate_run_status(result: ReplicationRunResult) -> RunStatus:
    """Deriva status do run a partir dos workflows."""
    if result.planning_error:
        return RunStatus.FAILED
    if not result.results:
        return RunStatus.FAILED
    ok = result.workflows_success
    failed = result.workflows_failed
    skipped = result.workflows_skipped
    cancelled = sum(1 for item in result.results if item.status == WorkflowStatus.CANCELLED)
    benign_skipped = sum(
        1 for item in result.results if item.status in (WorkflowStatus.SKIPPED, WorkflowStatus.INACTIVE)
    )
    total_done = ok + failed + result.workflows_partial
    if failed and ok:
        return RunStatus.PARTIAL
    if failed and not ok:
        return RunStatus.FAILED
    if cancelled and not failed:
        return RunStatus.CANCELLED
    if ok and not failed:
        return RunStatus.COMPLETED if total_done + skipped >= result.workflows_total else RunStatus.PARTIAL
    if benign_skipped >= result.workflows_total:
        return RunStatus.COMPLETED
    # Sem falha explícita, workflows ainda pendentes representam fechamento
    # incompleto/sincronização pendente, não uma falha operacional comprovada.
    return RunStatus.PARTIAL


def map_brflow_status(raw: str) -> WorkflowStatus:
    upper = (raw or "").strip().upper()
    if upper in ("SALVO_OK", "UPLOAD_OK", "SEM_ALTERACAO"):
        return WorkflowStatus.UPLOADED
    if upper in ("ERRO", "NAO_SALVO", "FALHOU", "FALHA", "TIMEOUT"):
        return WorkflowStatus.FAILED
    if upper in ("PULADO", "SKIP"):
        return WorkflowStatus.SKIPPED
    if upper in ("INATIVO",):
        return WorkflowStatus.INACTIVE
    if upper in ("CANCELADO",):
        return WorkflowStatus.CANCELLED
    if upper in ("PROCESSANDO",):
        return WorkflowStatus.RUNNING
    if upper in ("PENDENTE", ""):
        return WorkflowStatus.PENDING
    return WorkflowStatus.PENDING
