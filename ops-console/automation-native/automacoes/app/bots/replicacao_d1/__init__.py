# -*- coding: utf-8 -*-
"""Pacote modular D-1 — reexporta DTOs, manifesto e fachadas."""

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
from app.bots.replicacao_d1.settings import (
    get_credentials,
    get_headless,
    normalize_replicacao_settings,
    parse_bool_setting,
)
from app.bots.replicacao_d1.state import resolve_csv_upload_workflow, update_workflow_state

__all__ = [
    "ReplicationRunResult",
    "RunStatus",
    "RunWorkflowResult",
    "WorkflowStatus",
    "consolidate_run_status",
    "map_brflow_status",
    "MANIFEST_SCHEMA_VERSION",
    "build_manifest_payload",
    "emit_manifest_event",
    "write_manifest_atomic",
    "get_credentials",
    "get_headless",
    "normalize_replicacao_settings",
    "parse_bool_setting",
    "resolve_csv_upload_workflow",
    "update_workflow_state",
]