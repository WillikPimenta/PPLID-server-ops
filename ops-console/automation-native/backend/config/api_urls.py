from django.urls import include, path
from rest_framework.routers import DefaultRouter

from config.health import health_check, health_deep, health_live, health_ready
from apps.accounts.auth_views import (
    change_password_view,
    csrf_token_view,
    login_view,
    logout_view,
    me_view,
)
from apps.accounts.portal_user_views import PortalUserViewSet
from apps.access.views import (
    PortalMenuOptionViewSet,
    PortalRoleDefinitionViewSet,
    PortalRoutePolicyViewSet,
)
from apps.accounts.views import UserViewSet
from apps.communication.views import NewsViewSet, PortalFeedbackViewSet
from apps.communication.ciencia_views import (
    CienciaLeituraCommunicationsView,
    CienciaLeituraDashboardView,
    CienciaLeituraDetailView,
    CienciaLeituraFilterOptionsView,
    CienciaLeituraRecipientsView,
)
from apps.workforce.views import (
    AgentHistoryViewSet, AgentViewSet, UserProfileViewSet,
    dashboard_overview, dashboard_hiring_trends, dashboard_team_distribution
)

router = DefaultRouter()
router.register("portal-users", PortalUserViewSet, basename="portal-user")
router.register("rbac/route-policies", PortalRoutePolicyViewSet, basename="route-policy")
router.register("rbac/menu-options", PortalMenuOptionViewSet, basename="menu-option")
router.register("rbac/role-definitions", PortalRoleDefinitionViewSet, basename="role-definition")
router.register("users", UserViewSet, basename="user")
router.register("agents", AgentViewSet, basename="agent")
router.register("user-profiles", UserProfileViewSet, basename="user-profile")
router.register("agent-history", AgentHistoryViewSet, basename="agent-history")
router.register("news", NewsViewSet, basename="news")
router.register("feedbacks-onboarding", PortalFeedbackViewSet, basename="portal-feedback")

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("health/live/", health_live, name="health-live"),
    path("health/ready/", health_ready, name="health-ready"),
    path("health/deep/", health_deep, name="health-deep"),
    path("ops-metrics/", include("apps.ops_monitoring.urls")),
    path("", include(router.urls)),
    path("auth/csrf/", csrf_token_view, name="auth-csrf"),
    path("auth/me/", me_view, name="auth-me"),
    path("auth/login/", login_view, name="auth-login"),
    path("auth/change-password/", change_password_view, name="auth-change-password"),
    path("auth/logout/", logout_view, name="auth-logout"),
    path("access/", include("apps.access.api_urls")),
    # Dashboard endpoints
    path("dashboard/overview/", dashboard_overview, name="dashboard-overview"),
    path("dashboard/hiring-trends/", dashboard_hiring_trends, name="dashboard-hiring-trends"),
    path("dashboard/team-distribution/", dashboard_team_distribution, name="dashboard-team-distribution"),
    path("falhas/", include("apps.falhas_criticas.api_urls")),
    path("produtividade/", include("apps.produtividade.api_urls")),
    path("case-manager/", include("apps.produtividade_case.api_urls")),
    path("monitoramento-sla/", include("apps.monitoramento_sla.api_urls")),
    path("monitor-eventos/", include("apps.monitor_eventos.api_urls")),
    path("replicacao-d1/", include("apps.replicacao_d1.api_urls")),
    path("escala-flex/", include("apps.escala_flex.urls")),
    path("automacoes/", include("apps.automacoes.api_urls")),
    path("portal-ops/", include("apps.psa.api_urls")),
    path("psa/", include("apps.psa.api_urls")),
    path("cyber-psa/", include("apps.cyber_psa.api_urls")),
    path("suporte-claro/", include("apps.suporte_claro.api_urls")),
    path("brb-report/", include("apps.brb_report.api_urls")),
    path("operational-support/", include("apps.suporte_operacional.api_urls")),
    path("notifications/", include("apps.portal_notifications.api_urls")),
    path("qualidade/auditoria/", include("apps.auditoria.api_urls")),
    path("qualidade/contestacao/", include("apps.auditoria.contestacao_api_urls")),
    path(
        "qualidade/contestacao-operacional/",
        include("apps.auditoria.contestacao_operacional_api_urls"),
    ),
    path("qualidade/operacional/", include("apps.qualidade_operacional.api_urls")),
    # Contrato dedicado da tela administrativa (mantém as rotas EO legadas intactas).
    path("qualidade-operacional/", include("apps.qualidade_operacional.admin_api_urls")),
    path("dimensoes-processos/", include("apps.dimensoes_processos.api_urls")),
    path("controle-sla/", include("apps.controle_sla.api_urls")),
    path("planejamento-demandas/", include("apps.planejamento_demandas.api_urls")),
    path("workforce/", include("apps.workforce.api_urls")),
    path(
        "news/ciencia-leitura/filter-options/",
        CienciaLeituraFilterOptionsView.as_view(),
        name="news-ciencia-filter-options",
    ),
    path(
        "news/ciencia-leitura/dashboard/",
        CienciaLeituraDashboardView.as_view(),
        name="news-ciencia-dashboard",
    ),
    path(
        "news/ciencia-leitura/communications/",
        CienciaLeituraCommunicationsView.as_view(),
        name="news-ciencia-communications",
    ),
    path(
        "news/ciencia-leitura/communications/<uuid:news_id>/",
        CienciaLeituraDetailView.as_view(),
        name="news-ciencia-detail",
    ),
    path(
        "news/ciencia-leitura/communications/<uuid:news_id>/recipients/",
        CienciaLeituraRecipientsView.as_view(),
        name="news-ciencia-recipients",
    ),
]
