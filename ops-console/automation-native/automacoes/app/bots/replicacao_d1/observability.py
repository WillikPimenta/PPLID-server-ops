# -*- coding: utf-8 -*-
"""Logs estruturados da execução D-1 (sem payload sensível)."""
from __future__ import annotations

import logging
from typing import Any


def format_log_context(
    *,
    run_id: str = "",
    workflow: str = "",
    ingestion_id: str | int = "",
    artifact_id: str | int = "",
    job_id: str | int = "",
    phase: str = "",
    **extra: Any,
) -> str:
    parts: list[str] = []
    if run_id:
        parts.append(f"run_id={run_id}")
    if workflow:
        parts.append(f"workflow={workflow}")
    if ingestion_id not in ("", None):
        parts.append(f"ingestion={ingestion_id}")
    if artifact_id not in ("", None):
        parts.append(f"artifact={artifact_id}")
    if job_id not in ("", None):
        parts.append(f"job={job_id}")
    if phase:
        parts.append(f"phase={phase}")
    for key, value in extra.items():
        if value not in ("", None):
            parts.append(f"{key}={value}")
    return " | ".join(parts)


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    run_id: str = "",
    workflow: str = "",
    ingestion_id: str | int = "",
    artifact_id: str | int = "",
    job_id: str | int = "",
    phase: str = "",
    **extra: Any,
) -> None:
    ctx = format_log_context(
        run_id=run_id,
        workflow=workflow,
        ingestion_id=ingestion_id,
        artifact_id=artifact_id,
        job_id=job_id,
        phase=phase,
        **extra,
    )
    text = f"{message} | {ctx}" if ctx else message
    logger.log(level, text)
