# -*- coding: utf-8 -*-
from rest_framework.response import Response

from apps.produtividade.services.sync_status import get_sync_status
from apps.produtividade.views.base import ProdutividadeAPIView


class StatusView(ProdutividadeAPIView):
    def get(self, request):
        payload = get_sync_status()
        return Response(payload)
