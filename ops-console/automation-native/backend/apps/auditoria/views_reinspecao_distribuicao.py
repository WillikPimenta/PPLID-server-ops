from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.permissions import HasAnyPortalPermission
from apps.access.registry import (
    QUAL_AUDITORIA_ASSIGN,
    QUAL_AUDITORIA_COMPLIANCE_ASSIGN,
    QUAL_AUDITORIA_COMPLIANCE_VIEW,
    QUAL_AUDITORIA_VIEW,
)
from apps.auditoria.services.reinspecao_ged_divergencias import build_ged_portal_export_rows
from apps.auditoria.services.reinspecao_fila import (
    reset_fila_contexto,
    set_fila_contexto,
)


class ReinspecaoGedPortalExportView(APIView):
    permission_classes = [IsAuthenticated, HasAnyPortalPermission]
    portal_permissions = (
        QUAL_AUDITORIA_VIEW,
        QUAL_AUDITORIA_ASSIGN,
        QUAL_AUDITORIA_COMPLIANCE_VIEW,
        QUAL_AUDITORIA_COMPLIANCE_ASSIGN,
    )

    def get(self, request):
        contexto = (request.GET.get("contexto") or "reinspecao").strip().lower()
        if contexto not in {"reinspecao", "auditoria_compliance"}:
            contexto = "reinspecao"
        token = set_fila_contexto(contexto)
        try:
            rows = build_ged_portal_export_rows(contexto=contexto)
            return Response({"results": rows, "total": len(rows)})
        finally:
            reset_fila_contexto(token)
