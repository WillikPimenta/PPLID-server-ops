# -*- coding: utf-8 -*-
from pathlib import Path

import pandas as pd
from django.http import FileResponse, HttpResponse
from report_falhas.io.data_loader import norm_matricula
from report_falhas.reincidence import reincidencia_table_full
from rest_framework import status
from rest_framework.response import Response

from config.api_exceptions import secure_error_payload

from apps.falhas_criticas.models import FalhasAgent, Contestation, ExecutiveReportArchive, Failure, Support, Training
from apps.falhas_criticas.permissions import HasDataScope, IsAuthenticatedPortal, IsGlobalOnly
from apps.falhas_criticas.scoping import scoped_query_params
from apps.falhas_criticas.services.compare_analytics import build_comparativo_bsb_sc
from apps.falhas_criticas.services.executive_bridge import save_executive_report
from apps.falhas_criticas.services.export_service import export_failures_csv, export_reincidencia_xlsx
from apps.falhas_criticas.services.narrative import pre_diagnostico_block
from apps.falhas_criticas.views.base import ScopedAPIView


class ExecutiveDownloadView(ScopedAPIView):
    permission_classes = [IsAuthenticatedPortal, HasDataScope, IsGlobalOnly]

    def get(self, request):
        params, _ = scoped_query_params(request.user, request.query_params)
        try:
            archive, out_path = save_executive_report(request.user, params)
        except Exception as exc:
            return Response(
                secure_error_payload(
                    exc,
                    operation="falhas_criticas.executive_download",
                    message_field="error",
                ),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response = FileResponse(
            open(out_path, 'rb'),
            content_type='text/html; charset=utf-8',
            as_attachment=True,
            filename=out_path.name,
        )
        response['X-Archive-Id'] = str(archive.id)
        return response


class ExecutiveHistoryView(ScopedAPIView):
    permission_classes = [IsAuthenticatedPortal, HasDataScope, IsGlobalOnly]

    def get(self, request):
        qs = ExecutiveReportArchive.objects.all()[:8]
        items = [{
            'id': a.id,
            'period_start': a.period_start,
            'period_end': a.period_end,
            'generated_at': a.generated_at,
            'generated_by': a.generated_by.username if a.generated_by else None,
        } for a in qs]
        return Response({'items': items})


class ExecutiveHistoryDownloadView(ScopedAPIView):
    permission_classes = [IsAuthenticatedPortal, HasDataScope, IsGlobalOnly]

    def get(self, request, archive_id):
        from django.http import Http404

        try:
            archive = ExecutiveReportArchive.objects.get(pk=archive_id)
        except ExecutiveReportArchive.DoesNotExist:
            raise Http404
        path = Path(archive.file_path)
        if not path.is_file():
            raise Http404
        return FileResponse(
            open(path, 'rb'),
            content_type='text/html; charset=utf-8',
            as_attachment=True,
            filename=path.name,
        )


class ExportFailuresCsvView(ScopedAPIView):
    def get(self, request):
        data = export_failures_csv(request.user, request.query_params)
        response = HttpResponse(data, content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = 'attachment; filename="falhas_export.csv"'
        return response


class ExportReincidenciaXlsxView(ScopedAPIView):
    def get(self, request):
        data = export_reincidencia_xlsx(request.user, request.query_params)
        response = HttpResponse(
            data,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        response['Content-Disposition'] = 'attachment; filename="reincidencia_export.xlsx"'
        return response
