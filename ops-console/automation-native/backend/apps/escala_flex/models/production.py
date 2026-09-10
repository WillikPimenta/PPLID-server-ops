import uuid

from django.db import models


class Holiday(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    date = models.DateField()
    location = models.CharField(max_length=255, blank=True)
    holiday_type = models.CharField(max_length=64, blank=True)
    name = models.CharField(max_length=255, blank=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_holiday"
        indexes = [models.Index(fields=["date", "location"])]

    def __str__(self):
        return f"{self.name} — {self.date}"


class NotifyEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    action = models.CharField("ação", max_length=128, blank=True)
    status = models.CharField(max_length=64, blank=True)
    time = models.TimeField(null=True, blank=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_notify"
        ordering = ["-time"]

    def __str__(self):
        return self.action or str(self.id)
