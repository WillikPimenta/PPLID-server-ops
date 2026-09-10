from django.conf import settings
from django.db import models


class MonitorEventoRecord(models.Model):
    """Sessão tratada do Monitor BRFlow (schema COLUNAS_MONITOR_UNIFICADO)."""

    data = models.DateField(db_index=True)
    hora = models.PositiveSmallIntegerField()
    matricula_usuario = models.CharField(max_length=64, db_index=True)
    data_evento = models.DateTimeField(db_index=True)
    evento = models.CharField(max_length=255, db_index=True)
    data_segundo_evento = models.DateTimeField(null=True, blank=True)
    segundo_evento = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "monitor_evento_record"
        ordering = ["-data_evento", "matricula_usuario"]
        indexes = [
            models.Index(fields=["evento", "-data_evento"]),
            models.Index(fields=["data", "hora"]),
        ]

    def __str__(self):
        return f"{self.matricula_usuario} — {self.evento} @ {self.data_evento}"


class MonitorEventoSyncLog(models.Model):
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
        db_table = "monitor_evento_sync_log"
        ordering = ["-started_at"]

    def __str__(self):
        src = "auto" if self.trigger_source == self.TRIGGER_SYSTEM else "manual"
        return f"Monitor sync {self.started_at} [{src}] ({'OK' if self.success else 'FAIL'})"
