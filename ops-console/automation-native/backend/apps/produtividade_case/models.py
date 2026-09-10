# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import models

from apps.produtividade_case.constants import (
    REPORT_CONSOLIDADO,
    REPORT_FILA_ABERTA,
    REPORT_PROD_HORA,
    REPORT_TEMPO_LOGADO,
)

REPORT_TYPE_CHOICES = [
    (REPORT_CONSOLIDADO, "Consolidado"),
    (REPORT_PROD_HORA, "Produtividade por hora"),
    (REPORT_TEMPO_LOGADO, "Tempo logado (só Excel)"),
    (REPORT_FILA_ABERTA, "Fila em aberto"),
]


class ProdutividadeCaseSyncLog(models.Model):
    TRIGGER_USER = "user"
    TRIGGER_SYSTEM = "system"
    TRIGGER_CHOICES = [
        (TRIGGER_USER, "Usuário (portal/manual)"),
        (TRIGGER_SYSTEM, "Sistema (automático)"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    report_type = models.CharField(
        max_length=32,
        choices=REPORT_TYPE_CHOICES,
        db_index=True,
        default=REPORT_CONSOLIDADO,
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
    success = models.BooleanField(default=False)
    message = models.TextField(blank=True, default="")
    source_file = models.CharField(max_length=1024, blank=True, default="")
    source_mtime = models.FloatField(null=True, blank=True)
    source_size = models.BigIntegerField(null=True, blank=True)
    row_count = models.PositiveIntegerField(null=True, blank=True)
    periodo_mes = models.CharField(max_length=16, blank=True, default="", db_index=True)

    class Meta:
        db_table = "produtividade_case_sync_log"
        ordering = ["-started_at"]

    def __str__(self):
        src = "auto" if self.trigger_source == self.TRIGGER_SYSTEM else "manual"
        return (
            f"Case sync {self.report_type} {self.started_at} "
            f"[{src}] ({'OK' if self.success else 'FAIL'})"
        )


class CaseFilaSnapshot(models.Model):
    """Cabeçalho do snapshot horário da fila Case (sem blockedDate)."""

    captured_at = models.DateTimeField(db_index=True)
    total_abertos = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=True, db_index=True)
    source_file = models.CharField(max_length=1024, blank=True, default="")
    duration_seconds = models.FloatField(null=True, blank=True)
    message = models.TextField(blank=True, default="")
    # Pré-cálculo no sync (null = snapshot antigo; ler amostra só como fallback)
    aging_count = models.PositiveIntegerField(null=True, blank=True)
    aging_medio_seconds = models.FloatField(null=True, blank=True)
    aging_mediano_seconds = models.FloatField(null=True, blank=True)
    aging_p90_seconds = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "case_manager_fila_snapshot"
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["success", "-captured_at"]),
        ]

    def __str__(self):
        return f"Case fila {self.captured_at} total={self.total_abertos}"


class CaseFilaAgg(models.Model):
    """Agregados do snapshot (status / idade / request_type)."""

    DIM_STATUS = "status"
    DIM_IDADE = "idade_bucket"
    DIM_REQUEST_TYPE = "request_type"
    DIMENSION_CHOICES = [
        (DIM_STATUS, "Status"),
        (DIM_IDADE, "Idade na fila"),
        (DIM_REQUEST_TYPE, "Tipo de request"),
    ]

    snapshot = models.ForeignKey(
        CaseFilaSnapshot,
        on_delete=models.CASCADE,
        related_name="aggs",
    )
    dimension = models.CharField(max_length=32, choices=DIMENSION_CHOICES, db_index=True)
    key = models.CharField(max_length=64, db_index=True)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "case_manager_fila_agg"
        ordering = ["dimension", "-count", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "dimension", "key"],
                name="uniq_case_fila_agg_snap_dim_key",
            )
        ]
        indexes = [
            models.Index(fields=["snapshot", "dimension"]),
        ]

    def __str__(self):
        return f"{self.dimension}={self.key}:{self.count}"


class CaseConsolidadoSnapshot(models.Model):
    """Cabeçalho do reload mensal de agregados do consolidado Case."""

    periodo_mes = models.CharField(max_length=16, db_index=True)
    captured_at = models.DateTimeField(db_index=True)
    total_protocolos = models.PositiveIntegerField(default=0)
    success = models.BooleanField(default=True, db_index=True)
    source_file = models.CharField(max_length=1024, blank=True, default="")
    duration_seconds = models.FloatField(null=True, blank=True)
    message = models.TextField(blank=True, default="")
    # {workflow: {mediana_seconds, p90_seconds, horas_consumidas, tma_seconds, ...}}
    tempo_stats_json = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "case_manager_consolidado_snapshot"
        ordering = ["-captured_at"]
        indexes = [
            models.Index(fields=["periodo_mes", "-captured_at"]),
            models.Index(fields=["success", "-captured_at"]),
        ]

    def __str__(self):
        return (
            f"Case consolidado {self.periodo_mes} "
            f"total={self.total_protocolos}"
        )


