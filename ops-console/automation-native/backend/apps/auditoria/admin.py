from django.contrib import admin

from apps.auditoria.models import AuditoriaFalhaCadastro, QualidadeAnaliseOrigem


@admin.register(QualidadeAnaliseOrigem)
class QualidadeAnaliseOrigemAdmin(admin.ModelAdmin):
    list_display = (
        "protocolo",
        "protocolo_criado_em",
        "protocolo_analisado_em",
        "protocolo_concluido_em",
        "schema_version",
        "conteudo_hash",
        "created_at",
    )
    search_fields = ("protocolo", "conteudo_hash")
    readonly_fields = (
        "protocolo_criado_em",
        "protocolo_analisado_em",
        "protocolo_concluido_em",
        "conteudo_hash",
        "created_at",
        "updated_at",
    )


@admin.register(AuditoriaFalhaCadastro)
class AuditoriaFalhaCadastroAdmin(admin.ModelAdmin):
    list_display = (
        "protocolo",
        "resultado_qualidade",
        "tipo_falha",
        "usuario",
        "modulo",
        "created_at",
        "created_by",
    )
    search_fields = ("protocolo", "usuario", "modulo")
    list_filter = ("resultado_qualidade", "tipo_falha", "modulo")
