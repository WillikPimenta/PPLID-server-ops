from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


RETENTION_DAYS = 90


def default_notification_expiry():
    return timezone.now() + timedelta(days=RETENTION_DAYS)


class PortalNotification(models.Model):
    class Kind(models.TextChoices):
        INFO = "info", "Informação"
        SUCCESS = "success", "Sucesso"
        WARNING = "warning", "Atenção"
        ERROR = "error", "Crítica"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="portal_notifications",
    )
    title = models.CharField(max_length=160)
    message = models.TextField(blank=True, default="")
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.INFO)
    target_url = models.CharField(max_length=500, blank=True, default="")
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.CharField(max_length=100, blank=True, default="")
    dedupe_key = models.CharField(max_length=220, null=True, blank=True)
    seen_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField(default=default_notification_expiry, db_index=True)

    class Meta:
        db_table = "portal_notification"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(
                fields=["recipient", "read_at", "-created_at"],
                name="portal_notif_user_read_idx",
            ),
            models.Index(
                fields=["recipient", "seen_at", "-created_at"],
                name="portal_notif_user_seen_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["recipient", "dedupe_key"],
                name="portal_notif_user_dedupe_uniq",
            )
        ]

    def __str__(self) -> str:
        return f"{self.recipient}: {self.title}"


class PortalNotificationRule(models.Model):
    class Lifecycle(models.TextChoices):
        UNCONFIGURED = "unconfigured", "Não configurada"
        DRAFT = "draft", "Rascunho"
        REVIEW = "review", "Em revisão"
        PUBLISHED = "published", "Publicada"
        SUSPENDED = "suspended", "Suspensa"
        DISCARDED = "discarded", "Descartada"

    class IntegrationStatus(models.TextChoices):
        NOT_INTEGRATED = "not_integrated", "Não integrada"
        PARTIAL = "partial", "Parcialmente integrada"
        INTEGRATED = "integrated", "Integrada"
        ERROR = "error", "Integração com erro"

    class Frequency(models.TextChoices):
        IMMEDIATE = "immediate", "Imediatamente"
        PER_OCCURRENCE = "per_occurrence", "Uma vez por ocorrência"
        PER_STATE_CHANGE = "per_state_change", "Uma vez por mudança de estado"
        RATE_LIMITED = "rate_limited", "No máximo uma vez por período"
        DAILY_DIGEST = "daily_digest", "No máximo uma vez por dia"
        WEEKLY_DIGEST = "weekly_digest", "No máximo uma vez por semana"
    class Priority(models.TextChoices):
        INFO = "info", "Informativa"
        WARNING = "warning", "Atenção"
        CRITICAL = "critical", "Crítica"

    class Recommendation(models.TextChoices):
        REQUIRED = "required", "Obrigatória"
        RECOMMENDED = "recommended", "Recomendada"
        OPTIONAL = "optional", "Opcional"
        POLICY_REQUIRED = "policy_required", "Dependente de regra"
        KEEP = "keep", "Manter"

    class Decision(models.TextChoices):
        PENDING = "pending", "A definir"
        APPROVED = "approved", "Aprovar"
        ADJUST = "adjust", "Ajustar"
        DISCARDED = "discarded", "Descartar"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_key = models.CharField(max_length=160, unique=True)
    section = models.CharField(max_length=80, db_index=True)
    functionality = models.CharField(max_length=255)
    route = models.CharField(max_length=500, blank=True, default="")
    event_label = models.CharField(max_length=255)
    trigger_description = models.TextField(blank=True, default="")
    primary_recipient_rule = models.TextField(blank=True, default="")
    escalation_recipient_rule = models.TextField(blank=True, default="")
    channel = models.CharField(max_length=120, blank=True, default="Portal")
    priority = models.CharField(
        max_length=16,
        choices=Priority.choices,
        default=Priority.INFO,
        db_index=True,
    )
    action_description = models.TextField(blank=True, default="")
    current_coverage = models.TextField(blank=True, default="")
    recommendation = models.CharField(
        max_length=32,
        choices=Recommendation.choices,
        default=Recommendation.RECOMMENDED,
        db_index=True,
    )
    frequency_rule = models.TextField(blank=True, default="")
    decision = models.CharField(
        max_length=16,
        choices=Decision.choices,
        default=Decision.PENDING,
        db_index=True,
    )
    enabled = models.BooleanField(default=True, db_index=True)
    notes = models.TextField(blank=True, default="")
    lifecycle = models.CharField(
        max_length=20,
        choices=Lifecycle.choices,
        default=Lifecycle.UNCONFIGURED,
        db_index=True,
    )
    integration_status = models.CharField(
        max_length=20,
        choices=IntegrationStatus.choices,
        default=IntegrationStatus.NOT_INTEGRATED,
        db_index=True,
    )
    recipient_types = models.JSONField(default=list, blank=True)
    escalation_recipient_types = models.JSONField(default=list, blank=True)
    channels = models.JSONField(default=list, blank=True)
    frequency = models.CharField(
        max_length=24,
        choices=Frequency.choices,
        default=Frequency.PER_OCCURRENCE,
    )
    frequency_window_minutes = models.PositiveIntegerField(null=True, blank=True)
    notification_title = models.CharField(max_length=160, blank=True, default="")
    notification_message = models.TextField(blank=True, default="")
    target_url = models.CharField(max_length=500, blank=True, default="")
    version = models.PositiveIntegerField(default=0)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="published_notification_rules",
    )
    catalog_managed = models.BooleanField(default=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_notification_rules",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "portal_notification_rule"
        ordering = ["section", "functionality", "event_label"]
        indexes = [
            models.Index(
                fields=["section", "enabled", "priority"],
                name="portal_notif_rule_filter_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.functionality}: {self.event_label}"


class PortalNotificationRuleVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rule = models.ForeignKey(
        PortalNotificationRule,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version = models.PositiveIntegerField()
    snapshot = models.JSONField(default=dict)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_rule_versions",
    )
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "portal_notification_rule_version"
        ordering = ["-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["rule", "version"],
                name="portal_notif_rule_version_uniq",
            )
        ]


class PortalNotificationEventLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_key = models.CharField(max_length=160, db_index=True)
    rule = models.ForeignKey(
        PortalNotificationRule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="event_logs",
    )
    source_type = models.CharField(max_length=80, blank=True, default="")
    source_id = models.CharField(max_length=100, blank=True, default="")
    outcome = models.CharField(max_length=32, db_index=True)
    recipient_count = models.PositiveIntegerField(default=0)
    detail = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "portal_notification_event_log"
        ordering = ["-created_at"]
