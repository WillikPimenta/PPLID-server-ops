# -*- coding: utf-8 -*-
from django.urls import path

from apps.monitoramento_sla.views import (
    ConsolidadoView,
    DetalheView,
    ExportView,
    ImportParquetApplyView,
    ImportParquetPreviewView,
    ImportParquetRunDetailView,
    ImportParquetRunListView,
    KpisView,
    ResumoView,
    StatusView,
    SyncView,
)

urlpatterns = [
    path("status/", StatusView.as_view(), name="monitoramento-sla-status"),
    path("sync/", SyncView.as_view(), name="monitoramento-sla-sync"),
    path("kpis/", KpisView.as_view(), name="monitoramento-sla-kpis"),
    path("resumo/", ResumoView.as_view(), name="monitoramento-sla-resumo"),
    path("detalhe/", DetalheView.as_view(), name="monitoramento-sla-detalhe"),
    path("consolidado/", ConsolidadoView.as_view(), name="monitoramento-sla-consolidado"),
    path("export/", ExportView.as_view(), name="monitoramento-sla-export"),
    path(
        "import-parquet/preview/",
        ImportParquetPreviewView.as_view(),
        name="monitoramento-sla-import-parquet-preview",
    ),
    path(
        "import-parquet/apply/",
        ImportParquetApplyView.as_view(),
        name="monitoramento-sla-import-parquet-apply",
    ),
    path(
        "import-parquet/runs/",
        ImportParquetRunListView.as_view(),
        name="monitoramento-sla-import-parquet-runs",
    ),
    path(
        "import-parquet/runs/<int:run_id>/",
        ImportParquetRunDetailView.as_view(),
        name="monitoramento-sla-import-parquet-run-detail",
    ),
]
