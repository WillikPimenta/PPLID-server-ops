# -*- coding: utf-8 -*-
from rest_framework.views import APIView

from apps.falhas_criticas.permissions import HasDataScope, IsAuthenticatedPortal
from apps.falhas_criticas.scoping import scoped_query_params
from apps.falhas_criticas.services.dataframes import load_period_frames


class ScopedAPIView(APIView):
    permission_classes = [IsAuthenticatedPortal, HasDataScope]

    def scoped_params(self, request):
        return scoped_query_params(request.user, request.query_params)

    def load_frames(self, request):
        return load_period_frames(request.user, request.query_params)
