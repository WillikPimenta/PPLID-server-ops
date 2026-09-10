import uuid

from django.db import models

from apps.workforce.models import Agent

from .schedule import ScheduleToday


class OccurrenceType(models.Model):
    """Catálogo de tipos de ocorrência operacional."""

    id = models.PositiveSmallIntegerField(primary_key=True)
    name = models.CharField("nome", max_length=128)
    color = models.CharField("cor hex", max_length=32, blank=True)
    active = models.BooleanField(default=True)
    observation = models.TextField("observação", blank=True)
    status_types = models.ManyToManyField(
        "escala_flex.StatusType",
        related_name="occurrence_types",
        blank=True,
        verbose_name="status vinculados",
    )
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_occurrence_type"
        ordering = ["name"]
        verbose_name = "tipo de ocorrência"
        verbose_name_plural = "tipos de ocorrência"

    def __str__(self):
        return self.name


class OperationalOccurrence(models.Model):
    """Registro de ocorrência operacional aguardando ou após aprovação."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField("data")
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="operational_occurrences",
    )
    schedule_today = models.ForeignKey(
        ScheduleToday,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_occurrences",
    )
    occurrence_type = models.ForeignKey(
        OccurrenceType,
        on_delete=models.PROTECT,
        related_name="occurrences",
    )
    forecast_seconds = models.PositiveIntegerField(
        "previsão (segundos)",
        help_text="Duração prevista em segundos (HH:MM).",
    )
    scheduled_time = models.TimeField(
        "horário planejado",
        null=True,
        blank=True,
        help_text="Horário em que o agente será retirado da operação.",
    )
    description = models.TextField("descrição", blank=True)
    cancelled = models.BooleanField(default=False)
    approved = models.BooleanField(null=True, blank=True)
    approved_by = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_operational_occurrences",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_operational_occurrences",
    )
    leader = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_operational_occurrences",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ef_operational_occurrence"
        indexes = [
            models.Index(fields=["date", "agent"]),
            models.Index(fields=["approved"]),
            models.Index(fields=["leader"]),
        ]
        ordering = ["-date", "agent__full_name"]

    def __str__(self):
        return f"{self.agent} — {self.occurrence_type} ({self.date})"


class OperationalOccurrenceExtension(models.Model):
    """Solicitação de tempo adicional para ocorrência em andamento."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurrence = models.ForeignKey(
        OperationalOccurrence,
        on_delete=models.CASCADE,
        related_name="extensions",
    )
    extra_seconds = models.PositiveIntegerField(
        "tempo adicional (segundos)",
        help_text="Tempo adicional solicitado em segundos (HH:MM).",
    )
    description = models.TextField("motivo", blank=True)
    approved = models.BooleanField(null=True, blank=True)
    approved_by = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_occurrence_extensions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_occurrence_extensions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ef_operational_occurrence_extension"
        indexes = [
            models.Index(fields=["occurrence", "approved"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"Extensão {self.occurrence_id} (+{self.extra_seconds}s)"
