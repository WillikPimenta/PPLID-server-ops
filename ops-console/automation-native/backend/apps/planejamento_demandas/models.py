"""Espelho local de issues Jira para fila operacional do Planejamento."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class JiraDemanda(models.Model):
    CATEGORIA_HEADCOUNT = "headcount"
    CATEGORIA_OCORRENCIAS = "ocorrencias"
    CATEGORIA_ESCALA = "escala_trocas"
    CATEGORIA_REGRAS = "regras_workflow"
    CATEGORIA_REPLICACAO = "replicacao"
    CATEGORIA_MEGAZORD = "megazord"
    CATEGORIA_INTRANET_BI = "intranet_bi"
    CATEGORIA_CONFER = "confer"
    CATEGORIA_BRFLOW = "brflow"
    CATEGORIA_SUPORTE = "suporte_claro"
    CATEGORIA_OUTRO = "outro"
    STATUS_KIND_OPEN = "open"
    STATUS_KIND_DONE = "done"
    STATUS_KIND_CANCELLED = "cancelled"
    STATUS_KIND_CHOICES = [
        (STATUS_KIND_OPEN, "Aberta"),
        (STATUS_KIND_DONE, "Concluída"),
        (STATUS_KIND_CANCELLED, "Cancelada"),
    ]
    CATEGORIA_CHOICES = [
        (CATEGORIA_HEADCOUNT, "Headcount"),
        (CATEGORIA_OCORRENCIAS, "Ocorrências"),
        (CATEGORIA_ESCALA, "Escala / trocas / volumetria"),
        (CATEGORIA_REGRAS, "Regras de workflow"),
        (CATEGORIA_REPLICACAO, "Replicação / migração"),
        (CATEGORIA_MEGAZORD, "Megazord / metas / SLA"),
        (CATEGORIA_INTRANET_BI, "Intranet / BI"),
        (CATEGORIA_CONFER, "Confer"),
        (CATEGORIA_BRFLOW, "BrFlow / NH"),
        (CATEGORIA_SUPORTE, "Suporte Claro"),
        (CATEGORIA_OUTRO, "Outros"),
    ]

    issue_key = models.CharField(max_length=32, unique=True, db_index=True)
    project_key = models.CharField(max_length=32, db_index=True)
    project_name = models.CharField(max_length=128, blank=True, default="")
    summary = models.CharField(max_length=512)
    description_excerpt = models.TextField(blank=True, default="")
    status_name = models.CharField(max_length=128, db_index=True)
    status_kind = models.CharField(
        max_length=16,
        choices=STATUS_KIND_CHOICES,
        default=STATUS_KIND_OPEN,
        db_index=True,
    )
    is_open = models.BooleanField(default=True, db_index=True)
    assignee_display = models.CharField(max_length=255, blank=True, default="", db_index=True)
    assignee_username = models.CharField(max_length=128, blank=True, default="", db_index=True)
    reporter_display = models.CharField(max_length=255, blank=True, default="")
    reporter_username = models.CharField(max_length=128, blank=True, default="", db_index=True)
    in_team_queue = models.BooleanField(default=False, db_index=True)
    priority_name = models.CharField(max_length=64, blank=True, default="")
    issue_type = models.CharField(max_length=128, blank=True, default="")
    labels = models.JSONField(default=list, blank=True)
    components = models.JSONField(default=list, blank=True)
    categoria = models.CharField(
        max_length=32,
        choices=CATEGORIA_CHOICES,
        default=CATEGORIA_OUTRO,
        db_index=True,
    )
    portal_path = models.CharField(max_length=255, blank=True, default="")
    jira_url = models.URLField(max_length=512, blank=True, default="")
    created_at_jira = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at_jira = models.DateTimeField(null=True, blank=True, db_index=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "planejamento_jira_demanda"
        ordering = ["-updated_at_jira", "-issue_key"]
        indexes = [
            models.Index(fields=["project_key", "is_open", "-updated_at_jira"]),
            models.Index(fields=["categoria", "is_open"]),
            models.Index(fields=["in_team_queue", "is_open"]),
            models.Index(fields=["assignee_username", "is_open"]),
            models.Index(
                fields=["project_key", "status_kind", "is_open"],
                name="planejamen_project_6a8f0d_idx",
            ),
        ]

    def __str__(self) -> str:
        return self.issue_key


class JiraDemandaSyncRun(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_INTERRUPTED = "interrupted"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Na fila"),
        (STATUS_RUNNING, "Em execução"),
        (STATUS_SUCCESS, "Sucesso"),
        (STATUS_FAILED, "Falha"),
        (STATUS_CANCELLED, "Cancelada"),
        (STATUS_INTERRUPTED, "Interrompida"),
    ]

    MODE_FULL = "full"
    MODE_INCREMENTAL = "incremental"
    MODE_CHOICES = [
        (MODE_FULL, "Completa"),
        (MODE_INCREMENTAL, "Incremental"),
    ]

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default=MODE_FULL)
    projects = models.CharField(max_length=255, blank=True, default="")
    jql = models.TextField(blank=True, default="")
    total_fetched = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    duplicate_count = models.PositiveIntegerField(default=0)
    pages_processed = models.PositiveIntegerField(default=0)
    phase = models.CharField(max_length=32, blank=True, default="")
    phase_index = models.PositiveSmallIntegerField(default=0)
    phase_count = models.PositiveSmallIntegerField(default=0)
    phase_fetched = models.PositiveIntegerField(default=0)
    phase_total = models.PositiveIntegerField(default=0)
    progress_percent = models.FloatField(default=0)
    message = models.TextField(blank=True, default="")
    error_code = models.CharField(max_length=64, blank=True, default="")
    duration_seconds = models.FloatField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    retry_of = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retries",
    )
    started_at = models.DateTimeField(auto_now_add=True)
    heartbeat_at = models.DateTimeField(null=True, blank=True, db_index=True)
    checkpoint_at = models.DateTimeField(null=True, blank=True)
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "planejamento_jira_demanda_sync_run"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"{self.pk} {self.status} ({self.projects})"

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            self.STATUS_SUCCESS,
            self.STATUS_FAILED,
            self.STATUS_CANCELLED,
            self.STATUS_INTERRUPTED,
        }
