from django.urls import path

from apps.controle_sla import views

urlpatterns = [
    path("connection/", views.connection_status_view, name="controle-sla-connection"),
    path("connect/", views.connect_view, name="controle-sla-connect"),
    path("disconnect/", views.disconnect_view, name="controle-sla-disconnect"),
    path("cancel/", views.cancel_view, name="controle-sla-cancel"),
    path("breaches/", views.breaches_view, name="controle-sla-breaches"),
    path("gaps/", views.gaps_view, name="controle-sla-gaps"),
    path("gaps/<int:pk>/ignorar/", views.gap_ignorar_view, name="controle-sla-gap-ignorar"),
    path("gaps/<int:pk>/cadastrar/", views.gap_cadastrar_view, name="controle-sla-gap-cadastrar"),
    path("summary/", views.summary_view, name="controle-sla-summary"),
    path("historico/", views.historico_view, name="controle-sla-historico"),
    path("export/", views.export_view, name="controle-sla-export"),
]
