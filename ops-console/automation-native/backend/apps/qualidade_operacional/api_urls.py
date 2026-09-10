# -*- coding: utf-8 -*-
from django.urls import path

from apps.qualidade_operacional.monthly_import_views import (
    MonthlyImportConfirmView,
    MonthlyImportDetailView,
    MonthlyImportListView,
    MonthlyImportRestoreView,
    QualidadeImportCancelView,
    RetroactiveImportChunkView,
    RetroactiveImportCompleteView,
    RetroactiveImportInitView,
)
from apps.qualidade_operacional.views import (
    AgenteAcaoDetailView,
    AgenteAcoesView,
    AgenteDetalheView,
    AuditadosExportView,
    AuditadosView,
    BreakdownView,
    ClientesMelhoriasView,
    DashboardView,
    DetailColumnValuesView,
    FalhasView,
    FalhasExportView,
    FiltrosView,
    InsightsView,
    KpisView,
    MetaView,
    RankingView,
    SerieView,
    SyncView,
    IntranetSyncView,
)

urlpatterns = [
    path("meta/", MetaView.as_view(), name="qualidade-operacional-meta"),
    path("filtros/", FiltrosView.as_view(), name="qualidade-operacional-filtros"),
    path("dashboard/", DashboardView.as_view(), name="qualidade-operacional-dashboard"),
    path(
        "clientes-melhorias/",
        ClientesMelhoriasView.as_view(),
        name="qualidade-operacional-clientes-melhorias",
    ),
    path("kpis/", KpisView.as_view(), name="qualidade-operacional-kpis"),
    path("serie/", SerieView.as_view(), name="qualidade-operacional-serie"),
    path("breakdown/", BreakdownView.as_view(), name="qualidade-operacional-breakdown"),
    path("ranking/", RankingView.as_view(), name="qualidade-operacional-ranking"),
    path("insights/", InsightsView.as_view(), name="qualidade-operacional-insights"),
    path("auditados/", AuditadosView.as_view(), name="qualidade-operacional-auditados"),
    path("falhas/", FalhasView.as_view(), name="qualidade-operacional-falhas"),
    path(
        "detalhe/colunas/",
        DetailColumnValuesView.as_view(),
        name="qualidade-operacional-detalhe-colunas",
    ),
    path(
        "export/auditados.csv",
        AuditadosExportView.as_view(),
        name="qualidade-operacional-export-auditados",
    ),
    path(
        "export/falhas.csv",
        FalhasExportView.as_view(),
        name="qualidade-operacional-export-falhas",
    ),
    path("agente-detalhe/", AgenteDetalheView.as_view(), name="qualidade-operacional-agente-detalhe"),
    path("agente-acoes/", AgenteAcoesView.as_view(), name="qualidade-operacional-agente-acoes"),
    path(
        "agente-acoes/<int:pk>/",
        AgenteAcaoDetailView.as_view(),
        name="qualidade-operacional-agente-acao-detail",
    ),
    path("imports/", MonthlyImportListView.as_view(), name="qualidade-operacional-imports"),
    path(
        "imports/retroactive/init/",
        RetroactiveImportInitView.as_view(),
        name="qualidade-operacional-import-retro-init",
    ),
    path(
        "imports/<uuid:batch_id>/",
        MonthlyImportDetailView.as_view(),
        name="qualidade-operacional-import-detail",
    ),
    path(
        "imports/<uuid:batch_id>/chunks/",
        RetroactiveImportChunkView.as_view(),
        name="qualidade-operacional-import-chunk",
    ),
    path(
        "imports/<uuid:batch_id>/complete-upload/",
        RetroactiveImportCompleteView.as_view(),
        name="qualidade-operacional-import-complete-upload",
    ),
    path(
        "imports/<uuid:batch_id>/cancel/",
        QualidadeImportCancelView.as_view(),
        name="qualidade-operacional-import-cancel",
    ),
    path(
        "imports/<uuid:batch_id>/confirm/",
        MonthlyImportConfirmView.as_view(),
        name="qualidade-operacional-import-confirm",
    ),
    path(
        "imports/<uuid:batch_id>/restore/",
        MonthlyImportRestoreView.as_view(),
        name="qualidade-operacional-import-restore",
    ),
    path("sync/", SyncView.as_view(), name="qualidade-operacional-sync"),
    path(
        "intranet-sync/",
        IntranetSyncView.as_view(),
        name="qualidade-operacional-intranet-sync",
    ),
]
