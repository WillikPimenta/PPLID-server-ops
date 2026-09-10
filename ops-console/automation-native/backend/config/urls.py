from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

handler400 = "config.error_views.bad_request"
handler403 = "config.error_views.permission_denied"
handler404 = "config.error_views.page_not_found"
handler500 = "config.error_views.server_error"

urlpatterns = [
    path("", RedirectView.as_view(url=settings.PPLID_FRONTEND_URL, permanent=False)),
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.api_urls")),
    path("falhas/", include("apps.falhas_criticas.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
