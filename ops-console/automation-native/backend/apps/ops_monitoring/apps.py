import logging
import sys
import threading
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)

_PURGE_INTERVAL_SEC = 24 * 3600
_purge_started = False


def _running_under_tests() -> bool:
    """Reconhece tanto ``manage.py test`` quanto o runner pytest-django."""
    return "test" in sys.argv or "pytest" in sys.modules or any(
        "pytest" in str(arg).casefold() for arg in sys.argv
    )


def _purge_loop() -> None:
    while True:
        time.sleep(_PURGE_INTERVAL_SEC)
        try:
            from django.core.management import call_command

            call_command("purge_api_metrics", days=7)
        except Exception:
            logger.exception("Falha ao purgar metricas de API")


class OpsMonitoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ops_monitoring"
    verbose_name = "Ops Monitoring"

    def ready(self) -> None:
        global _purge_started
        if _purge_started:
            return
        if "migrate" in sys.argv or "makemigrations" in sys.argv:
            return
        if _running_under_tests():
            return
        _purge_started = True
        from .middleware import start_metrics_flush_thread

        start_metrics_flush_thread()
        thread = threading.Thread(target=_purge_loop, name="ops-metrics-purge", daemon=True)
        thread.start()
