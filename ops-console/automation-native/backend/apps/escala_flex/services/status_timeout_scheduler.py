"""Agendador em background do timeout de status (dentro do processo Django)."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time

from django.conf import settings
from django.db import connection

logger = logging.getLogger(__name__)

_started = False
_start_lock = threading.Lock()

_SKIP_COMMANDS = frozenset(
    {
        "migrate",
        "makemigrations",
        "test",
        "shell",
        "check",
        "collectstatic",
        "check_status_timeout",
        "cancel_expired_operational_occurrences",
        "rebuild_schedule_today",
        "sync_sharepoint",
        "sync_produtividade",
    }
)


def should_start_status_timeout_scheduler() -> bool:
    if not getattr(settings, "STATUS_TIMEOUT_ENABLED", False):
        return False

    if "pytest" in sys.modules or any(
        "pytest" in str(arg).casefold() for arg in sys.argv
    ):
        return False

    if len(sys.argv) > 1 and sys.argv[1] in _SKIP_COMMANDS:
        return False

    if "runserver" in sys.argv:
        return os.environ.get("RUN_MAIN") == "true"

    return True


def _timeout_worker(interval_seconds: int) -> None:
    while True:
        try:
            from .status_timeout import run_status_timeouts
            from .occurrence_auto_cancel import run_occurrence_auto_cancels

            result = run_status_timeouts()
            if result.get("closed"):
                logger.info(
                    "Status timeout: %s agente(s) encerrados (%s)",
                    result["closed"],
                    ", ".join(result["agents"]),
                )

            cancel_result = run_occurrence_auto_cancels()
            if cancel_result.get("cancelled"):
                logger.info(
                    "Ocorrências expiradas: %s cancelada(s) automaticamente.",
                    cancel_result["cancelled"],
                )
        except Exception:
            logger.exception("Falha ao executar status timeout")
        finally:
            connection.close()

        time.sleep(interval_seconds)


def start_status_timeout_scheduler() -> None:
    global _started

    with _start_lock:
        if _started or not should_start_status_timeout_scheduler():
            return

        interval_minutes = getattr(settings, "STATUS_TIMEOUT_INTERVAL_MINUTES", 5)
        interval_seconds = max(60, interval_minutes * 60)

        thread = threading.Thread(
            target=_timeout_worker,
            args=(interval_seconds,),
            name="ef-status-timeout",
            daemon=True,
        )
        thread.start()
        _started = True
        logger.info(
            "Status timeout scheduler iniciado (intervalo: %s min).",
            interval_minutes,
        )
