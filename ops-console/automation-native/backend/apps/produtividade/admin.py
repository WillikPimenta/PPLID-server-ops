from django.contrib import admin

from apps.produtividade.models import ProductivityRecord, ProductivitySyncLog


@admin.register(ProductivityRecord)
class ProductivityRecordAdmin(admin.ModelAdmin):
    list_display = (
        "recorded_at",
        "matricula_norm",
        "agent_name",
        "etapa",
        "analysis_seconds",
        "stage_goal",
        "team",
    )
    list_filter = ("etapa", "team", "location", "journey_shift")
    search_fields = ("matricula_norm", "agent_name", "etapa")
    date_hierarchy = "recorded_at"


@admin.register(ProductivitySyncLog)
class ProductivitySyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "trigger_source",
        "success",
        "row_count",
        "duration_seconds",
        "source_file",
    )
    list_filter = ("success", "trigger_source")
    readonly_fields = (
        "started_at",
        "finished_at",
        "duration_seconds",
        "trigger_source",
        "user",
        "success",
        "message",
        "source_file",
        "source_mtime",
        "source_size",
        "row_count",
    )
