from django.urls import path

from apps.auditoria.views_contestacao_operacional import (
    ContestacaoInternaDecidirView,
    ContestacaoInternaDevolverView,
    ContestacaoInternaDetailView,
    ContestacaoInternaFilaView,
    ContestacaoInternaIniciarView,
    ContestacaoInternaRevisarView,
    ContestacaoInternaRevisoesView,
    ContestacaoOperacaoAgentesView,
    ContestacaoOperacaoCriarView,
    ContestacaoOperacaoFalhasFacetasView,
    ContestacaoOperacaoFalhasView,
    ContestacaoOperacaoMinhasView,
)

urlpatterns = [
    path("falhas/", ContestacaoOperacaoFalhasView.as_view(), name="contestacao-operacao-falhas"),
    path(
        "falhas/facetas/",
        ContestacaoOperacaoFalhasFacetasView.as_view(),
        name="contestacao-operacao-falhas-facetas",
    ),
    path("agentes/", ContestacaoOperacaoAgentesView.as_view(), name="contestacao-operacao-agentes"),
    path("criar/", ContestacaoOperacaoCriarView.as_view(), name="contestacao-operacao-criar"),
    path("minhas/", ContestacaoOperacaoMinhasView.as_view(), name="contestacao-operacao-minhas"),
    path(
        "interna/<str:dominio>/fila/",
        ContestacaoInternaFilaView.as_view(),
        name="contestacao-interna-fila",
    ),
    path(
        "interna/<str:dominio>/revisoes/",
        ContestacaoInternaRevisoesView.as_view(),
        name="contestacao-interna-revisoes",
    ),
    path(
        "interna/<int:pk>/",
        ContestacaoInternaDetailView.as_view(),
        name="contestacao-interna-detail",
    ),
    path(
        "interna/<int:pk>/iniciar/",
        ContestacaoInternaIniciarView.as_view(),
        name="contestacao-interna-iniciar",
    ),
    path(
        "interna/<int:pk>/devolver/",
        ContestacaoInternaDevolverView.as_view(),
        name="contestacao-interna-devolver",
    ),
    path(
        "interna/<int:pk>/decidir/",
        ContestacaoInternaDecidirView.as_view(),
        name="contestacao-interna-decidir",
    ),
    path(
        "interna/<int:pk>/revisar/",
        ContestacaoInternaRevisarView.as_view(),
        name="contestacao-interna-revisar",
    ),
]
