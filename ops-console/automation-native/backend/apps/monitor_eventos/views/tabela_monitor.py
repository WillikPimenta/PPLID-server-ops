# -*- coding: utf-8 -*-
from rest_framework import status
from rest_framework.response import Response

from apps.monitor_eventos.services.sync_status import get_sync_status
from apps.monitor_eventos.services.tabela_monitor_serve import (
    require_date_filter,
    resolve_tabela_monitor_payload,
)
from apps.monitor_eventos.throttling import TabelaMonitorThrottle
from apps.monitor_eventos.views.base import MonitorEventosAPIView


class TabelaMonitorView(MonitorEventosAPIView):
    throttle_classes = [TabelaMonitorThrottle]

    def get(self, request):
        qs, params = self.filtered_queryset(request)
        missing = require_date_filter(params)
        if missing:
            return Response({"detail": missing}, status=status.HTTP_400_BAD_REQUEST)

        payload, err, _code = resolve_tabela_monitor_payload(qs, params)
        if err == "queue_timeout":
            return Response(
                {
                    "detail": "Servidor ocupado processando a tabela-monitor. Tente novamente em instantes.",
                    "retry_after": 3,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "3"},
            )
        if err == "build_timeout":
            return Response(
                {
                    "detail": "Timeout aguardando a tabela-monitor. Tente novamente em instantes.",
                    "retry_after": 5,
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
                headers={"Retry-After": "5"},
            )
        assert payload is not None
        return Response(payload)


class StatusView(MonitorEventosAPIView):
    def get(self, request):
        return Response(get_sync_status())
