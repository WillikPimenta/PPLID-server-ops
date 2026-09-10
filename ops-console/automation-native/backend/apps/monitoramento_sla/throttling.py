# -*- coding: utf-8 -*-
from rest_framework.throttling import UserRateThrottle


class MonitoramentoSlaResumoThrottle(UserRateThrottle):
    scope = "monitoramento_sla_resumo"


class MonitoramentoSlaImportThrottle(UserRateThrottle):
    scope = "monitoramento_sla_import"
