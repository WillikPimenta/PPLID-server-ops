# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import models

from apps.replicacao_d1.normalization import (
    RESULTADO_CHOICES,
    RESULTADO_PENDENTE,
    SEVERIDADE_CHOICES,
    SEVERIDADE_INFO,
    STATUS_OPERACIONAL_CHOICES,
    STATUS_PLANEJADO,
)


class ReplicacaoD1FonteLote(models.Model):
    """Lote normalizado da volumetria D-1; substitui o parquet como fonte oficial."""

    STATUS_LOADING = "loading"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_LOADING, "Carregando"),
        (STATUS_READY, "Pronto"),
        (STATUS_FAILED, "Falhou"),
    ]

    report_date = models.DateField(db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_LOADING, db_index=True)
    schema_version = models.CharField(max_length=16, default="1")
    content_hash = models.CharField(max_length=64, db_index=True)
    rows_read = models.PositiveIntegerField(default=0)
    rows_valid = models.PositiveIntegerField(default=0)
    rows_duplicate = models.PositiveIntegerField(default=0)
    rows_rejected = models.PositiveIntegerField(default=0)
    error_summary = models.CharField(max_length=255, blank=True, default="")
    ingestion = models.ForeignKey(
        "common.BotDataIngestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_fonte_lotes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "replicacao_d1_fonte_lote"
        ordering = ["-report_date", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["report_date", "content_hash"],
                name="replicacao_d1_fonte_data_hash_uniq",
            ),
        ]
        indexes = [models.Index(fields=["report_date", "status"], name="rep_d1_fonte_data_st")]

    def __str__(self) -> str:
        return f"Fonte D-1 {self.report_date} [{self.status}]"


class ReplicacaoD1FonteRegistro(models.Model):
    """Linha auditável da fonte D-1, inclusive para rastrear duplicidades."""

    MATRICULA_MANUAL = "manual"
    MATRICULA_AUTOMATICA = "automatico"
    MATRICULA_DESCONHECIDA = "desconhecido"
    MATRICULA_CHOICES = [
        (MATRICULA_MANUAL, "Manual"),
        (MATRICULA_AUTOMATICA, "Automático"),
        (MATRICULA_DESCONHECIDA, "Desconhecido"),
    ]

    lote = models.ForeignKey(ReplicacaoD1FonteLote, on_delete=models.CASCADE, related_name="registros")
    source_row_number = models.PositiveIntegerField()
    protocolo = models.CharField(max_length=64, db_index=True)
    protocolo_normalizado = models.CharField(max_length=64, db_index=True)
    workflow = models.CharField(max_length=255, db_index=True)
    data_analise = models.DateTimeField()
    hora = models.PositiveSmallIntegerField(null=True, blank=True)
    matricula_tipo = models.CharField(
        max_length=16,
        choices=MATRICULA_CHOICES,
        default=MATRICULA_DESCONHECIDA,
        db_index=True,
    )
    matricula = models.CharField(max_length=64, blank=True, default="")
    extra = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "replicacao_d1_fonte_registro"
        ordering = ["lote_id", "source_row_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["lote", "source_row_number"],
                name="rep_d1_fonte_lote_linha_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["lote", "workflow"], name="rep_d1_fonte_lote_wf"),
            models.Index(fields=["lote", "protocolo_normalizado"], name="rep_d1_fonte_lote_prot"),
        ]

    def __str__(self) -> str:
        return f"{self.lote_id} / {self.source_row_number} / {self.protocolo}"


