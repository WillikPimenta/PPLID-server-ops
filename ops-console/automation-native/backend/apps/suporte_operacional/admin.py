from django.contrib import admin

from .models import (
    OperationalSupportAgentPresence,
    OperationalSupportEvent,
    OperationalSupportNotice,
    OperationalSupportRequest,
)


class OperationalSupportEventInline(admin.TabularInline):
    model = OperationalSupportEvent
    extra = 0
    fields = (
        "sequence",
        "event_type",
        "actor_name",
        "actor_username",
        "from_status",
        "to_status",
        "note",
        "created_at",
    )
    readonly_fields = (
        "sequence",
        "event_type",
        "actor_name",
        "actor_username",
        "from_status",
        "to_status",
        "note",
        "created_at",
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(OperationalSupportRequest)
class OperationalSupportRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "subject",
        "request_type",
        "status",
        "agent",
        "requester",
        "created_at",
    )
    list_filter = ("request_type", "status", "category", "requester_type")
    search_fields = ("subject", "description", "reference", "agent__user_lan_id", "agent__full_name")
    inlines = [OperationalSupportEventInline]

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OperationalSupportAgentPresence)
class OperationalSupportAgentPresenceAdmin(admin.ModelAdmin):
    list_display = ("user", "status", "status_changed_at", "last_assigned_at")
    list_filter = ("status",)
    search_fields = ("user__username", "user__first_name", "user__last_name")


@admin.register(OperationalSupportNotice)
class OperationalSupportNoticeAdmin(admin.ModelAdmin):
    list_display = ("title", "created_by", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("title", "message", "created_by__username")
    readonly_fields = ("created_at", "updated_at")
