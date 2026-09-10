"""API de acompanhamento de ciência de leitura (liderança)."""

from __future__ import annotations

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from apps.communication.models import News
from apps.communication.services import ciencia_leitura as svc
from rest_framework.permissions import IsAuthenticated


class CienciaLeituraPermissionMixin:
    permission_classes = [IsAuthenticated, portal_perm(R.OPERACAO_CIENCIA_LEITURA_VIEW)]


class CienciaLeituraFilterOptionsView(CienciaLeituraPermissionMixin, APIView):
    def get(self, request):
        return Response(svc.filter_options())


class CienciaLeituraDashboardView(CienciaLeituraPermissionMixin, APIView):
    def get(self, request):
        return Response(svc.build_dashboard(request.query_params.dict()))


class CienciaLeituraCommunicationsView(CienciaLeituraPermissionMixin, APIView):
    def get(self, request):
        params = request.query_params.dict()
        if "pageSize" not in params and "page_size" in params:
            params["pageSize"] = params["page_size"]
        return Response(svc.list_communications(params))


class CienciaLeituraDetailView(CienciaLeituraPermissionMixin, APIView):
    def get(self, request, news_id):
        try:
            return Response(svc.get_detail(str(news_id)))
        except News.DoesNotExist:
            return Response({"detail": "Comunicado não encontrado."}, status=status.HTTP_404_NOT_FOUND)


class CienciaLeituraRecipientsView(CienciaLeituraPermissionMixin, APIView):
    def get(self, request, news_id):
        params = request.query_params.dict()
        params["communicationId"] = str(news_id)
        if "pageSize" not in params and "page_size" in params:
            params["pageSize"] = params["page_size"]
        try:
            return Response(svc.list_recipients(params))
        except News.DoesNotExist:
            return Response({"detail": "Comunicado não encontrado."}, status=status.HTTP_404_NOT_FOUND)
