# -*- coding: utf-8 -*-
"""Throttle DRF das rotas pesadas de produtividade."""
from django.conf import settings
from rest_framework.throttling import UserRateThrottle


class ProdutividadeHeavyThrottle(UserRateThrottle):
    scope = "produtividade_heavy"

    def get_rate(self):
        return getattr(settings, "PRODUTIVIDADE_THROTTLE", "30/min")
