from django.urls import path

from . import views


urlpatterns = [
    path("rules/", views.NotificationRuleListView.as_view(), name="portal-notification-rules"),
    path("rules/sync/", views.NotificationRuleSyncView.as_view(), name="portal-notification-rules-sync"),
    path(
        "rules/<uuid:rule_id>/",
        views.NotificationRuleDetailView.as_view(),
        name="portal-notification-rule-detail",
    ),
    path(
        "rules/<uuid:rule_id>/<str:action>/",
        views.NotificationRuleActionView.as_view(),
        name="portal-notification-rule-action",
    ),
    path("", views.NotificationListView.as_view(), name="portal-notification-list"),
    path("mark-seen/", views.MarkAllSeenView.as_view(), name="portal-notification-mark-seen"),
    path("mark-all-read/", views.MarkAllReadView.as_view(), name="portal-notification-mark-all-read"),
    path("<uuid:notification_id>/read/", views.MarkReadView.as_view(), name="portal-notification-mark-read"),
]
