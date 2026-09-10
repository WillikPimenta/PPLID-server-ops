"""Facade do modo produtividade_case para robot_runner."""

from app.bots.produtividade_case.bot import (
    executar_pipeline,
    parar_event,
    set_progress_callback,
    set_status_callback,
    start,
    stop,
)

__all__ = [
    "start",
    "stop",
    "executar_pipeline",
    "parar_event",
    "set_status_callback",
    "set_progress_callback",
]
