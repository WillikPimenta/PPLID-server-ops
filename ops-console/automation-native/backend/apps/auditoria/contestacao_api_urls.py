from django.urls import path

from apps.auditoria.views_contestacao_dashboard import ContestacaoDashboardView

urlpatterns = [
    path("dashboard/", ContestacaoDashboardView.as_view(), name="contestacao-dashboard"),
]