class ReplicacaoD1Run(models.Model):
    """Cabeçalho de um plano/execução D-1 (1 linha por run_id)."""

    STATUS_PLANNED = "planned"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_LEGACY = "legacy_unlinked"
    STATUS_CHOICES = [
        (STATUS_PLANNED, "Planejado"),
        (STATUS_RUNNING, "Em execução"),
        (STATUS_COMPLETED, "Concluído"),
        (STATUS_PARTIAL, "Parcial"),
        (STATUS_FAILED, "Falhou"),
        (STATUS_CANCELLED, "Cancelado"),
        (STATUS_LEGACY, "Legado sem vínculo"),
    ]

    VALIDATION_PENDING = "pending"
    VALIDATION_APPROVED = "approved"
    VALIDATION_REJECTED = "rejected"
    VALIDATION_SUPERSEDED = "superseded"
    VALIDATION_CHOICES = [
        (VALIDATION_PENDING, "Aguardando validação"),
        (VALIDATION_APPROVED, "Aprovado"),
        (VALIDATION_REJECTED, "Rejeitado"),
        (VALIDATION_SUPERSEDED, "Substituído"),
    ]

    run_id = models.CharField(max_length=64, unique=True, db_index=True)
    status_canonical = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_LEGACY,
        db_index=True,
    )
    config_version = models.PositiveIntegerField(null=True, blank=True)
    config_hash = models.CharField(max_length=64, blank=True, default="")
    config_snapshot = models.ForeignKey(
        "replicacao_d1.ReplicacaoD1ConfigSnapshot",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    source_batch = models.ForeignKey(
        ReplicacaoD1FonteLote,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="runs",
    )
    validation_status = models.CharField(
        max_length=16,
        choices=VALIDATION_CHOICES,
        default=VALIDATION_PENDING,
        db_index=True,
    )
    plan_hash = models.CharField(max_length=64, blank=True, default="", db_index=True)
    plan_revision = models.PositiveIntegerField(default=1)
    plan_summary = models.JSONField(default=dict, blank=True)
    plan_warnings = models.JSONField(default=list, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_planos_revisados",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.CharField(max_length=500, blank=True, default="")
    data_referencia_d1 = models.DateField(db_index=True)
    data_execucao = models.DateTimeField(null=True, blank=True)
    parquet_referencia = models.CharField(max_length=255, blank=True, default="")
    auditores_ativos_brflow = models.PositiveIntegerField(null=True, blank=True)
    auditores_ativos_case = models.PositiveIntegerField(null=True, blank=True)
    protocolos_total = models.PositiveIntegerField(default=0)
    workflows_total = models.PositiveIntegerField(default=0)
    workflows_salvo_ok = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempt_number = models.PositiveIntegerField(default=1)
    duration_seconds = models.FloatField(null=True, blank=True)
    erro_codigo = models.CharField(max_length=64, blank=True, default="")
    erro_resumo = models.CharField(max_length=255, blank=True, default="")
    ingestion = models.ForeignKey(
        "common.BotDataIngestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
    )
    synced_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "replicacao_d1_run"
        ordering = ["-data_referencia_d1", "-run_id"]
        indexes = [
            models.Index(fields=["data_execucao"], name="rep_d1_run_execucao"),
        ]

    def __str__(self) -> str:
        return f"ReplicacaoD1Run {self.run_id}"


class ReplicacaoD1WorkflowDia(models.Model):
    """Visão analítica por workflow no run (grão dashboard)."""

    MODO_PROTOCOLOS = "protocolos"
    MODO_QTD = "qtd"
    MODO_REPLICACAO_CHOICES = [
        (MODO_PROTOCOLOS, "Protocolos (arquivo CSV)"),
        (MODO_QTD, "Quantidade"),
    ]

    run = models.ForeignKey(
        ReplicacaoD1Run,
        to_field="run_id",
        on_delete=models.CASCADE,
        related_name="workflows",
        db_column="run_id",
    )
    data_referencia_d1 = models.DateField(db_index=True)
    workflow_config = models.CharField(max_length=255)
    workflow_d1 = models.CharField(max_length=255, blank=True, default="")
    workflow_brflow = models.CharField(max_length=255, blank=True, default="")
    canal_destino = models.CharField(max_length=64, blank=True, default="")
    cliente = models.CharField(max_length=128, blank=True, default="", db_index=True)
    segmento = models.CharField(max_length=128, blank=True, default="")
    categoria = models.CharField(max_length=128, blank=True, default="")
    fila = models.CharField(max_length=64, blank=True, default="")
    modo_replicacao = models.CharField(
        max_length=16,
        choices=MODO_REPLICACAO_CHOICES,
        default=MODO_PROTOCOLOS,
    )
    amostra_diaria = models.IntegerField(null=True, blank=True)
    amostra_solicitada = models.IntegerField(null=True, blank=True)
    amostra_efetiva = models.IntegerField(null=True, blank=True)
    protocolos_salvos = models.IntegerField(null=True, blank=True)
    pct_atingido = models.FloatField(null=True, blank=True)
    disponivel_d1 = models.IntegerField(null=True, blank=True)
    status_amostra = models.CharField(max_length=64, blank=True, default="")
    status_brflow = models.CharField(max_length=64, blank=True, default="", db_index=True)
    resultado = models.CharField(
        max_length=24,
        choices=RESULTADO_CHOICES,
        default=RESULTADO_PENDENTE,
        db_index=True,
    )
    severidade = models.CharField(
        max_length=12,
        choices=SEVERIDADE_CHOICES,
        default=SEVERIDADE_INFO,
        db_index=True,
    )
    motivo_codigo = models.CharField(max_length=64, blank=True, default="")
    motivo_resumo = models.CharField(max_length=255, blank=True, default="")
    fase_execucao = models.CharField(max_length=64, blank=True, default="")
    quantidade_alvo = models.PositiveIntegerField(null=True, blank=True)
    quantidade_encontrada = models.PositiveIntegerField(null=True, blank=True)
    status_operacional = models.CharField(
        max_length=16,
        choices=STATUS_OPERACIONAL_CHOICES,
        default=STATUS_PLANEJADO,
        db_index=True,
    )
    data_hora_upload_brflow = models.CharField(max_length=64, blank=True, default="")
    upload_em = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    attempt_number = models.PositiveIntegerField(default=1)
    protocolos_planejados = models.PositiveIntegerField(default=0)
    protocolos_enviados = models.PositiveIntegerField(default=0)
    protocolos_aceitos = models.PositiveIntegerField(default=0)
    excluidos_historico = models.PositiveIntegerField(default=0)
    redistribuidos = models.PositiveIntegerField(default=0)
    protocolos_manuais = models.PositiveIntegerField(default=0)
    protocolos_automaticos = models.PositiveIntegerField(default=0)
    protocolos_retroativos = models.PositiveIntegerField(default=0)
    warnings = models.JSONField(default=list, blank=True)
    erro_codigo = models.CharField(max_length=64, blank=True, default="")
    erro_resumo = models.CharField(max_length=255, blank=True, default="")
    ingestion = models.ForeignKey(
        "common.BotDataIngestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="workflows_dia",
    )
    faixa_horaria = models.CharField(max_length=128, blank=True, default="")

    class Meta:
        db_table = "replicacao_d1_workflow_dia"
        ordering = ["run_id", "workflow_config"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "workflow_config"],
                name="replicacao_d1_wf_run_config_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["cliente", "data_referencia_d1"],
                name="replicacao_d1_wf_cli_data",
            ),
            models.Index(
                fields=["data_referencia_d1", "status_brflow"],
                name="replicacao_d1_wf_data_st",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} / {self.workflow_config}"


class ReplicacaoD1Protocolo(models.Model):
    """Protocolos do plano (drill-down)."""

    run = models.ForeignKey(
        ReplicacaoD1Run,
        to_field="run_id",
        on_delete=models.CASCADE,
        related_name="protocolos",
        db_column="run_id",
    )
    data_referencia_d1 = models.DateField(db_index=True)
    protocolo = models.CharField(max_length=64, db_index=True)
    protocolo_normalizado = models.CharField(max_length=64, blank=True, default="", db_index=True)
    source_record = models.ForeignKey(
        ReplicacaoD1FonteRegistro,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="protocolos_planejados",
    )
    workflow_config = models.CharField(max_length=255, db_index=True)
    workflow_d1 = models.CharField(max_length=255, blank=True, default="")
    data_analise = models.DateTimeField(null=True, blank=True)
    hora = models.PositiveSmallIntegerField(null=True, blank=True)
    canal_destino = models.CharField(max_length=64, blank=True, default="")
    status_brflow = models.CharField(max_length=64, blank=True, default="")
    status_operacional = models.CharField(
        max_length=16,
        choices=STATUS_OPERACIONAL_CHOICES,
        default=STATUS_PLANEJADO,
        db_index=True,
    )
    replicado_em = models.DateTimeField(null=True, blank=True)
    erro_resumido = models.CharField(max_length=255, blank=True, default="")
    matricula_tipo = models.CharField(
        max_length=16,
        choices=ReplicacaoD1FonteRegistro.MATRICULA_CHOICES,
        default=ReplicacaoD1FonteRegistro.MATRICULA_DESCONHECIDA,
        db_index=True,
    )
    selection_reason = models.CharField(max_length=128, blank=True, default="")
    ingestion = models.ForeignKey(
        "common.BotDataIngestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="protocolos",
    )

    class Meta:
        db_table = "replicacao_d1_protocolo"
        ordering = ["run_id", "workflow_config", "protocolo"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "protocolo"],
                name="replicacao_d1_prot_run_prot_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workflow_config", "data_referencia_d1"],
                name="replicacao_d1_prot_wf_data",
            ),
            models.Index(
                fields=["data_referencia_d1", "protocolo_normalizado"],
                name="replicacao_d1_prot_data_norm",
            ),
            models.Index(
                fields=["data_referencia_d1", "status_operacional"],
                name="replicacao_d1_prot_data_st",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.run_id} / {self.protocolo}"


