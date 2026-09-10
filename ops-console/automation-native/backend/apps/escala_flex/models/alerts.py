import uuid

from django.db import models
from django.db.models import Q

from apps.workforce.models import Agent


class OperationalAlertEvent(models.Model):
    """Ciclo de vida persistido de um alerta calculado pelo painel operacional."""

    STATUS_ACTIVE = "active"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = (
        (STATUS_ACTIVE, "Ativo"),
        (STATUS_RESOLVED, "Resolvido"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        Agent,
        on_delete=models.PROTECT,
        related_name="operational_alert_events",
    )
    operational_date = models.DateField()
    alert_type = models.CharField(max_length=64)
    fingerprint = models.CharField(max_length=64)
    source_type = models.CharField(max_length=32, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    priority_initial = models.CharField(max_length=16, blank=True)
    priority_current = models.CharField(max_length=16, blank=True)
    state = models.CharField(max_length=255, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    recommended_action = models.TextField(blank=True)
    first_seen_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    occurrence_count = models.PositiveIntegerField(default=1)
    leader_lan_id = models.CharField(max_length=64, blank=True)
    leader_name = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    sector = models.CharField(max_length=255, blank=True)
    job_activity = models.CharField(max_length=255, blank=True)
    context = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ef_operational_alert_event"
        ordering = ("-first_seen_at", "-created_at")
        indexes = [
            models.Index(fields=("operational_date", "status"), name="ef_alert_date_status_idx"),
            models.Index(fields=("agent", "first_seen_at"), name="ef_alert_agent_seen_idx"),
            models.Index(fields=("alert_type", "status"), name="ef_alert_type_status_idx"),
            models.Index(fields=("leader_lan_id", "operational_date"), name="ef_alert_leader_date_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("fingerprint",),
                condition=Q(status="active"),
                name="ef_unique_active_alert_fp",
            ),
        ]

    def __str__(self):
        return f"{self.agent} — {self.alert_type} ({self.status})"
