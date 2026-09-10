from django.urls import path

from apps.suporte_claro.jira_views import (
    JiraCancelView,
    JiraConfigView,
    JiraCredentialsView,
    JiraPendentesView,
    JiraPreviewView,
    JiraRunView,
    JiraStatusView,
)
from apps.suporte_claro.views import (
    IncidentesMomReportPdfView,
    RegistroAnexosView,
    RegistroComentarioEtapaDetailView,
    RegistroComentariosEtapaView,
    RegistroDetailView,
    RegistroExportView,
    RegistroFichaPdfView,
    RegistroImportApplyView,
    RegistroImportPreviewView,
    RegistroRachelImportApplyView,
    RegistroRachelImportPreviewView,
    RegistroImportTemplateView,
    RegistroListCreateView,
    RegistroReportPdfView,
    RegistroStatusView,
)

urlpatterns = [
    path("jira/pendentes/", JiraPendentesView.as_view(), name="suporte-claro-jira-pendentes"),
    path("jira/config/", JiraConfigView.as_view(), name="suporte-claro-jira-config"),
    path("jira/credentials/", JiraCredentialsView.as_view(), name="suporte-claro-jira-credentials"),
    path("jira/preview/", JiraPreviewView.as_view(), name="suporte-claro-jira-preview"),
    path("jira/run/", JiraRunView.as_view(), name="suporte-claro-jira-run"),
    path("jira/status/", JiraStatusView.as_view(), name="suporte-claro-jira-status"),
    path("jira/cancel/", JiraCancelView.as_view(), name="suporte-claro-jira-cancel"),
    path("registros/export.xlsx", RegistroExportView.as_view(), name="suporte-claro-registros-export"),
    path(
        "registros/import-template.xlsx",
        RegistroImportTemplateView.as_view(),
        name="suporte-claro-registros-import-template",
    ),
    path(
        "registros/import/preview/",
        RegistroImportPreviewView.as_view(),
        name="suporte-claro-registros-import-preview",
    ),
    path(
        "registros/import/apply/",
        RegistroImportApplyView.as_view(),
        name="suporte-claro-registros-import-apply",
    ),
    path(
        "registros/import-rachel/preview/",
        RegistroRachelImportPreviewView.as_view(),
        name="suporte-claro-registros-import-rachel-preview",
    ),
    path(
        "registros/import-rachel/apply/",
        RegistroRachelImportApplyView.as_view(),
        name="suporte-claro-registros-import-rachel-apply",
    ),
    path("registros/report.pdf", RegistroReportPdfView.as_view(), name="suporte-claro-registros-report-pdf"),
    path(
        "registros/incidentes-report.pdf",
        IncidentesMomReportPdfView.as_view(),
        name="suporte-claro-incidentes-report-pdf",
    ),
    path("registros/", RegistroListCreateView.as_view(), name="suporte-claro-registros"),
    path("registros/<int:registro_id>/ficha.pdf", RegistroFichaPdfView.as_view(), name="suporte-claro-registro-ficha-pdf"),
    path("registros/<int:registro_id>/anexos/", RegistroAnexosView.as_view(), name="suporte-claro-registro-anexos"),
    path(
        "registros/<int:registro_id>/comentarios-etapa/",
        RegistroComentariosEtapaView.as_view(),
        name="suporte-claro-registro-comentarios-etapa",
    ),
    path(
        "registros/<int:registro_id>/comentarios-etapa/<int:comentario_id>/",
        RegistroComentarioEtapaDetailView.as_view(),
        name="suporte-claro-registro-comentario-etapa-detail",
    ),
    path("registros/<int:registro_id>/", RegistroDetailView.as_view(), name="suporte-claro-registro-detail"),
    path("registros/<int:registro_id>/status/", RegistroStatusView.as_view(), name="suporte-claro-registro-status"),
]
