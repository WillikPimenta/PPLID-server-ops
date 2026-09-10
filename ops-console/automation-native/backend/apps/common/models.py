# -*- coding: utf-8 -*-
from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone


class BotDbSyncJob(models.Model):
    """Fila de sync bot→banco processada fora do worker HTTP."""

    DOMAIN_PRODUTIVIDADE = "produtividade"
    DOMAIN_MONITOR_EVENTOS = "monitor_eventos"
    DOMAIN_ROTINA_BRUTO = "rotina_bruto"
    DOMAIN_FALHAS_CRITICAS = "falhas_criticas"
    DOMAIN_REPLICACAO_D1 = "replicacao_d1"
    DOMAIN_PRODUTIVIDADE_CASE = "produtividade_case"
    DOMAIN_PRIORIDADES_NH = "prioridades_nh"
    DOMAIN_MONITORAMENTO_SLA = "monitoramento_sla"
    DOMAIN_REINSPECAO_GED = "reinspecao_ged"
    DOMAIN_QUALIDADE_PROJECTION = "qualidade_projection"
    DOMAIN_CHOICES = [
        (DOMAIN_PRODUTIVIDADE, "Produtividade"),
        (DOMAIN_MONITOR_EVENTOS, "Monitor eventos"),
        (DOMAIN_ROTINA_BRUTO, "Rotina bruto"),
        (DOMAIN_FALHAS_CRITICAS, "Falhas críticas"),
        (DOMAIN_REPLICACAO_D1, "Replicação D-1"),
        (DOMAIN_PRODUTIVIDADE_CASE, "Produtividade Case Manager"),
        (DOMAIN_PRIORIDADES_NH, "Prioridades por nível hierárquico"),
        (DOMAIN_MONITORAMENTO_SLA, "Monitoramento SLA útil"),
        (DOMAIN_REINSPECAO_GED, "Reinspeção GED irregularidade"),
        (DOMAIN_QUALIDADE_PROJECTION, "Projeção da Qualidade"),
    ]

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_DONE = "done"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pendente"),
        (STATUS_RUNNING, "Em execução"),
        (STATUS_DONE, "Concluído"),
        (STATUS_FAILED, "Falhou"),
        (STATUS_SKIPPED, "Ignorado"),
    ]

    LANE_HIGH = "high"
    LANE_MIDDLE = "middle"
    LANE_MID = LANE_MIDDLE
    LANE_LOW = "low"
    LANE_CHOICES = [
        (LANE_HIGH, "Alta (pesado)"),
        (LANE_MID, "Média (Projeção da Qualidade)"),
        (LANE_LOW, "Baixa (leve)"),
    ]
    LANE_CHOICES = [
        (LANE_HIGH, "Alta (pesado)"),
        (LANE_MIDDLE, "M\u00e9dia (peso m\u00e9dio)"),
        (LANE_LOW, "Baixa (leve)"),
    ]

    domain = models.CharField(max_length=32, choices=DOMAIN_CHOICES, db_index=True)
    report_type = models.CharField(max_length=64, blank=True, default="")
    source_path = models.CharField(max_length=1024, blank=True, default="")
    force = models.BooleanField(default=True)
    lane = models.CharField(
        max_length=8,
        choices=LANE_CHOICES,
        default=LANE_LOW,
        db_index=True,
        help_text="Filas high, middle e low; processadas em paralelo.",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    message = models.TextField(blank=True, default="")
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "common_bot_db_sync_job"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["status", "created_at"], name="bot_db_sync_status_created"),
            models.Index(
                fields=["lane", "status", "created_at"],
                name="bot_db_sync_lane_status",
            ),
            models.Index(
                fields=["domain", "report_type", "source_path", "status"],
                name="bot_db_sync_dedupe",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"BotDbSyncJob#{self.pk} {self.domain}/{self.report_type or '-'} "
            f"[{self.status}/{self.lane}]"
        )


