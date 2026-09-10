import uuid

from django.db import models

from apps.workforce.models import Agent


class Schedule(models.Model):
    """Escala planejada (tblSchedule)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="schedules",
    )
    date = models.DateField("data")
    work_schedule = models.CharField("escala", max_length=32, blank=True)
    working_hour = models.DecimalField(
        "horas",
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
    )
    overtime = models.BooleanField("hora extra", default=False)
    work_day = models.BooleanField("dia útil", default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ef_schedule"
        indexes = [
            models.Index(fields=["date", "agent"]),
            models.Index(fields=["date", "work_day"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "date"],
                name="ef_unique_schedule_agent_date",
            ),
        ]

    def __str__(self):
        return f"{self.agent} — {self.date}"


class ScheduleToday(models.Model):
    """Escala consolidada do dia (tblScheduleToday)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="schedule_today_entries",
    )
    date = models.DateField("data")
    work_schedule = models.CharField("escala do dia", max_length=32, blank=True)
    working_hour = models.DecimalField(
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
    )
    overtime = models.BooleanField(default=False)
    status = models.ForeignKey(
        "escala_flex.StatusType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schedule_today_entries",
    )
    current_activity = models.CharField(max_length=255, blank=True)
    hierarchical_level = models.ForeignKey(
        "escala_flex.HierarchicalLevel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="schedule_today_entries",
    )
    start_of_work = models.DateTimeField(null=True, blank=True)
    week_break = models.CharField(max_length=32, blank=True)
    weekend_break = models.CharField(max_length=32, blank=True)
    is_previous_night_shift = models.BooleanField(default=False)
    leader = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_schedule_today",
    )
    location = models.CharField(max_length=255, blank=True)
    job_activity = models.CharField(max_length=255, blank=True)
    job_title = models.CharField(max_length=255, blank=True)
    journey = models.CharField(max_length=128, blank=True)
    sector = models.CharField(max_length=255, blank=True)
    full_name = models.CharField(max_length=255, blank=True)
    leader_lan_id = models.CharField(max_length=64, blank=True)
    blocked = models.BooleanField(default=False)
    blocked_by = models.CharField(max_length=255, blank=True)
    blocked_description = models.TextField(blank=True)
    blocked_at = models.DateTimeField(null=True, blank=True)
    last_change = models.DateTimeField(null=True, blank=True)
    observation = models.TextField("observação", blank=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ef_schedule_today"
        indexes = [
            models.Index(fields=["date", "agent"]),
            models.Index(fields=["date", "leader_lan_id"]),
            models.Index(fields=["date", "status"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "date"],
                name="ef_unique_schedule_today_agent_date",
            ),
        ]

    def __str__(self):
        return f"{self.full_name or self.agent} — {self.date}"
