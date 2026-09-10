# -*- coding: utf-8 -*-
from django.http import HttpResponse
from rest_framework import status
from rest_framework.response import Response

from apps.produtividade.services.export_service import export_records_xlsx
from apps.produtividade.services.produtividade_serve import (
    busy_response_detail,
    require_date_range,
    resolve_gated_payload,
)
from apps.produtividade.throttling import ProdutividadeHeavyThrottle
from apps.produtividade.views.base import ProdutividadeAPIView


class ExportXlsxView(ProdutividadeAPIView):
    """Export pesado: datas obrigatórias, range máx., gate; sem cache LocMem do binário."""

    throttle_classes = [ProdutividadeHeavyThrottle]

    def get(self, request):
        qs, params = self.filtered_queryset(request)
        missing = require_date_range(params)
        if missing:
            return Response({"detail": missing}, status=status.HTTP_400_BAD_REQUEST)

        content, err, _ = resolve_gated_payload(
            route="export-xlsx",
            params=params,
            builder=lambda: export_records_xlsx(qs),
            use_cache=False,
        )
        if err:
            body, http_status, headers = busy_response_detail(err)
            return Response(body, status=http_status, headers=headers)

        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="produtividade_export.xlsx"'
        return response
