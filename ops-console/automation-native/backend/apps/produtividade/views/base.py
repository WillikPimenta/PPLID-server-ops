# -*- coding: utf-8 -*-
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.produtividade.permissions import CanViewProductivity


class ProdutividadeAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewProductivity]

    def effective_user(self, request):
        """Usuário efetivo (impersonado) para escopo e filtros — alinhado ao RBAC portal."""
        from apps.access.services.impersonation import resolve_effective_user

        return resolve_effective_user(request)

    def filter_params(self, request):
        from apps.produtividade.scoping import constrain_filter_params
        from apps.produtividade.services.analytics import parse_query_params

        params = parse_query_params(request.query_params)
        return constrain_filter_params(self.effective_user(request), params)

    def filtered_queryset(self, request):
        from apps.produtividade.models import ProductivityRecord
        from apps.produtividade.scoping import apply_produtividade_scope
        from apps.produtividade.services.analytics import apply_record_filters

        params = self.filter_params(request)
        user = self.effective_user(request)
        qs = ProductivityRecord.objects.all()
        qs = apply_produtividade_scope(qs, user)
        return apply_record_filters(qs, params), params
