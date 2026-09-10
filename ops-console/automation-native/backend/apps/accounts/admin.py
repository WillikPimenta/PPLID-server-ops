from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User, UserChangeHistorico


@admin.register(UserChangeHistorico)
class UserChangeHistoricoAdmin(admin.ModelAdmin):
    list_display = ("target_user", "actor", "action", "field_name", "created_at")
    list_filter = ("action",)
    search_fields = ("target_user__username", "actor__username")
    readonly_fields = ("target_user", "actor", "action", "field_name", "old_value", "new_value", "created_at")


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "email", "is_active", "is_staff", "created_at")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("username", "email")
    readonly_fields = ("id", "created_at", "updated_at")
    fieldsets = BaseUserAdmin.fieldsets + (
        (
            "Auditoria",
            {
                "fields": (
                    "id",
                    "created_at",
                    "updated_at",
                    "created_by",
                    "updated_by",
                ),
            },
        ),
    )
    add_fieldsets = BaseUserAdmin.add_fieldsets
