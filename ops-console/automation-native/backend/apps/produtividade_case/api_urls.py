# -*- coding: utf-8 -*-
from django.urls import path

from apps.produtividade_case.analitica_views import (
    AnaliticaAgenteDetalheView,
    AnaliticaCruzamentoView,
    AnaliticaMatrizOrigemDestinoView,
    AnaliticaPorAgenteView,
    AnaliticaPorWorkflowView,
    AnaliticaProtocolosView,
    AnaliticaRankingView,
    AnaliticaResumoView,
    AnaliticaSerieDiariaView,
    AnaliticaStatusView,
)
from apps.produtividade_case.views import (
    FilaAmostraView,
    FilaPainelView,
    FilaResumoView,
    FilaSerieView,
    StatusView,
    SyncJobStatusView,
    SyncView,
)

urlpatterns = [
    path("status/", StatusView.as_view(), name="case-manager-status"),
    path("fila/resumo/", FilaResumoView.as_view(), name="case-manager-fila-resumo"),
    path("fila/painel/", FilaPainelView.as_view(), name="case-manager-fila-painel"),
    path("fila/serie/", FilaSerieView.as_view(), name="case-manager-fila-serie"),
    path("fila/amostra/", FilaAmostraView.as_view(), name="case-manager-fila-amostra"),
    path("sync/", SyncView.as_view(), name="case-manager-sync"),
    path(
        "sync-jobs/<int:job_id>/",
        SyncJobStatusView.as_view(),
        name="case-manager-sync-job",
    ),
    path("analitica/status/", AnaliticaStatusView.as_view(), name="case-manager-analitica-status"),
    path("analitica/resumo/", AnaliticaResumoView.as_view(), name="case-manager-analitica-resumo"),
    path(
        "analitica/por-workflow/",
        AnaliticaPorWorkflowView.as_view(),
        name="case-manager-analitica-por-workflow",
    ),
    path(
        "analitica/por-agente/",
        AnaliticaPorAgenteView.as_view(),
        name="case-manager-analitica-por-agente",
    ),
    path(
        "analitica/serie-diaria/",
        AnaliticaSerieDiariaView.as_view(),
        name="case-manager-analitica-serie-diaria",
    ),
    path(
        "analitica/cruzamento/",
        AnaliticaCruzamentoView.as_view(),
        name="case-manager-analitica-cruzamento",
    ),
    path(
        "analitica/matriz-origem-destino/",
        AnaliticaMatrizOrigemDestinoView.as_view(),
        name="case-manager-analitica-matriz",
    ),
    path(
        "analitica/ranking/",
        AnaliticaRankingView.as_view(),
        name="case-manager-analitica-ranking",
    ),
    path(
        "analitica/protocolos/",
        AnaliticaProtocolosView.as_view(),
        name="case-manager-analitica-protocolos",
    ),
    path(
        "analitica/agente/<str:matricula>/",
        AnaliticaAgenteDetalheView.as_view(),
        name="case-manager-analitica-agente",
    ),
]
