# -*- coding: utf-8 -*-
from django.db import models
from django.conf import settings


class BrbReportGeneration(models.Model):
    """Histórico de gerações Quality Pulse (Fase 2 operação)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="brb_report_generations",
    )
    client_slug = models.CharField(max_length=64, default="brb")
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    storage_key = models.CharField(max_length=64, unique=True)
    artifacts = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Geração Relatório BRB"
        verbose_name_plural = "Gerações Relatório BRB"

    def __str__(self) -> str:
        return f"{self.client_slug} {self.period_start}–{self.period_end} ({self.storage_key})"


class BrbCsDataSource(models.Model):
    """Base atualizada para a Visão CS, sem geração de artefatos."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="brb_cs_data_sources",
    )
    client_slug = models.CharField(max_length=64, db_index=True)
    storage_key = models.CharField(max_length=64, unique=True)
    original_filename = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Base da Visão CS"
        verbose_name_plural = "Bases da Visão CS"

    def __str__(self) -> str:
        return f"{self.client_slug} ({self.storage_key})"


class ReportSupplementImportBatch(models.Model):
    """Rastreio de cargas NA/Treinamentos via Excel suplemento."""

    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_COMPLETED, "Concluído"),
        (STATUS_FAILED, "Falhou"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_supplement_imports",
    )
    source_filename = models.CharField(max_length=255, blank=True, default="")
    rows_na_demanda = models.PositiveIntegerField(default=0)
    rows_na_falha = models.PositiveIntegerField(default=0)
    rows_treinamento = models.PositiveIntegerField(default=0)
    rows_treinamento_horas = models.PositiveIntegerField(default=0)
    upserted = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_COMPLETED)
    detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Import suplemento report"
        verbose_name_plural = "Imports suplemento report"

    def __str__(self) -> str:
        return f"Import {self.source_filename or self.pk} ({self.status})"


class ReportNaDemanda(models.Model):
    id_cliente = models.IntegerField(db_index=True)
    demanda = models.CharField(max_length=512, blank=True, default="")
    data_abertura = models.DateField(null=True, blank=True, db_index=True)
    data_retorno = models.DateField(null=True, blank=True)
    mes = models.CharField(max_length=32, blank=True, default="")
    quantidade_protocolos = models.IntegerField(default=0)
    falhas_manuais = models.IntegerField(default=0)
    falhas_processuais = models.IntegerField(default=0)
    falhas_automaticas = models.IntegerField(default=0)
    situacao = models.CharField(max_length=128, blank=True, default="")
    cliente_label = models.CharField(max_length=255, blank=True, default="")
    import_batch = models.ForeignKey(
        ReportSupplementImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="na_demandas",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_na_demanda"
        constraints = [
            models.UniqueConstraint(
                fields=["id_cliente", "demanda", "data_abertura"],
                name="report_na_dem_cli_dem_ab",
            ),
        ]
        indexes = [
            models.Index(fields=["id_cliente", "data_abertura"], name="report_na_dem_cli_dt"),
        ]

    def __str__(self) -> str:
        return f"NA {self.demanda} ({self.data_abertura})"


class ReportNaFalha(models.Model):
    id_cliente = models.IntegerField(db_index=True)
    protocolo = models.CharField(max_length=100, blank=True, default="")
    protocolo_norm = models.CharField(max_length=100, blank=True, default="", db_index=True)
    matricula = models.CharField(max_length=64, blank=True, default="")
    matricula_norm = models.CharField(max_length=64, blank=True, default="", db_index=True)
    data_cadastro = models.DateField(null=True, blank=True, db_index=True)
    data_notificacao = models.DateField(null=True, blank=True, db_index=True)
    motivo_falha = models.TextField(blank=True, default="")
    resultado_cliente = models.CharField(max_length=256, blank=True, default="")
    resultado_auditoria = models.CharField(max_length=256, blank=True, default="")
    demanda = models.CharField(max_length=512, blank=True, default="")
    cliente_label = models.CharField(max_length=255, blank=True, default="")
    import_batch = models.ForeignKey(
        ReportSupplementImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="na_falhas",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_na_falha"
        constraints = [
            models.UniqueConstraint(
                fields=["id_cliente", "protocolo_norm", "matricula_norm"],
                name="report_na_fal_cli_prot_mat",
            ),
        ]
        indexes = [
            models.Index(fields=["id_cliente", "data_notificacao"], name="report_na_fal_cli_not"),
        ]

    def __str__(self) -> str:
        return f"NA falha {self.protocolo} ({self.data_notificacao})"


class ReportTreinamento(models.Model):
    id_cliente = models.IntegerField(db_index=True)
    event_title = models.CharField(max_length=512, blank=True, default="")
    matricula = models.CharField(max_length=64, blank=True, default="")
    matricula_norm = models.CharField(max_length=64, blank=True, default="", db_index=True)
    assignment_date = models.DateField(null=True, blank=True)
    signature_date = models.DateField(null=True, blank=True)
    session_start = models.DateField(null=True, blank=True, db_index=True)
    session_final = models.DateField(null=True, blank=True)
    horas = models.FloatField(null=True, blank=True)
    status = models.CharField(max_length=128, blank=True, default="")
    cliente_label = models.CharField(max_length=255, blank=True, default="")
    scope = models.CharField(max_length=16, blank=True, default="")
    import_batch = models.ForeignKey(
        ReportSupplementImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="treinamentos",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "report_treinamento"
        constraints = [
            models.UniqueConstraint(
                fields=["id_cliente", "matricula_norm", "event_title", "session_start"],
                name="report_tr_cli_mat_evt_dt",
            ),
        ]
        indexes = [
            models.Index(fields=["id_cliente", "session_start"], name="report_tr_cli_start"),
        ]

    def __str__(self) -> str:
        return f"Trein {self.event_title} ({self.session_start})"
