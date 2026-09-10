from django.contrib import admin

from .models import PortalNotification, PortalNotificationRule


@admin.register(PortalNotification)
class PortalNotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "recipient", "kind", "read_at", "created_at", "expires_at")
    list_filter = ("kind", "read_at", "created_at")
    search_fields = ("title", "message", "recipient__username", "source_id")
    readonly_fields = ("created_at",)


@admin.register(PortalNotificationRule)
class PortalNotificationRuleAdmin(admin.ModelAdmin):
    list_display = (
        "event_key",
        "section",
        "functionality",
        "priority",
        "decision",
        "enabled",
        "updated_at",
    )
    list_filter = ("section", "priority", "recommendation", "decision", "enabled")
    search_fields = ("event_key", "functionality", "event_label")
