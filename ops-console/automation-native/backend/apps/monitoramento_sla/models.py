# -*- coding: utf-8 -*-
from __future__ import annotations

from django.conf import settings
from django.db import models


class SlaUtilSyncRun(models.Model):
    """Controle de carga / instante de corte (§7.3 / §19)."""

    STATUS_RUNNING = "running"
    STATUS_OK = "ok"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_RUNNING, "Em execução"),
        (STATUS_OK, "OK"),
        (STATUS_ERROR, "Erro"),
    ]

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    cutover_at = models.DateTimeField(help_text="Instante de corte para protocolos abertos")
    rows_detalhe = models.PositiveIntegerField(default=0)
    rows_consolidado = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True, default="")
    metrics = models.JSONField(default=dict, blank=True)
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sla_util_sync_runs",
    )

    class Meta:
        db_table = "sla_util_sync_run"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"SlaUtilSyncRun#{self.pk} {self.status}"


class SlaUtilDetalhe(models.Model):
    HORA_FONTE_REAL = "real"
    HORA_FONTE_INDISPONIVEL = "unavailable"
    HORA_FONTE_CHOICES = [
        (HORA_FONTE_REAL, "Horário real da origem"),
        (HORA_FONTE_INDISPONIVEL, "Horário indisponível"),
    ]

    """Fato detalhada (combine): 1 linha por protocolo × NH."""

    protocolo = models.CharField(max_length=64, db_index=True)
    source_key = models.CharField(max_length=640, unique=True)
    id_cliente = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    id_workflow = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    id_nh = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    cliente_nome = models.CharField(max_length=255, blank=True, default="")
    workflow_nome = models.CharField(max_length=255, blank=True, default="")
    nh_nome = models.CharField(max_length=255, blank=True, default="")

    data_cadastro = models.DateField(db_index=True)
    hora_cadastro = models.TimeField(null=True, blank=True)
    hora_cadastro_fonte = models.CharField(
        max_length=16,
        choices=HORA_FONTE_CHOICES,
        default=HORA_FONTE_REAL,
        db_index=True,
    )
    data_conclusao = models.DateField(null=True, blank=True, db_index=True)
    hora_conclusao = models.TimeField(null=True, blank=True)
    em_aberto = models.BooleanField(default=False, db_index=True)

    resultado = models.CharField(max_length=255, blank=True, default="")
    tipo_conclusao = models.CharField(max_length=255, blank=True, default="")
    avaliacao = models.CharField(max_length=64, blank=True, default="")

    date_key_cadastro = models.PositiveIntegerField(db_index=True)
    date_key_conclusao = models.PositiveIntegerField(null=True, blank=True)
    key_cwn = models.BigIntegerField(null=True, blank=True, db_index=True)
    chave_nh = models.CharField(max_length=128, blank=True, default="", db_index=True)

    sla_segundos = models.PositiveIntegerField(null=True, blank=True)
    data_vencimento_d2u = models.DateField(null=True, blank=True)
    sla_descricao_natural = models.CharField(max_length=16, blank=True, default="")
    sla_descricao_ajustado = models.CharField(max_length=16, blank=True, default="")
    faixa = models.CharField(max_length=64, blank=True, default="")

    problemas = models.JSONField(default=list, blank=True)
    source_report_date = models.DateField(null=True, blank=True, db_index=True)
    source_fingerprint = models.CharField(max_length=64, blank=True, default="")
    sync_run = models.ForeignKey(
        SlaUtilSyncRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="detalhes",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "sla_util_detalhe"
        ordering = ["-data_cadastro", "protocolo"]
        constraints = [
            models.UniqueConstraint(
                fields=["protocolo", "id_workflow", "id_nh"],
                name="uniq_sla_util_detalhe_prot_wf_nh",
            )
        ]
        indexes = [
            models.Index(fields=["data_cadastro", "id_cliente", "id_workflow"]),
            models.Index(fields=["sla_descricao_natural"]),
            models.Index(fields=["sla_descricao_ajustado"]),
            models.Index(fields=["em_aberto", "-data_cadastro"]),
            models.Index(
                fields=["data_cadastro", "protocolo", "id_workflow", "id_nh"],
                name="sla_det_cad_prot_wf_nh",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.protocolo} NH={self.id_nh} sla={self.sla_segundos}"


class SlaUtilConsolidado(models.Model):
    HORA_FONTE_REAL = "real"
    HORA_FONTE_INDISPONIVEL = "unavailable"
    HORA_FONTE_CHOICES = [
        (HORA_FONTE_REAL, "Horário real da origem"),
        (HORA_FONTE_INDISPONIVEL, "Horário indisponível"),
    ]

    """Fato consolidada diária (quantidade agregada)."""

    data_cadastro = models.DateField(db_index=True)
    hora_cadastro = models.TimeField(null=True, blank=True)
    hora_cadastro_fonte = models.CharField(
        max_length=16,
        choices=HORA_FONTE_CHOICES,
        default=HORA_FONTE_REAL,
        db_index=True,
    )
    data_conclusao = models.DateField(null=True, blank=True)
    hora_conclusao = models.TimeField(null=True, blank=True)
    id_cliente = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    id_workflow = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    id_nh = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    cliente_nome = models.CharField(max_length=255, blank=True, default="")
    workflow_nome = models.CharField(max_length=255, blank=True, default="")
    nh_nome = models.CharField(max_length=255, blank=True, default="")
    tipo_conclusao = models.CharField(max_length=255, blank=True, default="")
    resultado = models.CharField(max_length=255, blank=True, default="")
    sla_descricao_natural = models.CharField(max_length=16, blank=True, default="")
    sla_descricao_ajustado = models.CharField(max_length=16, blank=True, default="")
    faixa = models.CharField(max_length=64, blank=True, default="")
    avaliacao = models.CharField(max_length=64, blank=True, default="")
    quantidade = models.PositiveIntegerField(default=0)
    date_key_cadastro = models.PositiveIntegerField(db_index=True)
    sync_run = models.ForeignKey(
        SlaUtilSyncRun,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consolidados",
    )

    class Meta:
        db_table = "sla_util_consolidado"
        ordering = ["-data_cadastro"]
        indexes = [
            models.Index(fields=["data_cadastro", "id_cliente", "id_workflow"]),
        ]

    def __str__(self) -> str:
        return f"{self.data_cadastro} q={self.quantidade}"

