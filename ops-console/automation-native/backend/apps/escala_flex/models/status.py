import uuid

from django.db import models

from apps.workforce.models import Agent

from .dimensions import StatusType


class AgentStatus(models.Model):
    """Status atual do agente (tblStatus)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.OneToOneField(
        Agent,
        on_delete=models.CASCADE,
        related_name="operational_status",
    )
    status = models.ForeignKey(
        StatusType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    current_activity = models.CharField(max_length=255, blank=True)
    start_of_work = models.DateTimeField(null=True, blank=True)
    date_of_change = models.DateTimeField(null=True, blank=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ef_agent_status"

    def __str__(self):
        return f"{self.agent} — {self.status}"


class StatusEvent(models.Model):
    """Eventos de status (tblStatusEvents)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="status_events",
    )
    leader = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="team_status_events",
    )
    status = models.ForeignKey(
        StatusType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    start_date = models.DateTimeField()
    final_date = models.DateTimeField(null=True, blank=True)
    active_event = models.BooleanField(default=True)
    total_duration = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Duração total em segundos (início → fim).",
    )
    approved = models.BooleanField(null=True, blank=True)
    approved_by = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_status_events",
    )
    approved_duration = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Duração aprovada em segundos.",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    approval_notes = models.TextField(
        blank=True,
        help_text="Comentário do líder na aprovação/rejeição.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ef_status_event"
        indexes = [
            models.Index(fields=["agent", "start_date"]),
            models.Index(fields=["leader"]),
            models.Index(fields=["agent", "active_event"]),
        ]

    def __str__(self):
        return f"{self.agent} — {self.status} ({self.start_date})"


class CurrentActivity(models.Model):
    """Atividade/NH atual (tblCurrentActivity)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.OneToOneField(
        Agent,
        on_delete=models.CASCADE,
        related_name="current_activity_record",
    )
    current_activity = models.CharField(max_length=255, blank=True)
    hierarchical_level = models.ForeignKey(
        "escala_flex.HierarchicalLevel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_activity_records",
    )
    date_of_change = models.DateTimeField(null=True, blank=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ef_current_activity"

    def __str__(self):
        return f"{self.agent} — {self.current_activity}"
