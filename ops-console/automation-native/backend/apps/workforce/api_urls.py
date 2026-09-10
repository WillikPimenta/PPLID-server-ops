from django.urls import path

from apps.workforce.catalog_views import (
    HeadcountCatalogConfigDetailView,
    HeadcountCatalogConfigListView,
    HeadcountCatalogConfigMetaView,
    HeadcountCatalogOptionsView,
)
from apps.workforce.import_views import (
    AgentHistoryImportConfirmView,
    AgentHistoryImportPreflightView,
    AgentHistoryImportRestoreView,
)
from apps.workforce.agent_active_views import (
    AgentActiveAlignView,
    AgentActiveConsistencyView,
)

urlpatterns = [
    path(
        "headcount-catalog/options/",
        HeadcountCatalogOptionsView.as_view(),
        name="headcount-catalog-options",
    ),
    path(
        "headcount-catalog/meta/",
        HeadcountCatalogConfigMetaView.as_view(),
        name="headcount-catalog-meta",
    ),
    path(
        "headcount-catalog/<str:catalog>/",
        HeadcountCatalogConfigListView.as_view(),
        name="headcount-catalog-list",
    ),
    path(
        "headcount-catalog/<str:catalog>/<int:pk>/",
        HeadcountCatalogConfigDetailView.as_view(),
        name="headcount-catalog-detail",
    ),
    path(
        "agent-history/import/preflight/",
        AgentHistoryImportPreflightView.as_view(),
        name="agent-history-import-preflight",
    ),
    path(
        "agent-history/import/confirm/",
        AgentHistoryImportConfirmView.as_view(),
        name="agent-history-import-confirm",
    ),
    path(
        "agent-history/import/restore/",
        AgentHistoryImportRestoreView.as_view(),
        name="agent-history-import-restore",
    ),
    path(
        "agent-active/consistency/",
        AgentActiveConsistencyView.as_view(),
        name="agent-active-consistency",
    ),
    path(
        "agent-active/align/",
        AgentActiveAlignView.as_view(),
        name="agent-active-align",
    ),
]
