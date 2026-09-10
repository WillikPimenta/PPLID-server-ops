from django.contrib import admin

from apps.suporte_claro.models import SuporteClaroAnexo, SuporteClaroProtocolo, SuporteClaroRegistro, SuporteClaroComentarioEtapa


class SuporteClaroAnexoInline(admin.TabularInline):
    model = SuporteClaroAnexo
    extra = 0
    readonly_fields = ("uploaded_at", "size_bytes", "content_type")


class SuporteClaroProtocoloInline(admin.TabularInline):
    model = SuporteClaroProtocolo
    extra = 0
    fields = ("numero", "comentario", "ordem")


class SuporteClaroComentarioEtapaInline(admin.TabularInline):
    model = SuporteClaroComentarioEtapa
    extra = 0
    readonly_fields = ("created_at", "created_by", "etapa_status")
    fields = ("texto", "etapa_status", "created_by", "created_at")


@admin.register(SuporteClaroRegistro)
class SuporteClaroRegistroAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "titulo",
        "protocolo",
        "categoria",
        "tipo_incidente",
        "status",
        "received_at",
        "sent_by",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "categoria", "tipo_incidente", "created_at")
    search_fields = ("titulo", "protocolo", "sent_by", "irregularidade")
    readonly_fields = ("created_at", "updated_at")
    inlines = [
        SuporteClaroProtocoloInline,
        SuporteClaroAnexoInline,
        SuporteClaroComentarioEtapaInline,
    ]


@admin.register(SuporteClaroAnexo)
class SuporteClaroAnexoAdmin(admin.ModelAdmin):
    list_display = ("id", "registro", "original_name", "size_bytes", "uploaded_at")
    search_fields = ("original_name", "registro__protocolo", "registro__titulo")


@admin.register(SuporteClaroComentarioEtapa)
class SuporteClaroComentarioEtapaAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "registro",
        "visibilidade",
        "formalizado_externo",
        "etapa_status",
        "created_by",
        "created_at",
    )
    list_filter = ("visibilidade", "formalizado_externo", "etapa_status", "created_at")
    search_fields = ("texto", "registro__protocolo", "registro__titulo", "formalizado_issue_keys")
    readonly_fields = ("created_at",)