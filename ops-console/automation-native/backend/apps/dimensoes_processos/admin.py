# -*- coding: utf-8 -*-
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from apps.dimensoes_processos.models import (
    DerivacaoEtapaComparativo,
    DerivacaoEtapaDiaria,
    DerivacaoEtapaImportRun,
    DimNomeAlias,
)
from apps.dimensoes_processos.services.derivacao_etapa.validacao import (
    _divergencia_detalhe,
    _nome_divergente,
)


@admin.register(DerivacaoEtapaImportRun)
class DerivacaoEtapaImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "run_kind",
        "status",
        "started_at",
        "finished_at",
        "files_processed",
        "rows_inserted",
        "rows_rejected",
        "rows_skipped_total",
    )
    list_filter = ("run_kind", "status")
    readonly_fields = (
        "run_kind",
        "started_at",
        "finished_at",
        "status",
        "files_processed",
        "rows_inserted",
        "rows_skipped_total",
        "rows_rejected",
        "message",
        "metrics",
        "triggered_by",
    )
    ordering = ("-started_at",)


@admin.register(DerivacaoEtapaDiaria)
class DerivacaoEtapaDiariaAdmin(admin.ModelAdmin):
    list_display = (
        "data",
        "cliente",
        "workflow",
        "etapa",
        "registros",
        "percentual",
        "validacao_badge",
        "source_file",
        "updated_at",
    )
    list_filter = ("data", "cliente", "workflow", "etapa")
    search_fields = (
        "cliente__nome",
        "workflow__nome",
        "etapa__nome",
        "cliente_nome_origem",
        "workflow_nome_origem",
        "etapa_nome_origem",
        "source_file",
    )
    readonly_fields = (
        "data",
        "cliente",
        "workflow",
        "etapa",
        "registros",
        "percentual",
        "cliente_nome_origem",
        "workflow_nome_origem",
        "etapa_nome_origem",
        "source_file",
        "import_run",
        "updated_at",
        "validacao_detalhe",
    )
    ordering = ("-data", "cliente_id", "workflow_id", "etapa_id")
    date_hierarchy = "data"
    list_per_page = 50

    @admin.display(description="Validação")
    def validacao_badge(self, obj: DerivacaoEtapaDiaria) -> str:
        issues = []
        if obj.percentual > 100 or obj.percentual < 0:
            issues.append("percentual")
        if _nome_divergente(obj):
            issues.extend(_divergencia_detalhe(obj))
        if not issues:
            return format_html('<span style="color:#047857;font-weight:600;">OK</span>')
        uniq = sorted(set(issues))
        return format_html(
            '<span style="color:#b45309;font-weight:600;" title="{}">Alerta</span>',
            ", ".join(uniq),
        )

    @admin.display(description="Detalhe validação")
    def validacao_detalhe(self, obj: DerivacaoEtapaDiaria) -> str:
        issues = []
        if obj.percentual > 100 or obj.percentual < 0:
            issues.append(f"Percentual fora de 0–100: {obj.percentual}")
        for campo in _divergencia_detalhe(obj):
            if campo == "cliente":
                issues.append(f"Cliente CSV ≠ Megazord: {obj.cliente_nome_origem!r} → {obj.cliente.nome!r}")
            if campo == "workflow":
                issues.append(
                    f"Workflow CSV ≠ Megazord: {obj.workflow_nome_origem!r} → {obj.workflow.nome!r}"
                )
            if campo == "etapa":
                issues.append(f"Etapa CSV ≠ Megazord: {obj.etapa_nome_origem!r} → {obj.etapa.nome!r}")
        if not issues:
            return "Sem divergências detectadas."
        return format_html("<br>".join(issues))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DimNomeAlias)
class DimNomeAliasAdmin(admin.ModelAdmin):
    list_display = ("dimensao", "nome_origem", "cliente", "workflow", "etapa", "ativo", "created_at")
    list_filter = ("dimensao", "ativo")
    search_fields = ("nome_origem", "notas")


@admin.register(DerivacaoEtapaComparativo)
class DerivacaoEtapaComparativoAdmin(admin.ModelAdmin):
    list_display = (
        "scan_run",
        "dimensao",
        "nome_origem",
        "linhas_csv",
        "id_resolvido",
        "status",
        "match_strategy",
    )
    list_filter = ("scan_run", "dimensao", "status")
    search_fields = ("nome_origem", "nome_megazord")