class ReplicacaoD1PlanReview(models.Model):
    ACTION_APPROVE = "approve"
    ACTION_REJECT = "reject"
    ACTION_CHOICES = [
        (ACTION_APPROVE, "Aprovar"),
        (ACTION_REJECT, "Rejeitar"),
    ]

    run = models.ForeignKey(ReplicacaoD1Run, on_delete=models.CASCADE, related_name="reviews")
    action = models.CharField(max_length=16, choices=ACTION_CHOICES, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_plan_reviews",
    )
    reason = models.CharField(max_length=500, blank=True, default="")
    plan_hash = models.CharField(max_length=64)
    plan_revision = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "replicacao_d1_plan_review"
        ordering = ["-created_at"]


class ReplicacaoD1PlanDeletion(models.Model):
    """Tombstone auditável de um plano removido antes da execução."""

    run_id = models.CharField(max_length=64, db_index=True)
    data_referencia_d1 = models.DateField(db_index=True)
    source_batch = models.ForeignKey(
        ReplicacaoD1FonteLote,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="plan_deletions",
    )
    status_canonical = models.CharField(max_length=32)
    validation_status = models.CharField(max_length=16)
    plan_hash = models.CharField(max_length=64)
    plan_revision = models.PositiveIntegerField()
    config_hash = models.CharField(max_length=64, blank=True, default="")
    protocolos_total = models.PositiveIntegerField(default=0)
    workflows_total = models.PositiveIntegerField(default=0)
    deletion_reason = models.CharField(max_length=500)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_plan_deletions",
    )
    audit_snapshot = models.JSONField(default=dict, blank=True)
    deleted_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "replicacao_d1_plan_deletion"
        ordering = ["-deleted_at", "-id"]


