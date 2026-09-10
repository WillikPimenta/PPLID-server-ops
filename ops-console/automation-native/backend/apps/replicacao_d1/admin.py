# -*- coding: utf-8 -*-
from django.contrib import admin

from apps.replicacao_d1.models import (
    ReplicacaoD1Categoria,
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1ConfigHistorico,
    ReplicacaoD1ConfigSnapshot,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1ExecutionEvent,
    ReplicacaoD1FonteLote,
    ReplicacaoD1FonteRegistro,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1MetaMensal,
    ReplicacaoD1PlanDeletion,
    ReplicacaoD1PlanReview,
    ReplicacaoD1Protocolo,
    ReplicacaoD1Replicado,
    ReplicacaoD1Run,
    ReplicacaoD1SchedulerState,
    ReplicacaoD1Segmento,
    ReplicacaoD1SyncLog,
    ReplicacaoD1Workflow,
    ReplicacaoD1WorkflowDia,
)


@admin.register(ReplicacaoD1FonteLote)
class ReplicacaoD1FonteLoteAdmin(admin.ModelAdmin):
    list_display = ("report_date", "status", "rows_read", "rows_valid", "rows_duplicate", "rows_rejected", "finished_at")
    list_filter = ("status", "report_date")
    search_fields = ("content_hash",)
    readonly_fields = ("created_at", "finished_at")


@admin.register(ReplicacaoD1FonteRegistro)
class ReplicacaoD1FonteRegistroAdmin(admin.ModelAdmin):
    list_display = ("lote", "source_row_number", "protocolo", "workflow", "data_analise", "matricula_tipo")
    list_filter = ("matricula_tipo", "lote__report_date")
    search_fields = ("protocolo", "protocolo_normalizado", "workflow")


@admin.register(ReplicacaoD1PlanReview)
class ReplicacaoD1PlanReviewAdmin(admin.ModelAdmin):
    list_display = ("run", "action", "user", "plan_revision", "created_at")
    list_filter = ("action",)
    search_fields = ("run__run_id", "plan_hash", "reason")
    readonly_fields = ("run", "action", "user", "reason", "plan_hash", "plan_revision", "created_at")


@admin.register(ReplicacaoD1PlanDeletion)
class ReplicacaoD1PlanDeletionAdmin(admin.ModelAdmin):
    list_display = (
        "run_id",
        "data_referencia_d1",
        "validation_status",
        "protocolos_total",
        "workflows_total",
        "deleted_by",
        "deleted_at",
    )
    list_filter = ("validation_status", "data_referencia_d1")
    search_fields = ("run_id", "plan_hash", "deletion_reason")
    readonly_fields = (
        "run_id",
        "data_referencia_d1",
        "source_batch",
        "status_canonical",
        "validation_status",
        "plan_hash",
        "plan_revision",
        "config_hash",
        "protocolos_total",
        "workflows_total",
        "deletion_reason",
        "deleted_by",
        "audit_snapshot",
        "deleted_at",
    )


@admin.register(ReplicacaoD1ExecutionEvent)
class ReplicacaoD1ExecutionEventAdmin(admin.ModelAdmin):
    list_display = ("run", "workflow", "phase", "status", "created_at")
    list_filter = ("phase", "status")
    search_fields = ("run__run_id", "message")
    readonly_fields = ("run", "workflow", "phase", "status", "message", "payload", "created_at")


admin.site.register(ReplicacaoD1SchedulerState)


@admin.register(ReplicacaoD1Run)
class ReplicacaoD1RunAdmin(admin.ModelAdmin):
    list_display = (
        "run_id",
        "data_referencia_d1",
        "data_execucao",
        "parquet_referencia",
        "protocolos_total",
        "workflows_total",
        "workflows_salvo_ok",
        "synced_at",
    )
    search_fields = ("run_id", "parquet_referencia")
    list_filter = ("data_referencia_d1",)
    date_hierarchy = "data_referencia_d1"


@admin.register(ReplicacaoD1WorkflowDia)
class ReplicacaoD1WorkflowDiaAdmin(admin.ModelAdmin):
    list_display = (
        "run_id",
        "workflow_config",
        "cliente",
        "canal_destino",
        "amostra_efetiva",
        "protocolos_salvos",
        "pct_atingido",
        "status_brflow",
        "data_referencia_d1",
    )
    list_filter = ("status_brflow", "canal_destino", "data_referencia_d1")
    search_fields = ("run_id", "workflow_config", "cliente", "workflow_brflow")


@admin.register(ReplicacaoD1Protocolo)
class ReplicacaoD1ProtocoloAdmin(admin.ModelAdmin):
    list_display = (
        "run_id",
        "protocolo",
        "workflow_config",
        "canal_destino",
        "status_brflow",
        "data_analise",
        "data_referencia_d1",
    )
    list_filter = ("status_brflow", "canal_destino", "data_referencia_d1")
    search_fields = ("run_id", "protocolo", "workflow_config")


