"""Agendador em background do reset diário de status (05:30 horário local)."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import date

from django.conf import settings
from django.db import connection
from django.utils import timezone

logger = logging.getLogger(__name__)

_started = False
_start_lock = threading.Lock()
_last_reset_date: date | None = None

_SKIP_COMMANDS = frozenset(
    {
        "migrate",
        "makemigrations",
        "test",
        "shell",
        "check",
        "collectstatic",
        "reset_daily_status",
        "rebuild_schedule_today",
        "sync_sharepoint",
    }
)


def should_start_daily_status_reset_scheduler() -> bool:
    if not getattr(settings, "DAILY_STATUS_RESET_ENABLED", True):
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


def maybe_run_daily_status_reset() -> dict | None:
    global _last_reset_date

    now = timezone.localtime()
    today = now.date()
    reset_hour = getattr(settings, "DAILY_STATUS_RESET_HOUR", 5)
    reset_minute = getattr(settings, "DAILY_STATUS_RESET_MINUTE", 30)

    if (now.hour, now.minute) != (reset_hour, reset_minute):
        return None

    if _last_reset_date == today:
        return None

    from .daily_status_reset import reset_daily_status

    result = reset_daily_status(today)
    _last_reset_date = today
    logger.info(
        "Reset diário de status (%s): %s evento(s) removido(s), %s escala(s) limpa(s).",
        today,
        result["events_deleted"],
        result["schedule_today_cleared"],
    )
    return result


def _reset_worker(check_interval_seconds: int) -> None:
    while True:
        try:
            maybe_run_daily_status_reset()
        except Exception:
            logger.exception("Falha ao executar reset diário de status")
        finally:
            connection.close()

        time.sleep(check_interval_seconds)


def start_daily_status_reset_scheduler() -> None:
    global _started

    with _start_lock:
        if _started or not should_start_daily_status_reset_scheduler():
            return

        thread = threading.Thread(
            target=_reset_worker,
            args=(60,),
            name="ef-daily-status-reset",
            daemon=True,
        )
        thread.start()
        _started = True
        reset_hour = getattr(settings, "DAILY_STATUS_RESET_HOUR", 5)
        reset_minute = getattr(settings, "DAILY_STATUS_RESET_MINUTE", 30)
        logger.info(
            "Reset diário de status agendado para %02d:%02d (horário local).",
            reset_hour,
            reset_minute,
        )
