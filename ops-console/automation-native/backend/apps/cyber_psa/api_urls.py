from django.urls import path

from apps.cyber_psa.views import (
    CyberPsaAccessView,
    CyberPsaDocDownloadView,
    CyberPsaDocViewView,
    CyberPsaExportPackageView,
    CyberPsaExportView,
    CyberPsaHistoryDetailView,
    CyberPsaHistoryListView,
    CyberPsaOverviewView,
    CyberPsaRiskPatchView,
    CyberPsaRunScanView,
)

urlpatterns = [
    path("access/", CyberPsaAccessView.as_view(), name="cyber-psa-access"),
    path("overview/", CyberPsaOverviewView.as_view(), name="cyber-psa-overview"),
    path("run-scan/", CyberPsaRunScanView.as_view(), name="cyber-psa-run-scan"),
    path("risks/<str:risk_id>/", CyberPsaRiskPatchView.as_view(), name="cyber-psa-risk-patch"),
    path("history/", CyberPsaHistoryListView.as_view(), name="cyber-psa-history"),
    path("history/<str:scan_id>/", CyberPsaHistoryDetailView.as_view(), name="cyber-psa-history-detail"),
    path("docs/download/", CyberPsaDocDownloadView.as_view(), name="cyber-psa-doc-download"),
    path("docs/view/", CyberPsaDocViewView.as_view(), name="cyber-psa-doc-view"),
    path("export/", CyberPsaExportView.as_view(), name="cyber-psa-export"),
    path("export-package/", CyberPsaExportPackageView.as_view(), name="cyber-psa-export-package"),
]
