import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class EscalaFlexConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.escala_flex"
    verbose_name = "Monitoramento"

    def ready(self) -> None:
        try:
            from .services.status_types_bootstrap import ensure_status_types

            ensure_status_types()
        except Exception:
            logger.exception("Não foi possível garantir tipos de status do Monitoramento")

        try:
            from .services.status_timeout_scheduler import start_status_timeout_scheduler

            start_status_timeout_scheduler()
        except Exception:
            logger.exception("Não foi possível iniciar o scheduler de status timeout")

        try:
            from .services.daily_status_reset_scheduler import (
                start_daily_status_reset_scheduler,
            )

            start_daily_status_reset_scheduler()
        except Exception:
            logger.exception("Não foi possível iniciar o scheduler de reset diário de status")
