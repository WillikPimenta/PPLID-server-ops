from django.urls import path

from apps.qualidade_operacional.admin_records_views import (
    QualidadeAdminRegistroActionView,
    QualidadeAdminRegistroDetailView,
    QualidadeAdminRegistroListView,
    QualidadeAdminRegistroPreviewView,
)

urlpatterns = [
    path("admin/registros/", QualidadeAdminRegistroListView.as_view()),
    path("admin/registros/<str:kind>/<int:pk>/", QualidadeAdminRegistroDetailView.as_view()),
    path("admin/registros/<str:kind>/<int:pk>/preview/", QualidadeAdminRegistroPreviewView.as_view()),
    path("admin/registros/<str:kind>/<int:pk>/actions/", QualidadeAdminRegistroActionView.as_view()),
]
