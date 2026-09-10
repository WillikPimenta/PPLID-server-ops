# -*- coding: utf-8 -*-
from django.conf import settings
from django.http import HttpResponseRedirect
from django.views import View

from apps.falhas_criticas.auth import PPLIDSessionRequiredMixin


class PortalView(PPLIDSessionRequiredMixin, View):
    """Redireciona o portal legado para o SPA Vue."""

    def get(self, request, *args, **kwargs):
        base = settings.PPLID_FRONTEND_URL.rstrip("/")
        module = request.GET.get("module", "")
        path = f"{base}/secao/indicadores/falhas"
        if module:
            path = f"{path}/{module}"
        return HttpResponseRedirect(path)
