# -*- coding: utf-8 -*-
from app.bots.replicacao_d1.domain import (
    ReplicationRunResult,
    RunStatus,
    RunWorkflowResult,
    WorkflowStatus,
    consolidate_run_status,
    map_brflow_status,
)


def test_map_brflow_status_uploaded():
    assert map_brflow_status("SALVO_OK") == WorkflowStatus.UPLOADED
    assert map_brflow_status("SEM_ALTERACAO") == WorkflowStatus.UPLOADED
    assert map_brflow_status("NAO_SALVO") == WorkflowStatus.FAILED
    assert map_brflow_status("CANCELADO") == WorkflowStatus.CANCELLED
    assert map_brflow_status("PROCESSANDO") == WorkflowStatus.RUNNING


def test_consolidate_partial_when_mixed():
    result = ReplicationRunResult(
        run_id="r1",
        workflows_total=2,
        workflows_success=1,
        workflows_failed=1,
        results=[
            RunWorkflowResult("A", status=WorkflowStatus.UPLOADED),
            RunWorkflowResult("B", status=WorkflowStatus.FAILED),
        ],
    )
    assert consolidate_run_status(result) == RunStatus.PARTIAL


def test_consolidate_failed_on_planning_error():
    result = ReplicationRunResult(run_id="r1", planning_error="invalid plan")
    assert consolidate_run_status(result) == RunStatus.FAILED


def test_consolidate_pending_without_explicit_failure_is_partial():
    result = ReplicationRunResult(
        run_id="r1",
        workflows_total=1,
        results=[RunWorkflowResult("A", status=WorkflowStatus.PENDING)],
    )
    assert consolidate_run_status(result) == RunStatus.PARTIAL