class BotDbSyncRuntimeConfig(models.Model):
    """Singleton: knobs de sync bot→banco editáveis pelo painel operacional."""

    SINGLETON_PK = 1

    chunk_size = models.PositiveIntegerField(default=2000)
    batch_size = models.PositiveIntegerField(default=2000)
    stale_minutes = models.PositiveIntegerField(default=45)
    drain_max_jobs = models.PositiveIntegerField(default=10)
    queue_wait_s = models.PositiveIntegerField(default=900)
    high_concurrency = models.PositiveSmallIntegerField(default=1)
    mid_concurrency = models.PositiveSmallIntegerField(default=1)
    low_concurrency = models.PositiveSmallIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "common_bot_db_sync_runtime_config"

    def __str__(self) -> str:
        return (
            f"BotDbSyncRuntimeConfig chunk={self.chunk_size} batch={self.batch_size} "
            f"stale={self.stale_minutes}m drain_max={self.drain_max_jobs}"
        )

    def save(self, *args, **kwargs):
        self.pk = self.SINGLETON_PK
        super().save(*args, **kwargs)


class BotDbSyncLockLease(models.Model):
    """Lease metadata for a PostgreSQL advisory lock held by a bot sync."""

    lane = models.CharField(max_length=8, unique=True)
    owner_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    backend_pid = models.PositiveIntegerField(null=True, blank=True)
    process_pid = models.PositiveIntegerField(null=True, blank=True)
    label = models.CharField(max_length=128, blank=True, default="")
    acquired_at = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "common_bot_db_sync_lock_lease"

    def __str__(self) -> str:
        return f"BotDbSyncLockLease {self.lane} {self.owner_token}"


class BotDataArtifact(models.Model):
    """Identidade lógica de um arquivo/conteúdo ingerido (independente de tentativas)."""

    domain = models.CharField(max_length=32, db_index=True)
    kind = models.CharField(max_length=32, db_index=True)
    semantic_key = models.CharField(max_length=128, db_index=True)
    safe_name = models.CharField(max_length=255)
    content_sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)
    content_size = models.BigIntegerField(null=True, blank=True)
    reference_date = models.DateField(null=True, blank=True, db_index=True)
    legacy_unverified = models.BooleanField(default=False)
    discovered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "common_bot_data_artifact"
        ordering = ["-discovered_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["domain", "kind", "content_sha256"],
                condition=models.Q(content_sha256__gt=""),
                name="bot_artifact_domain_kind_hash_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["domain", "kind", "semantic_key"],
                name="bot_artifact_domain_kind_key",
            ),
        ]

    def __str__(self) -> str:
        return f"BotDataArtifact#{self.pk} {self.domain}/{self.kind}"


class BotDataIngestion(models.Model):
    """Lote de dados persistido após ingestão bot→banco (metadados, não segunda fila)."""

    STATUS_RECEIVED = "received"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_PARTIAL = "partial"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "Recebido"),
        (STATUS_PROCESSING, "Processando"),
        (STATUS_COMPLETED, "Concluído"),
        (STATUS_PARTIAL, "Parcial"),
        (STATUS_FAILED, "Falhou"),
    ]

    # Chave legada/diagnóstica. A identidade da tentativa é artefato + número;
    # retries nunca devem sobrescrever uma tentativa anterior por source_key.
    source_key = models.CharField(max_length=128, db_index=True)
    artifact = models.ForeignKey(
        BotDataArtifact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ingestions",
    )
    attempt_number = models.PositiveIntegerField(default=1)
    parser_version = models.CharField(max_length=32, blank=True, default="")
    domain = models.CharField(max_length=32, db_index=True)
    kind = models.CharField(max_length=32, db_index=True)
    reference_date = models.DateField(null=True, blank=True, db_index=True)
    run_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    source_file = models.CharField(max_length=1024, blank=True, default="")
    source_mtime = models.FloatField(null=True, blank=True)
    source_size = models.BigIntegerField(null=True, blank=True)
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_RECEIVED,
        db_index=True,
    )
    rows_read = models.PositiveIntegerField(default=0)
    rows_loaded = models.PositiveIntegerField(default=0)
    rows_rejected = models.PositiveIntegerField(default=0)
    error_summary = models.TextField(blank=True, default="")
    sync_job = models.ForeignKey(
        BotDbSyncJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ingestions",
    )
    sync_log_id = models.PositiveIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "common_bot_data_ingestion"
        ordering = ["-started_at"]
        indexes = [
            models.Index(
                fields=["domain", "kind", "reference_date"],
                name="bot_ingest_domain_kind_date",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["artifact", "attempt_number"],
                condition=models.Q(artifact__isnull=False),
                name="bot_ingest_artifact_attempt_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"BotDataIngestion#{self.pk} {self.domain}/{self.kind} [{self.status}]"
