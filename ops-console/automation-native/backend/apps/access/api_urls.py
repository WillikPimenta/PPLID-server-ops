from django.urls import path

from apps.access.views_impersonation import (
    ImpersonationClearView,
    ImpersonationPreviewView,
    ImpersonationStartView,
    ImpersonationTargetsView,
)

urlpatterns = [
    path("impersonate/targets/", ImpersonationTargetsView.as_view(), name="access-impersonate-targets"),
    path("impersonate/preview/", ImpersonationPreviewView.as_view(), name="access-impersonate-preview"),
    path("impersonate/", ImpersonationStartView.as_view(), name="access-impersonate-start"),
    path("impersonate/clear/", ImpersonationClearView.as_view(), name="access-impersonate-clear"),
]
