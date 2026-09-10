from django.urls import include, path
from rest_framework.routers import DefaultRouter

from apps.planejamento_demandas.views import (
    JiraDemandaViewSet,
    sync_demandas_view,
    sync_cancel_view,
    sync_history_view,
    sync_retry_view,
    sync_run_detail_view,
)

router = DefaultRouter()
router.register("jira", JiraDemandaViewSet, basename="planejamento-demandas-jira")

urlpatterns = [
    path("jira/sync/", sync_demandas_view, name="planejamento-demandas-jira-sync"),
    path("jira/sync/<int:run_id>/cancel/", sync_cancel_view, name="planejamento-demandas-jira-sync-cancel"),
    path("jira/sync/<int:run_id>/retry/", sync_retry_view, name="planejamento-demandas-jira-sync-retry"),
    path(
        "jira/sync/<int:run_id>/",
        sync_run_detail_view,
        name="planejamento-demandas-jira-sync-detail",
    ),
    path("jira/sync/historico/", sync_history_view, name="planejamento-demandas-jira-sync-historico"),
    path("", include(router.urls)),
]
