from django.contrib import admin

from apps.falhas_criticas.models import (
    Contestation,
    ExecutiveReportArchive,
    Failure,
    FalhasAgent,
    Support,
    SyncAuditLog,
    Training,
)


@admin.register(FalhasAgent)
class FalhasAgentAdmin(admin.ModelAdmin):
    list_display = ("matricula_norm", "name", "localidade", "turno_atual", "atividade_atual")
    search_fields = ("matricula_norm", "name")
    list_filter = ("localidade", "turno_atual")


@admin.register(Failure)
class FailureAdmin(admin.ModelAdmin):
    list_display = ("protocolo", "data_analise", "localidade", "modulo", "tipo_falha", "agent")
    search_fields = ("protocolo", "cenario", "cliente")
    list_filter = ("localidade", "modulo", "tipo_falha")
    date_hierarchy = "data_analise"


@admin.register(Support)
class SupportAdmin(admin.ModelAdmin):
    list_display = ("protocolo", "data", "cliente", "conformidade_regra", "critico", "localidade")
    list_filter = ("localidade", "conformidade_regra", "critico")
    date_hierarchy = "data"


@admin.register(Training)
class TrainingAdmin(admin.ModelAdmin):
    list_display = ("titulo", "matricula", "localidade", "status", "situacao", "data_limite")
    list_filter = ("localidade", "status", "tipo_acao")
    search_fields = ("matricula", "nome_agente", "titulo")


@admin.register(Contestation)
class ContestationAdmin(admin.ModelAdmin):
    list_display = ("protocolo", "data", "fonte", "status", "localidade")
    list_filter = ("localidade", "fonte")


@admin.register(ExecutiveReportArchive)
class ExecutiveReportArchiveAdmin(admin.ModelAdmin):
    list_display = ("period_start", "period_end", "generated_at", "generated_by")
    date_hierarchy = "generated_at"


@admin.register(SyncAuditLog)
class SyncAuditLogAdmin(admin.ModelAdmin):
    list_display = ("started_at", "trigger_source", "user", "success", "duration_seconds", "path")
    list_filter = ("success", "trigger_source")
    readonly_fields = (
        "started_at",
        "finished_at",
        "duration_seconds",
        "trigger_source",
        "user",
        "success",
        "message",
        "path",
    )
