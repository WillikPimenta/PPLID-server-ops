# -*- coding: utf-8 -*-
"""Fases estáveis de log do planejamento D-1."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

from app.bots.replicacao_d1.observability import format_log_context

PLANNING_START = "planning_start"
PLANNING_SOURCE = "planning_source"
PLANNING_CONFIG = "planning_config"
PLANNING_RETRO = "planning_retro"
PLANNING_ALLOCATE = "planning_allocate"
PLANNING_PERSIST = "planning_persist"
PLANNING_DONE = "planning_done"
PLANNING_ERROR = "planning_error"

PLAN_EVENT_PREFIX = "PLAN_EVENT|"
PLAN_EVENT_VERSION = 1

PHASE_LABELS: dict[str, str] = {
    PLANNING_START: "Iniciando planejamento",
    PLANNING_SOURCE: "Carregando fonte D-1",
    PLANNING_CONFIG: "Carregando configuração",
    PLANNING_RETRO: "Aplicando retroativo",
    PLANNING_ALLOCATE: "Alocando protocolos",
    PLANNING_PERSIST: "Persistindo plano",
    PLANNING_DONE: "Planejamento concluído",
    PLANNING_ERROR: "Falha no planejamento",
}

# Marcos compatíveis com o progresso legado do bot (planejamento ocupa 0-100
# quando executado isoladamente e 0-15 antes da etapa BRFlow no fluxo completo).
PHASE_PROGRESS: dict[str, int] = {
    PLANNING_START: 5,
    PLANNING_SOURCE: 20,
    PLANNING_CONFIG: 30,
    PLANNING_RETRO: 40,
    PLANNING_ALLOCATE: 40,
    PLANNING_PERSIST: 97,
    PLANNING_DONE: 100,
}

_event_callback: Callable[[dict[str, Any]], None] | None = None
_event_lock = threading.Lock()
_last_event_at: dict[tuple[int, str], float] = {}
_last_progress: dict[tuple[int, str], int] = {}


def set_plan_event_callback(callback: Callable[[dict[str, Any]], None] | None) -> None:
    """Registra o consumidor em tempo real sem acoplar o planejador ao runner."""
    global _event_callback
    _event_callback = callback


def _event_state(phase: str, extra: dict[str, Any]) -> str:
    if extra.get("warning") is True:
        return "warning"
    if phase == PLANNING_START:
        return "started"
    if phase == PLANNING_ERROR:
        return "failed"
    if phase == PLANNING_RETRO and extra.get("active") is False:
        return "skipped"
    if phase == PLANNING_ALLOCATE and not (
        isinstance(extra.get("current"), (int, float))
        and isinstance(extra.get("total"), (int, float))
    ):
        return "started"
    if phase == PLANNING_ALLOCATE:
        return "progress"
    return "completed"


def _safe_metrics(extra: dict[str, Any]) -> dict[str, Any]:
    """Mantém apenas contexto escalar e bloqueia chaves potencialmente sensíveis."""
    blocked = ("password", "senha", "token", "secret", "cookie", "session", "credential")
    metrics: dict[str, Any] = {}
    for raw_key, value in extra.items():
        key = str(raw_key or "").strip()[:64]
        if not key or any(fragment in key.lower() for fragment in blocked):
            continue
        if value is None or value == "":
            continue
        if isinstance(value, (bool, int, float)):
            metrics[key] = value
        elif isinstance(value, str):
            metrics[key] = value[:240]
    return metrics


def _build_plan_event(
    *, run_id: str, phase: str, message: str, extra: dict[str, Any]
) -> dict[str, Any]:
    thread_key = (threading.get_ident(), str(run_id or ""))
    now_mono = time.perf_counter()
    with _event_lock:
        previous_at = _last_event_at.get(thread_key)
        elapsed_ms = round(max(0.0, now_mono - previous_at) * 1000) if previous_at else 0
        _last_event_at[thread_key] = now_mono
        progress = PHASE_PROGRESS.get(phase, _last_progress.get(thread_key, 0))
        current = extra.get("current")
        total = extra.get("total")
        if (
            phase == PLANNING_ALLOCATE
            and isinstance(current, (int, float))
            and isinstance(total, (int, float))
            and total > 0
        ):
            progress = 40 + round(45 * min(max(float(current) / float(total), 0.0), 1.0))
        progress = max(_last_progress.get(thread_key, 0), progress)
        _last_progress[thread_key] = progress
        if phase in (PLANNING_DONE, PLANNING_ERROR):
            _last_event_at.pop(thread_key, None)
            _last_progress.pop(thread_key, None)

    metrics = _safe_metrics(extra)
    current = metrics.get("current")
    total = metrics.get("total")
    event: dict[str, Any] = {
        "version": PLAN_EVENT_VERSION,
        "event_id": uuid4().hex,
        "occurred_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "run_id": str(run_id or "")[:64],
        "phase": str(phase or "")[:32],
        "phase_label": PHASE_LABELS.get(phase, phase or "Planejamento"),
        "state": _event_state(phase, extra),
        "message": str(message or "")[:500],
        "progress_pct": progress,
        "elapsed_ms": elapsed_ms,
        "metrics": metrics,
    }
    if isinstance(current, (int, float)):
        event["current"] = int(current)
    if isinstance(total, (int, float)):
        event["total"] = int(total)
    return event


def truncate_hash(value: Any, length: int = 8) -> str:
    text = str(value or "").strip()
    return text[:length] if text else ""


def plan_log(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    run_id: str = "",
    phase: str = "",
    **extra: Any,
) -> None:
    """Emite o log legado e um evento v1 aditivo para status/UI em tempo real."""
    ctx = format_log_context(
        run_id=run_id,
        phase=phase,
        **extra,
    )
    text = f"{message} | {ctx}" if ctx else message
    logger.log(level, text)
    event = _build_plan_event(run_id=run_id, phase=phase, message=message, extra=extra)
    try:
        print(f"PLANLOG|{text}", flush=True)
        print(f"{PLAN_EVENT_PREFIX}{json.dumps(event, ensure_ascii=False, separators=(',', ':'))}", flush=True)
    except Exception:
        pass
    callback = _event_callback
    if callback is not None:
        try:
            callback(dict(event))
        except Exception:
            logger.debug("Falha no callback de evento do planejamento", exc_info=True)
