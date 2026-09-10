from django.urls import path

from apps.falhas_criticas.views import HealthView, PortalView

urlpatterns = [
    path("health/", HealthView.as_view(), name="falhas-health"),
    path("", PortalView.as_view(), name="falhas-portal"),
]
