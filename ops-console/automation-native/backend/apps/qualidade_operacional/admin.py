# -*- coding: utf-8 -*-
from django.contrib import admin

from apps.qualidade_operacional.models import (
    QualidadeAgenteAcao,
    QualidadeAuditado,
    QualidadeFalha,
    QualidadeGAuditoriaFailureReconciliation,
    QualidadeGAuditoriaProjection,
)


@admin.register(QualidadeAuditado)
class QualidadeAuditadoAdmin(admin.ModelAdmin):
    actions = None
    list_display = (
        "protocolo",
        "data",
        "id_cliente",
        "id_workflow",
        "matricula",
        "tipo_analise",
        "tipo_conclusao",
    )
    list_filter = ("tipo_analise", "tipo_conclusao")
    search_fields = ("protocolo", "matricula", "matricula_auditor")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QualidadeFalha)
class QualidadeFalhaAdmin(admin.ModelAdmin):
    actions = None
    list_display = (
        "protocolo",
        "data_analise",
        "id_cliente",
        "id_workflow",
        "matricula",
        "tipo_falha",
        "localidade",
    )
    list_filter = ("tipo_falha", "localidade")
    search_fields = ("protocolo", "matricula", "lider")

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(QualidadeAgenteAcao)
class QualidadeAgenteAcaoAdmin(admin.ModelAdmin):
    list_display = (
        "titulo",
        "matricula",
        "tipo",
        "status",
        "prioridade",
        "responsavel",
        "prazo",
        "created_at",
    )
    list_filter = ("status", "tipo", "prioridade")
    search_fields = ("titulo", "matricula", "responsavel", "categoria_falha")


@admin.register(QualidadeGAuditoriaProjection)
class QualidadeGAuditoriaProjectionAdmin(admin.ModelAdmin):
    list_display = (
        "staging_id",
        "auditado_id",
        "mapping_version",
        "is_active",
        "projected_at",
    )
    list_filter = ("is_active", "mapping_version")
    search_fields = (
        "staging__source_key",
        "staging__protocolo_origem",
        "auditado__protocolo",
    )


@admin.register(QualidadeGAuditoriaFailureReconciliation)
class QualidadeGAuditoriaFailureReconciliationAdmin(admin.ModelAdmin):
    list_display = (
        "falha_id",
        "projection_id",
        "status",
        "candidate_count",
        "reconciled_at",
    )
    list_filter = ("status", "mapping_version")
    search_fields = ("falha__protocolo", "observation")
