from django.contrib import admin

from .models import (
    RotinaBrutoSyncLog,
    RotinaConferBuscaRecord,
    RotinaDetalhadoBrutoAlerta,
    RotinaDetalhadoBrutoRecord,
    RotinaGedDetalhadoTratadoRecord,
    RotinaGedIrregularidadeTratadoRecord,
    RotinaGAuditoriaRecord,
    RotinaMonitorTratadoRecord,
    RotinaProdBrutoRecord,
)


@admin.register(RotinaBrutoSyncLog)
class RotinaBrutoSyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "report_type",
        "success",
        "report_date",
        "row_count",
        "trigger_source",
        "started_at",
        "duration_seconds",
    )
    list_filter = ("report_type", "success", "trigger_source")
    search_fields = ("source_file", "message")


class RotinaDetalhadoBrutoAlertaInline(admin.StackedInline):
    model = RotinaDetalhadoBrutoAlerta
    extra = 0
    can_delete = False


@admin.register(RotinaDetalhadoBrutoRecord)
class RotinaDetalhadoBrutoRecordAdmin(admin.ModelAdmin):
    list_display = ("id", "report_date", "protocolo", "matricula", "cliente")
    list_filter = ("report_date",)
    search_fields = ("protocolo", "matricula", "cliente")
    inlines = [RotinaDetalhadoBrutoAlertaInline]


admin.site.register(RotinaDetalhadoBrutoAlerta)
admin.site.register(RotinaProdBrutoRecord)
admin.site.register(RotinaMonitorTratadoRecord)
admin.site.register(RotinaConferBuscaRecord)
admin.site.register(RotinaGedDetalhadoTratadoRecord)
admin.site.register(RotinaGedIrregularidadeTratadoRecord)


@admin.register(RotinaGAuditoriaRecord)
class RotinaGAuditoriaRecordAdmin(admin.ModelAdmin):
    list_display = (
        "report_date",
        "protocolo_origem",
        "etapa",
        "matricula_agente",
        "data_analise",
        "data_auditoria",
        "prazo_status",
        "is_active",
    )
    list_filter = ("report_date", "prazo_status", "is_active", "is_quarantined")
    search_fields = (
        "source_key",
        "protocolo_origem",
        "protocolo_destino",
        "matricula_agente",
        "matricula_auditor",
        "etapa",
    )
