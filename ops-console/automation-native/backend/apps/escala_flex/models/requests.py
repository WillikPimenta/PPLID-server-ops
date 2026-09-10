import uuid

from django.db import models

from apps.workforce.models import Agent

from .dimensions import RequestType


class ScheduleRequest(models.Model):
    """Solicitações de escala (tblRequests)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_type = models.ForeignKey(
        RequestType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    agent_lan_id = models.CharField(max_length=64, blank=True)
    agent_lan_id_2 = models.CharField(max_length=64, blank=True)
    applicant_lan_id = models.CharField(max_length=64, blank=True)
    date_swap = models.DateField(null=True, blank=True)
    date_request = models.DateTimeField(null=True, blank=True)
    description = models.TextField(blank=True)
    new_journey = models.CharField(max_length=128, blank=True)
    balance_time = models.CharField(max_length=64, blank=True)
    positive = models.BooleanField(null=True, blank=True)
    approved = models.BooleanField(null=True, blank=True)
    approved_leader = models.BooleanField(null=True, blank=True)
    date_approve_leader = models.DateTimeField(null=True, blank=True)
    approver_leader_lan_id = models.CharField(max_length=64, blank=True)
    date_approve_plan = models.DateTimeField(null=True, blank=True)
    approver_plan_lan_id = models.CharField(max_length=64, blank=True)
    validation_days = models.CharField(max_length=64, blank=True)
    validation_hour = models.CharField(max_length=64, blank=True)
    validation_activity = models.CharField(max_length=255, blank=True)
    swap_kind = models.CharField(max_length=32, blank=True, default="")
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ef_schedule_request"
        indexes = [models.Index(fields=["date_swap"])]

    def __str__(self):
        return f"Solicitação {self.id}"