class ReplicacaoD1ExecutionEvent(models.Model):
    run = models.ForeignKey(ReplicacaoD1Run, on_delete=models.CASCADE, related_name="events")
    workflow = models.ForeignKey(
        ReplicacaoD1WorkflowDia,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    phase = models.CharField(max_length=32, db_index=True)
    status = models.CharField(max_length=32, db_index=True)
    message = models.CharField(max_length=500, blank=True, default="")
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "replicacao_d1_execution_event"
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["run", "phase", "status"], name="rep_d1_event_run_phase_st")]


class ReplicacaoD1SchedulerState(models.Model):
    key = models.CharField(max_length=64, unique=True, default="replicacao_d1")
    state = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "replicacao_d1_scheduler_state"


class ReplicacaoD1Replicado(models.Model):
    """Confirmação BRFlow (brflow-replicadosd1-tratado) — o que foi de fato replicado."""

    report_date = models.DateField(db_index=True)
    cliente_destino = models.CharField(max_length=255, blank=True, default="")
    data_cadastro_destino = models.DateTimeField(null=True, blank=True)
    protocolo_destino = models.CharField(max_length=64, blank=True, default="", db_index=True)
    workflow_destino = models.CharField(max_length=255, blank=True, default="")
    protocolo_origem = models.CharField(max_length=64, db_index=True)
    protocolo_origem_normalizado = models.CharField(max_length=64, blank=True, default="", db_index=True)
    cliente_origem = models.CharField(max_length=255, blank=True, default="")
    workflow_origem = models.CharField(max_length=255, blank=True, default="", db_index=True)
    nivel_hierarquico_origem = models.CharField(max_length=255, blank=True, default="")
    data_cadastro_origem = models.DateTimeField(null=True, blank=True)
    tipo_conclusao_analise_origem = models.CharField(max_length=255, blank=True, default="")
    ingestion = models.ForeignKey(
        "common.BotDataIngestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicados",
    )

    class Meta:
        db_table = "replicacao_d1_replicado"
        ordering = ["-report_date", "protocolo_origem"]
        indexes = [
            models.Index(
                fields=["report_date", "protocolo_origem"],
                name="replicacao_d1_rep_data_prot",
            ),
            models.Index(
                fields=["report_date", "workflow_origem"],
                name="replicacao_d1_rep_data_wf",
            ),
            models.Index(
                fields=["report_date", "protocolo_origem_normalizado"],
                name="rep_d1_rep_data_norm",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.report_date} / {self.protocolo_origem}"


class ReplicacaoD1SyncLog(models.Model):
    TRIGGER_USER = "user"
    TRIGGER_SYSTEM = "system"
    TRIGGER_CHOICES = [
        (TRIGGER_USER, "Usuário (portal/manual)"),
        (TRIGGER_SYSTEM, "Sistema (automático)"),
    ]

    KIND_PLANO = "plano"
    KIND_REPLICADOS = "replicados"
    KIND_CHOICES = [
        (KIND_PLANO, "Plano (Excel)"),
        (KIND_REPLICADOS, "Replicados BRFlow"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    trigger_source = models.CharField(
        max_length=20,
        choices=TRIGGER_CHOICES,
        default=TRIGGER_USER,
        db_index=True,
    )
    kind = models.CharField(
        max_length=16,
        choices=KIND_CHOICES,
        default=KIND_PLANO,
        db_index=True,
    )
    success = models.BooleanField(default=False)
    message = models.TextField(blank=True, default="")
    source_file = models.CharField(max_length=1024, blank=True, default="")
    source_mtime = models.FloatField(null=True, blank=True)
    source_size = models.BigIntegerField(null=True, blank=True)
    run_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    report_date = models.DateField(null=True, blank=True, db_index=True)
    row_count = models.PositiveIntegerField(null=True, blank=True)
    rows_read = models.PositiveIntegerField(null=True, blank=True)
    rows_loaded = models.PositiveIntegerField(null=True, blank=True)
    rows_rejected = models.PositiveIntegerField(null=True, blank=True)
    ingestion = models.ForeignKey(
        "common.BotDataIngestion",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sync_logs",
    )

    class Meta:
        db_table = "replicacao_d1_sync_log"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        src = "auto" if self.trigger_source == self.TRIGGER_SYSTEM else "manual"
        return f"Replicacao D-1 sync {self.started_at} [{src}/{self.kind}] ({'OK' if self.success else 'FAIL'})"


class ReplicacaoD1Reconciliacao(models.Model):
    """Vínculo determinístico entre protocolo planejado e confirmação."""

    STATUS_MATCHED = "matched"
    STATUS_MISSING = "missing"
    STATUS_AMBIGUOUS = "ambiguous"
    STATUS_DIVERGENT = "divergent"
    STATUS_IGNORED = "ignored"
    STATUS_CHOICES = [
        (STATUS_MATCHED, "Matched"),
        (STATUS_MISSING, "Missing"),
        (STATUS_AMBIGUOUS, "Ambiguous"),
        (STATUS_DIVERGENT, "Divergent"),
        (STATUS_IGNORED, "Ignored"),
    ]

    METHOD_RUN_ID = "run_id"
    METHOD_EXACT_DATE = "exact_date"
    METHOD_DATE_WINDOW = "date_window"
    METHOD_MANUAL = "manual"
    METHOD_CHOICES = [
        (METHOD_RUN_ID, "Run ID"),
        (METHOD_EXACT_DATE, "Data exata"),
        (METHOD_DATE_WINDOW, "Janela de datas"),
        (METHOD_MANUAL, "Manual"),
    ]

    protocolo = models.ForeignKey(
        ReplicacaoD1Protocolo,
        on_delete=models.CASCADE,
        related_name="reconciliacoes",
    )
    replicado = models.ForeignKey(
        ReplicacaoD1Replicado,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reconciliacoes",
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, db_index=True)
    method = models.CharField(max_length=32, choices=METHOD_CHOICES, default=METHOD_DATE_WINDOW)
    motivo = models.CharField(max_length=255, blank=True, default="")
    regra_version = models.CharField(max_length=16, default="1")
    reconciliado_em = models.DateTimeField(auto_now=True)
    ajuste_manual_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replicacao_d1_reconciliacoes",
    )

    class Meta:
        db_table = "replicacao_d1_reconciliacao"
        ordering = ["-reconciliado_em"]
        constraints = [
            models.UniqueConstraint(
                fields=["protocolo", "regra_version"],
                name="replicacao_d1_reconc_prot_regra_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "reconciliado_em"], name="replicacao_d1_reconc_st_em"),
            models.Index(
                fields=["regra_version", "status", "protocolo"],
                name="rep_d1_rec_rule_st_prot",
            ),
            models.Index(
                fields=["regra_version", "status", "replicado"],
                name="rep_d1_rec_rule_st_rep",
            ),
        ]

    def __str__(self) -> str:
        return f"Reconc {self.protocolo_id} -> {self.status}"


from apps.replicacao_d1.config_models import (  # noqa: E402,F401
    ReplicacaoD1Categoria,
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigGeral,
    ReplicacaoD1ConfigHistorico,
    ReplicacaoD1ConfigSnapshot,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1MetaMensal,
    ReplicacaoD1RetroativoCliente,
    ReplicacaoD1RetroativoConfig,
    ReplicacaoD1RetroativoWorkflow,
    ReplicacaoD1Segmento,
    ReplicacaoD1Workflow,
)
