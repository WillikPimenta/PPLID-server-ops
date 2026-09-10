from django.contrib import admin

from apps.monitor_eventos.models import MonitorEventoRecord, MonitorEventoSyncLog


@admin.register(MonitorEventoRecord)
class MonitorEventoRecordAdmin(admin.ModelAdmin):
    list_display = (
        "data",
        "hora",
        "data_evento",
        "matricula_usuario",
        "evento",
        "segundo_evento",
    )
    list_filter = ("evento", "data")
    search_fields = ("matricula_usuario", "evento")
    date_hierarchy = "data_evento"


@admin.register(MonitorEventoSyncLog)
class MonitorEventoSyncLogAdmin(admin.ModelAdmin):
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
