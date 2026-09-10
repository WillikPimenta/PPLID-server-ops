from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permissions import HasPortalPermission
from apps.access.registry import QUAL_AUDITORIA_VIEW
from apps.auditoria.services.contestacao_dashboard import build_contestacao_dashboard


class ContestacaoDashboardView(APIView):
    permission_classes = [IsAuthenticated, HasPortalPermission]
    portal_permission = QUAL_AUDITORIA_VIEW

    def get(self, request):
        start_date = (request.query_params.get("start_date") or "").strip() or None
        end_date = (request.query_params.get("end_date") or "").strip() or None
        payload = build_contestacao_dashboard(start_date=start_date, end_date=end_date)
        return Response(payload)
