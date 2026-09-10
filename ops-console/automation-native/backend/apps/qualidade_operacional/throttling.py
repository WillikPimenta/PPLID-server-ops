# -*- coding: utf-8 -*-
from rest_framework.throttling import UserRateThrottle


class QualidadeOperacionalListThrottle(UserRateThrottle):
    scope = "qualidade_operacional_list"


class QualidadeOperacionalExportThrottle(UserRateThrottle):
    scope = "qualidade_operacional_export"

    def get_rate(self):
        from django.conf import settings

        return getattr(
            settings, "QUALIDADE_OPERACIONAL_EXPORT_THROTTLE", "10/min"
        )
