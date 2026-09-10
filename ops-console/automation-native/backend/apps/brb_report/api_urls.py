# -*- coding: utf-8 -*-
from django.urls import path

from apps.brb_report.views import (
    BrbReportArtifactDownloadView,
    BrbReportClientsView,
    BrbReportCsDashboardView,
    BrbReportCsSourceUploadView,
    BrbReportExecutivePortfolioClientRcaView,
    BrbReportExecutivePortfolioClientView,
    BrbReportExecutivePortfolioSupplementView,
    BrbReportExecutivePortfolioView,
    BrbReportGenerateView,
    BrbReportHistoryView,
    BrbReportPreviewDbView,
    BrbReportPreviewView,
    BrbReportRegenerateView,
)

urlpatterns = [
    path("clients/", BrbReportClientsView.as_view(), name="brb-report-clients"),
    path("cs-dashboard/", BrbReportCsDashboardView.as_view(), name="brb-report-cs-dashboard"),
    path("cs-dashboard/source/", BrbReportCsSourceUploadView.as_view(), name="brb-report-cs-source"),
    path("preview/", BrbReportPreviewView.as_view(), name="brb-report-preview"),
    path("preview-db/", BrbReportPreviewDbView.as_view(), name="brb-report-preview-db"),
    path("generate/", BrbReportGenerateView.as_view(), name="brb-report-generate"),
    path(
        "regenerate/<str:storage_key>/",
        BrbReportRegenerateView.as_view(),
        name="brb-report-regenerate",
    ),
    path("history/", BrbReportHistoryView.as_view(), name="brb-report-history"),
    path(
        "executive-portfolio/",
        BrbReportExecutivePortfolioView.as_view(),
        name="brb-report-executive-portfolio",
    ),
    path(
        "executive-portfolio/supplement/",
        BrbReportExecutivePortfolioSupplementView.as_view(),
        name="brb-report-executive-portfolio-supplement",
    ),
    path(
        "executive-portfolio/client/",
        BrbReportExecutivePortfolioClientView.as_view(),
        name="brb-report-executive-portfolio-client",
    ),
    path(
        "executive-portfolio/client/rca/",
        BrbReportExecutivePortfolioClientRcaView.as_view(),
        name="brb-report-executive-portfolio-client-rca",
    ),
    path(
        "artifacts/<str:storage_key>/<str:filename>/",
        BrbReportArtifactDownloadView.as_view(),
        name="brb-report-artifact",
    ),
]
