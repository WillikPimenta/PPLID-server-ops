from django.urls import path

from apps.psa.views import (
    PortalOpsAccessView,
    PortalOpsDocDownloadView,
    PortalOpsDocViewView,
    PortalOpsOverviewView,
    PortalOpsQualidadeDatesView,
    PortalOpsQualidadeCaseKeyDupesView,
    PortalOpsRunChecksView,
)

urlpatterns = [
    path("access/", PortalOpsAccessView.as_view(), name="portal-ops-access"),
    path("overview/", PortalOpsOverviewView.as_view(), name="portal-ops-overview"),
    path("run-checks/", PortalOpsRunChecksView.as_view(), name="portal-ops-run-checks"),
    path("docs/download/", PortalOpsDocDownloadView.as_view(), name="portal-ops-doc-download"),
    path("docs/view/", PortalOpsDocViewView.as_view(), name="portal-ops-doc-view"),
    path(
        "data-quality/qualidade-dates/",
        PortalOpsQualidadeDatesView.as_view(),
        name="portal-ops-qualidade-dates",
    ),
    path(
        "data-quality/qualidade-case-key-dupes/",
        PortalOpsQualidadeCaseKeyDupesView.as_view(),
        name="portal-ops-qualidade-case-key-dupes",
    ),
]
