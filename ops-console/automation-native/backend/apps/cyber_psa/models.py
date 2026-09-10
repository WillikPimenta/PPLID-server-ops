# -*- coding: utf-8 -*-
from django.conf import settings
from django.db import models


class CyberRiskOverride(models.Model):
    STATUS_OPEN = "open"
    STATUS_ACCEPTED = "accepted"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Aberto"),
        (STATUS_ACCEPTED, "Aceito"),
        (STATUS_RESOLVED, "Resolvido"),
    ]

    risk_id = models.CharField(max_length=80, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    note = models.TextField(blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cyber_risk_overrides",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["risk_id"]

    def __str__(self) -> str:
        return f"{self.risk_id} ({self.status})"
