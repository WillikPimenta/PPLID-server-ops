from django.urls import path

from apps.produtividade.views.analytics import (
    AgentDetailView,
    DashboardView,
    EvolucaoView,
    FilterOptionsView,
    KPIsView,
    PorAgenteView,
    PorEquipeView,
    PorHoraView,
    RegistrosView,
    SupervisaoView,
)
from apps.produtividade.views.exports import ExportXlsxView
from apps.produtividade.views.status import StatusView
from apps.produtividade.views.sync import SyncView

urlpatterns = [
    path("status/", StatusView.as_view(), name="produtividade-status"),
    path("filters/", FilterOptionsView.as_view(), name="produtividade-filters"),
    path("kpis/", KPIsView.as_view(), name="produtividade-kpis"),
    path("dashboard/", DashboardView.as_view(), name="produtividade-dashboard"),
    path("supervisao/", SupervisaoView.as_view(), name="produtividade-supervisao"),
    path("evolucao/", EvolucaoView.as_view(), name="produtividade-evolucao"),
    path("por-agente/", PorAgenteView.as_view(), name="produtividade-por-agente"),
    path("por-equipe/", PorEquipeView.as_view(), name="produtividade-por-equipe"),
    path("por-hora/", PorHoraView.as_view(), name="produtividade-por-hora"),
    path("registros/", RegistrosView.as_view(), name="produtividade-registros"),
    path("agente/<str:matricula>/", AgentDetailView.as_view(), name="produtividade-agente"),
    path("export.xlsx", ExportXlsxView.as_view(), name="produtividade-export"),
    path("sync/", SyncView.as_view(), name="produtividade-sync"),
]
