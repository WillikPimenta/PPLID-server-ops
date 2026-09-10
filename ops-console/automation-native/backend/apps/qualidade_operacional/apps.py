# -*- coding: utf-8 -*-
from django.apps import AppConfig


class QualidadeOperacionalConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.qualidade_operacional"
    label = "qualidade_operacional"
    verbose_name = "Qualidade Operacional"

    def ready(self) -> None:
        from apps.qualidade_operacional import signals

        signals.connect_auditoria_signals()
        del signals
