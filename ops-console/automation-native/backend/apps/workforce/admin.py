from django.contrib import admin

from .models import Agent, AgentHistory, UserProfile


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = (
        "full_name",
        "user_lan_id",
        "active",
        "hire_date",
    )
    list_filter = ("active",)
    search_fields = ("full_name", "user_lan_id", "email")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "agent", "created_at")
    search_fields = ("user__username", "agent__full_name")
    readonly_fields = ("id", "created_at")


@admin.register(AgentHistory)
class AgentHistoryAdmin(admin.ModelAdmin):
    list_display = (
        "agent",
        "team",
        "job_title",
        "job_activity",
        "leader",
        "facilitator",
        "start_date",
        "final_date",
        "active",
    )
    list_filter = ("active", "team", "location")
    search_fields = ("agent__full_name", "team", "job_title")
    readonly_fields = ("id", "created_at")
    raw_id_fields = ("agent", "leader", "facilitator")
