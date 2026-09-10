from django.urls import path

from . import views, views_fila

urlpatterns = [
    path("notices/", views.NoticeListCreateView.as_view(), name="operational-support-notices"),
    path(
        "notices/<uuid:notice_id>/",
        views.NoticeDetailView.as_view(),
        name="operational-support-notice-detail",
    ),
    path("metadata/", views.MetadataView.as_view(), name="operational-support-metadata"),
    path("categories/", views.CategoriesView.as_view(), name="operational-support-categories"),
    path("agents/", views.ActiveAgentsView.as_view(), name="operational-support-agents"),
    path("presence/me/", views_fila.PresenceMeView.as_view(), name="operational-support-presence-me"),
    path("presence/presencial/", views.PresencialAgentsView.as_view(), name="operational-support-presence-presencial"),
    path(
        "capacitacao-support-users/",
        views.CapacitacaoSupportUsersView.as_view(),
        name="operational-support-capacitacao-support-users",
    ),
    path("fila/me/", views_fila.MinhaFilaView.as_view(), name="operational-support-fila-me"),
    path("fila/me/claim/", views_fila.ClaimFilaView.as_view(), name="operational-support-fila-claim"),
    path(
        "fila/me/<uuid:request_id>/assumir/",
        views_fila.AssumirProtocoloView.as_view(),
        name="operational-support-fila-assumir",
    ),
    path(
        "fila/me/<uuid:request_id>/remover/",
        views_fila.RemoverDirecionadoView.as_view(),
        name="operational-support-fila-remover",
    ),
    path("fila/direcionar/", views_fila.DirecionarView.as_view(), name="operational-support-fila-direcionar"),
    path("fila/priorizar/", views_fila.PriorizarView.as_view(), name="operational-support-fila-priorizar"),
    path(
        "fila/controle/",
        views_fila.ControleOperacoesView.as_view(),
        name="operational-support-fila-controle",
    ),
    path("requests/", views.RequestListCreateView.as_view(), name="operational-support-requests"),
    path(
        "requests/<uuid:request_id>/",
        views.RequestDetailView.as_view(),
        name="operational-support-request-detail",
    ),
    path(
        "requests/<uuid:request_id>/leader-decision/",
        views.LeaderDecisionView.as_view(),
        name="operational-support-leader-decision",
    ),
    path(
        "requests/<uuid:request_id>/cancel/",
        views.CancelView.as_view(),
        name="operational-support-cancel",
    ),
    path(
        "requests/<uuid:request_id>/assign/",
        views.AssignView.as_view(),
        name="operational-support-assign",
    ),
    path(
        "requests/<uuid:request_id>/answer/",
        views.AnswerView.as_view(),
        name="operational-support-answer",
    ),
]