class CaseConsolidadoDailyAgg(models.Model):
    """Agregados diários do consolidado (workflow, agente, resultado, …)."""

    snapshot = models.ForeignKey(
        CaseConsolidadoSnapshot,
        on_delete=models.CASCADE,
        related_name="daily_aggs",
    )
    periodo_mes = models.CharField(max_length=16, db_index=True)
    day = models.DateField(db_index=True)
    dimension = models.CharField(max_length=48, db_index=True)
    key = models.CharField(max_length=255, db_index=True)
    count = models.PositiveIntegerField(default=0)
    analysis_seconds_sum = models.PositiveBigIntegerField(default=0)

    class Meta:
        db_table = "case_manager_consolidado_daily_agg"
        ordering = ["day", "dimension", "-count", "key"]
        constraints = [
            models.UniqueConstraint(
                fields=["snapshot", "day", "dimension", "key"],
                name="uniq_case_cons_daily_snap_day_dim_key",
            )
        ]
        indexes = [
            models.Index(fields=["periodo_mes", "dimension", "day"]),
            models.Index(fields=["dimension", "key"]),
        ]

    def __str__(self):
        return f"{self.day} {self.dimension}={self.key}:{self.count}"


class CaseConsolidadoFact(models.Model):
    """Linha de protocolo do consolidado (sem CPF) para drill-down."""

    snapshot = models.ForeignKey(
        CaseConsolidadoSnapshot,
        on_delete=models.CASCADE,
        related_name="facts",
    )
    periodo_mes = models.CharField(max_length=16, db_index=True)
    protocolo_destino = models.CharField(max_length=128, db_index=True)
    protocolo_origem = models.CharField(max_length=128, blank=True, default="")
    workflow_origem = models.CharField(max_length=255, blank=True, default="", db_index=True)
    nh_origem = models.CharField(max_length=64, blank=True, default="", db_index=True)
    cliente_origem = models.CharField(max_length=255, blank=True, default="", db_index=True)
    matricula_origem = models.CharField(max_length=64, blank=True, default="", db_index=True)
    matricula_destino = models.CharField(max_length=64, blank=True, default="", db_index=True)
    resultado_origem = models.CharField(max_length=128, blank=True, default="", db_index=True)
    resultado_destino = models.CharField(max_length=128, blank=True, default="", db_index=True)
    status_destino = models.CharField(max_length=64, blank=True, default="")
    tipo_conclusao_origem = models.CharField(max_length=32, blank=True, default="")
    alertas_destino = models.CharField(max_length=500, blank=True, default="")
    cadastro_origem_at = models.DateTimeField(null=True, blank=True)
    cadastro_destino_at = models.DateTimeField(null=True, blank=True, db_index=True)
    conclusao_destino_at = models.DateTimeField(null=True, blank=True, db_index=True)
    inspecao_at = models.DateTimeField(null=True, blank=True)
    tempo_analise_segundos = models.PositiveIntegerField(null=True, blank=True)
    source_file = models.CharField(max_length=1024, blank=True, default="")

    class Meta:
        db_table = "case_manager_consolidado_fact"
        ordering = ["-conclusao_destino_at", "protocolo_destino"]
        indexes = [
            models.Index(fields=["periodo_mes", "workflow_origem"]),
            models.Index(fields=["periodo_mes", "matricula_destino"]),
            models.Index(fields=["periodo_mes", "conclusao_destino_at"]),
            models.Index(fields=["periodo_mes", "cadastro_destino_at"]),
        ]

    def __str__(self):
        return f"{self.protocolo_destino} ({self.periodo_mes})"


class CaseFilaSampleItem(models.Model):
    """Protocolos em aberto no snapshot da fila (lista completa do sync)."""

    snapshot = models.ForeignKey(
        CaseFilaSnapshot,
        on_delete=models.CASCADE,
        related_name="sample_items",
    )
    protocolo_id = models.CharField(max_length=128, db_index=True)
    protocolo_origem = models.CharField(max_length=128, blank=True, default="", db_index=True)
    transaction_status = models.CharField(max_length=64, blank=True, default="")
    idade_bucket = models.CharField(max_length=16, blank=True, default="", db_index=True)
    created_ts = models.DateTimeField(null=True, blank=True)
    cadastro_origem_at = models.DateTimeField(null=True, blank=True, db_index=True)
    workflow_origem = models.CharField(max_length=255, blank=True, default="")
    cliente_origem = models.CharField(
        max_length=255, blank=True, default="", db_index=True
    )

    class Meta:
        db_table = "case_manager_fila_sample_item"
        ordering = ["created_ts", "protocolo_id"]
        indexes = [
            models.Index(fields=["snapshot", "idade_bucket"]),
            models.Index(fields=["snapshot", "cliente_origem"]),
        ]

    def __str__(self):
        return f"{self.protocolo_id} ({self.idade_bucket})"
