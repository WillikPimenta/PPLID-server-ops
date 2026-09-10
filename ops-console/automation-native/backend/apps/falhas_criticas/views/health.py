# -*- coding: utf-8 -*-
from django.http import JsonResponse
from django.views import View

from apps.falhas_criticas.models import Failure, FalhasAgent


class HealthView(View):
    """Endpoint público para validar que o módulo está montado."""

    def get(self, request):
        try:
            import report_falhas
        except ImportError:
            return JsonResponse(
                {
                    "status": "error",
                    "module": "falhas_criticas",
                    "report_falhas": None,
                    "detail": "Serviço temporariamente indisponível.",
                },
                status=503,
            )
        return JsonResponse(
            {
                "status": "ok",
                "module": "falhas_criticas",
                "report_falhas": getattr(report_falhas, "__version__", "imported"),
                "db": {
                    "agents": FalhasAgent.objects.count(),
                    "failures": Failure.objects.count(),
                },
            }
        )
