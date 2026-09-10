from django.urls import path

from apps.monitor_eventos.views.tabela_monitor import StatusView, TabelaMonitorView

urlpatterns = [
    path("status/", StatusView.as_view(), name="monitor-eventos-status"),
    path("tabela-monitor/", TabelaMonitorView.as_view(), name="monitor-eventos-tabela-monitor"),
]
