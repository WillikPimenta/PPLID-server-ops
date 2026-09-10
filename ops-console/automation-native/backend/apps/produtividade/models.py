from django.conf import settings
from django.db import models


SOURCE_BRFLOW = "brflow"
SOURCE_CASE = "case"
SOURCE_CHOICES = [
    (SOURCE_BRFLOW, "BRFlow HxH"),
    (SOURCE_CASE, "Case Manager"),
]

CASE_ETAPA = "Análise Visual - GA - Case Manager"
CASE_STAGE_GOAL = 320


class ProductivityRecord(models.Model):
    """Registro de produtividade HxH (BRFlow e/ou Case Manager)."""

    matricula_norm = models.CharField(max_length=64, db_index=True)
    agent = models.ForeignKey(
        "workforce.Agent",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="productivity_records",
    )
    etapa = models.CharField(max_length=255, db_index=True)
    analysis_seconds = models.PositiveIntegerField(default=0)
    analysis_count = models.PositiveIntegerField(default=0)
    stage_goal = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    recorded_at = models.DateTimeField(db_index=True)
    agent_name = models.CharField(max_length=255, blank=True, default="")
    team = models.CharField(max_length=255, blank=True, default="", db_index=True)
    location = models.CharField(max_length=255, blank=True, default="", db_index=True)
    journey_shift = models.CharField(max_length=64, blank=True, default="", db_index=True)
    leader_name = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(
        max_length=16,
        choices=SOURCE_CHOICES,
        default=SOURCE_BRFLOW,
        db_index=True,
    )

    class Meta:
        db_table = "produtividade_record"
        ordering = ["-recorded_at", "matricula_norm"]
        indexes = [
            models.Index(fields=["etapa", "-recorded_at"]),
            models.Index(fields=["team", "-recorded_at"]),
            models.Index(fields=["source", "etapa", "-recorded_at"]),
        ]

    def __str__(self):
        return f"{self.matricula_norm} — {self.etapa} @ {self.recorded_at}"


class ProductivitySyncLog(models.Model):
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
    source_file = models.CharField(max_length=500, blank=True, default="")
    source_mtime = models.FloatField(null=True, blank=True)
    source_size = models.BigIntegerField(null=True, blank=True)
    row_count = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "produtividade_sync_log"
        ordering = ["-started_at"]

    def __str__(self):
        src = "auto" if self.trigger_source == self.TRIGGER_SYSTEM else "manual"
        return f"Prod sync {self.started_at} [{src}] ({'OK' if self.success else 'FAIL'})"
