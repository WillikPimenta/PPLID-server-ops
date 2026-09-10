"""Bot Falhas Críticas — export Power BI + relatório HTML/Outlook."""
from app.bots.falhas_criticas.orchestration import (
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
