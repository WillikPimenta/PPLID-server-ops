# -*- coding: utf-8 -*-
from rest_framework.views import APIView

from apps.access import registry as R
from apps.access.permission_classes import portal_perm
from rest_framework.permissions import IsAuthenticated


class ReplicacaoD1APIView(APIView):
    permission_classes = [IsAuthenticated, portal_perm(R.PLANEJAMENTO_AUTOMACAO_VIEW)]


class ReplicacaoD1SyncWriteAPIView(APIView):
    """Mutação operacional (sync/enqueue) — exige permissão de configuração."""

    permission_classes = [IsAuthenticated, portal_perm(R.PLANEJAMENTO_AUTOMACAO_CONFIGURE)]
