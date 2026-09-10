from django.urls import path

from apps.dimensoes_processos.views import (
    DimensaoCatalogViewSet,
    dimensoes_meta_view,
    etapas_proximo_id_view,
    metas_etapa_ciclos_view,
    metas_etapa_etapas_faltantes_view,
    projecao_sla_ciclos_view,
)
from apps.dimensoes_processos.views_derivacao_etapa import (
    DerivacaoEtapaAliasView,
    DerivacaoEtapaAutoResolveView,
    DerivacaoEtapaComparativoListView,
    DerivacaoEtapaComparativoResumoView,
    DerivacaoEtapaDiariaDetailView,
    DerivacaoEtapaDiariaListView,
    DerivacaoEtapaImportStatusView,
    DerivacaoEtapaImportView,
    DerivacaoEtapaPurgeView,
    DerivacaoEtapaPurgeRunDetailView,
    DerivacaoEtapaScanView,
    DerivacaoEtapaUploadDetailView,
    DerivacaoEtapaUploadView,
)
from apps.dimensoes_processos.views_identificacao_processos import (
    IdentificacaoProcessosImportRunDetailView,
    IdentificacaoProcessosImportView,
    IdentificacaoProcessosPreflightView,
    IdentificacaoProcessosUploadDetailView,
    IdentificacaoProcessosUploadView,
)
from apps.dimensoes_processos.views_capacity import (
    CapacityDailyView,
    CapacityDerivacoesExportView,
    CapacityDistributionView,
    CapacityFamiliaAliasView,
    CapacityHourlyDrilldownExportView,
    CapacityPeriodView,
    CapacityScenarioCatalogView,
    CapacitySimulationCompareView,
    CapacityVolumeEsperadoHoraExportView,
)

catalog_list = DimensaoCatalogViewSet.as_view({"get": "list", "post": "create"})
catalog_detail = DimensaoCatalogViewSet.as_view(
    {"get": "retrieve", "put": "update", "patch": "partial_update"}
)

urlpatterns = [
    path("meta/", dimensoes_meta_view, name="dimensoes-processos-meta"),
    path(
        "etapas/proximo-id/",
        etapas_proximo_id_view,
        name="etapas-proximo-id",
    ),
    path(
        "metas-etapa/etapas-faltantes/",
        metas_etapa_etapas_faltantes_view,
        name="metas-etapa-etapas-faltantes",
    ),
    path("metas-etapa/ciclos/", metas_etapa_ciclos_view, name="metas-etapa-ciclos"),
    path("projecao-sla/ciclos/", projecao_sla_ciclos_view, name="projecao-sla-ciclos"),
    path(
        "identificacao-processos/upload/",
        IdentificacaoProcessosUploadView.as_view(),
        name="identificacao-processos-upload",
    ),
    path(
        "identificacao-processos/upload/<uuid:token>/",
        IdentificacaoProcessosUploadDetailView.as_view(),
        name="identificacao-processos-upload-detail",
    ),
    path(
        "identificacao-processos/preflight/",
        IdentificacaoProcessosPreflightView.as_view(),
        name="identificacao-processos-preflight",
    ),
    path(
        "identificacao-processos/import/",
        IdentificacaoProcessosImportView.as_view(),
        name="identificacao-processos-import",
    ),
    path(
        "identificacao-processos/runs/<int:run_id>/",
        IdentificacaoProcessosImportRunDetailView.as_view(),
        name="identificacao-processos-run-detail",
    ),
    path("capacity/diario/", CapacityDailyView.as_view(), name="capacity-diario"),
    path("capacity/export/derivacoes.csv", CapacityDerivacoesExportView.as_view(), name="capacity-export-derivacoes"),
    path("capacity/export/volume-esperado-hora.csv", CapacityVolumeEsperadoHoraExportView.as_view(), name="capacity-export-volume-esperado-hora"),
    path("capacity/export/distribuicao-horaria.csv", CapacityHourlyDrilldownExportView.as_view(), name="capacity-export-distribuicao-horaria"),
    path("capacity/periodo/", CapacityPeriodView.as_view(), name="capacity-periodo"),
    path("capacity/cenarios/", CapacityScenarioCatalogView.as_view(), name="capacity-cenarios"),
    path(
        "capacity/simulacao/compare/",
        CapacitySimulationCompareView.as_view(),
        name="capacity-simulation-compare",
    ),
    path(
        "capacity/distribuicao/",
        CapacityDistributionView.as_view(),
        name="capacity-distribuicao",
    ),
    path(
        "capacity/familia-aliases/",
        CapacityFamiliaAliasView.as_view(),
        name="capacity-familia-aliases",
    ),
    path(
        "derivacao-etapa/comparativo/auto-resolver/",
        DerivacaoEtapaAutoResolveView.as_view(),
        name="derivacao-etapa-auto-resolver",
    ),
    path("derivacao-etapa/scan/", DerivacaoEtapaScanView.as_view(), name="derivacao-etapa-scan"),
    path(
        "derivacao-etapa/comparativo/",
        DerivacaoEtapaComparativoListView.as_view(),
        name="derivacao-etapa-comparativo",
    ),
    path(
        "derivacao-etapa/comparativo/resumo/",
        DerivacaoEtapaComparativoResumoView.as_view(),
        name="derivacao-etapa-comparativo-resumo",
    ),
    path("derivacao-etapa/import/", DerivacaoEtapaImportView.as_view(), name="derivacao-etapa-import"),
    path(
        "derivacao-etapa/import-status/",
        DerivacaoEtapaImportStatusView.as_view(),
        name="derivacao-etapa-import-status",
    ),
    path("derivacao-etapa/purge/", DerivacaoEtapaPurgeView.as_view(), name="derivacao-etapa-purge"),
    path(
        "derivacao-etapa/purge/runs/<int:run_id>/",
        DerivacaoEtapaPurgeRunDetailView.as_view(),
        name="derivacao-etapa-purge-run-detail",
    ),
    path("derivacao-etapa/diaria/", DerivacaoEtapaDiariaListView.as_view(), name="derivacao-etapa-diaria"),
    path(
        "derivacao-etapa/diaria/<int:pk>/",
        DerivacaoEtapaDiariaDetailView.as_view(),
        name="derivacao-etapa-diaria-detail",
    ),
    path("derivacao-etapa/aliases/", DerivacaoEtapaAliasView.as_view(), name="derivacao-etapa-aliases"),
    path("derivacao-etapa/uploads/", DerivacaoEtapaUploadView.as_view(), name="derivacao-etapa-uploads"),
    path(
        "derivacao-etapa/uploads/<uuid:token>/",
        DerivacaoEtapaUploadDetailView.as_view(),
        name="derivacao-etapa-upload-detail",
    ),
    path("<slug:slug>/", catalog_list, name="dimensoes-processos-list"),
    path("<slug:slug>/<str:pk>/", catalog_detail, name="dimensoes-processos-detail"),
]
