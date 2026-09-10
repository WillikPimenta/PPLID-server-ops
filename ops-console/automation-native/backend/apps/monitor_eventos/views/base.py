# -*- coding: utf-8 -*-
from rest_framework.views import APIView

from apps.monitor_eventos.permissions import IsAuthenticatedPortal


class MonitorEventosAPIView(APIView):
    permission_classes = [IsAuthenticatedPortal]

    def filter_params(self, request):
        from apps.monitor_eventos.services.query_params import parse_query_params

        return parse_query_params(request.query_params)

    def filtered_queryset(self, request):
        from apps.monitor_eventos.models import MonitorEventoRecord
        from apps.monitor_eventos.services.query_params import apply_record_filters

        params = self.filter_params(request)
        qs = MonitorEventoRecord.objects.all()
        return apply_record_filters(qs, params), params