@admin.register(ReplicacaoD1Replicado)
class ReplicacaoD1ReplicadoAdmin(admin.ModelAdmin):
    list_display = (
        "report_date",
        "protocolo_origem",
        "workflow_origem",
        "protocolo_destino",
        "workflow_destino",
        "cliente_origem",
        "cliente_destino",
    )
    list_filter = ("report_date",)
    search_fields = ("protocolo_origem", "protocolo_destino", "workflow_origem")
    date_hierarchy = "report_date"


@admin.register(ReplicacaoD1SyncLog)
class ReplicacaoD1SyncLogAdmin(admin.ModelAdmin):
    list_display = (
        "started_at",
        "kind",
        "run_id",
        "report_date",
        "trigger_source",
        "success",
        "row_count",
        "duration_seconds",
        "source_file",
    )
    list_filter = ("success", "trigger_source", "kind")
    readonly_fields = (
        "started_at",
        "finished_at",
        "duration_seconds",
        "trigger_source",
        "kind",
        "user",
        "success",
        "message",
        "source_file",
        "source_mtime",
        "source_size",
        "run_id",
        "report_date",
        "row_count",
    )


@admin.register(ReplicacaoD1ConfigGeral)
class ReplicacaoD1ConfigGeralAdmin(admin.ModelAdmin):
    list_display = (
        "pk",
        "fonte_banco_ativa",
        "config_version",
        "config_hash",
        "agendamento_ativo",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(ReplicacaoD1Segmento)
class ReplicacaoD1SegmentoAdmin(admin.ModelAdmin):
    list_display = ("nome", "chave_normalizada", "ativo", "updated_at")
    search_fields = ("nome", "chave_normalizada")
    list_filter = ("ativo",)


@admin.register(ReplicacaoD1Categoria)
class ReplicacaoD1CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nome", "segmento", "chave_normalizada", "ativo", "updated_at")
    search_fields = ("nome", "chave_normalizada")
    list_filter = ("ativo",)


@admin.register(ReplicacaoD1Cliente)
class ReplicacaoD1ClienteAdmin(admin.ModelAdmin):
    list_display = (
        "nome",
        "chave_normalizada",
        "segmento_nome",
        "categoria_nome",
        "meta_mensal",
        "ativo",
    )
    search_fields = ("nome", "chave_normalizada")
    list_filter = ("ativo",)


@admin.register(ReplicacaoD1Workflow)
class ReplicacaoD1WorkflowAdmin(admin.ModelAdmin):
    list_display = (
        "nome_canonico",
        "cliente",
        "fila",
        "status",
        "ativo",
        "amostra_100",
        "updated_at",
    )
    search_fields = ("nome_canonico", "nome_d1", "chave_normalizada")
    list_filter = ("status", "ativo", "fila")


@admin.register(ReplicacaoD1MetaMensal)
class ReplicacaoD1MetaMensalAdmin(admin.ModelAdmin):
    list_display = ("cliente", "competencia", "meta", "updated_at")
    list_filter = ("competencia",)
    search_fields = ("cliente__nome",)


@admin.register(ReplicacaoD1EscalaDia)
class ReplicacaoD1EscalaDiaAdmin(admin.ModelAdmin):
    list_display = ("data", "auditores_brflow", "auditores_case", "updated_at")
    date_hierarchy = "data"


@admin.register(ReplicacaoD1LedgerConsumo)
class ReplicacaoD1LedgerConsumoAdmin(admin.ModelAdmin):
    list_display = (
        "competencia",
        "run_id",
        "workflow_chave",
        "cliente_nome",
        "protocolos",
        "origem",
        "ajuste",
        "data_execucao",
    )
    list_filter = ("competencia", "origem")
    search_fields = ("run_id", "workflow_chave", "cliente_nome")


@admin.register(ReplicacaoD1ConfigHistorico)
class ReplicacaoD1ConfigHistoricoAdmin(admin.ModelAdmin):
    list_display = ("created_at", "entidade", "entidade_id", "operacao", "usuario", "lote_id")
    list_filter = ("operacao", "entidade")
    search_fields = ("entidade", "entidade_id", "lote_id")
    readonly_fields = ("created_at", "valores_anteriores", "valores_novos")


@admin.register(ReplicacaoD1ConfigSnapshot)
class ReplicacaoD1ConfigSnapshotAdmin(admin.ModelAdmin):
    list_display = ("run_id", "config_version", "config_hash", "created_at")
    search_fields = ("run_id", "config_hash")
    readonly_fields = ("created_at", "snapshot_json")
