# -*- coding: utf-8 -*-
"""Throttle DRF específico da tabela-monitor."""
from django.conf import settings
from rest_framework.throttling import UserRateThrottle


class TabelaMonitorThrottle(UserRateThrottle):
    scope = "tabela_monitor"

    def get_rate(self):
        return getattr(settings, "TABELA_MONITOR_THROTTLE", "30/min")
