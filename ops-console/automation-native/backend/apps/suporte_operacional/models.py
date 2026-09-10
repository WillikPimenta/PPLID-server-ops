from __future__ import annotations

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class OperationalSupportRequestQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError(
            "Solicitações de suporte só podem ser alteradas pelo workflow auditado."
        )


class OperationalSupportRequest(models.Model):
    CONFER_SUBJECT = "Confer"
    CONFER_WORKFLOW = "Confer Web"
    CONFER_CLIENT = "Claro"

    class OperationOrigin(models.TextChoices):
        FRAUD = "fraud", "Fraud"
        CONFER = "confer", "Confer"

    class RequestType(models.TextChoices):
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"
        PRESENCIAL = "presencial", "Presencial"

    class RequesterType(models.TextChoices):
        AGENT = "agent", "Agente"
        LEADER = "leader", "Líder"

    class Status(models.TextChoices):
        PENDING_LEADER = "pending_leader", "Aguardando líder"
        PENDING_SUPPORT = "pending_support", "Aguardando suporte"
        PENDING_OFFLINE = "pending_offline", "Aguardando suporte offline"
        IN_ANALYSIS = "in_analysis", "Em análise"
        ANSWERED = "answered", "Respondida"
        REJECTED_LEADER = "rejected_leader", "Recusada pelo líder"
        CANCELLED = "cancelled", "Cancelada"

    class LeaderDecision(models.TextChoices):
        APPROVED = "approved", "Aprovada"
        REJECTED = "rejected", "Recusada"

    class DifficultyLevel(models.TextChoices):
        EASY = "Fácil", "Fácil"
        MEDIUM = "Médio", "Médio"
        HARD = "Difícil", "Difícil"

    class QueueOrigin(models.TextChoices):
        AUTO = "auto", "Automático"
        DIRECTED = "directed", "Direcionado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        "workforce.Agent",
        on_delete=models.PROTECT,
        related_name="operational_support_requests",
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="operational_support_requests_created",
    )
    requester_type = models.CharField(max_length=16, choices=RequesterType.choices)
    operation_origin = models.CharField(
        max_length=16,
        choices=OperationOrigin.choices,
        default=OperationOrigin.FRAUD,
        db_index=True,
    )
    request_type = models.CharField(
        max_length=16,
        choices=RequestType.choices,
        default=RequestType.ONLINE,
        db_index=True,
    )
    protocol = models.CharField(max_length=100, blank=True, default="", db_index=True)
    workflow = models.CharField(max_length=512, blank=True, default="")
    client = models.CharField(max_length=512, blank=True, default="")
    subject = models.CharField(max_length=200)
    category = models.CharField(max_length=32)
    description = models.TextField()
    reference = models.CharField(max_length=120, blank=True, default="")
    status = models.CharField(max_length=32, choices=Status.choices, db_index=True)

    leader_decider = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_support_leader_decisions",
    )
    leader_decision = models.CharField(
        max_length=16,
        choices=LeaderDecision.choices,
        blank=True,
        default="",
    )
    leader_justification = models.TextField(blank=True, default="")
    leader_decided_at = models.DateTimeField(null=True, blank=True)
    auto_approved = models.BooleanField(default=False)

    presencial_support_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_support_presencial_requests",
    )
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_support_assigned",
    )
    assigned_at = models.DateTimeField(null=True, blank=True)

    answer = models.TextField(blank=True, default="")
    answer_option = models.CharField(max_length=200, blank=True, default="")
    difficulty_level = models.CharField(
        max_length=16,
        choices=DifficultyLevel.choices,
        blank=True,
        default="",
    )
    document_uf = models.CharField(max_length=32, blank=True, default="")
    document_type = models.CharField(max_length=120, blank=True, default="")
    answered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_support_answers",
    )
    answered_at = models.DateTimeField(null=True, blank=True)

    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_support_cancellations",
    )
    cancel_reason = models.TextField(blank=True, default="")
    cancelled_at = models.DateTimeField(null=True, blank=True)

    workflow_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)
    cliente_id = models.PositiveIntegerField(null=True, blank=True, db_index=True)

    queue_origin = models.CharField(max_length=16, blank=True, default="", db_index=True)
    queue_slot = models.PositiveSmallIntegerField(null=True, blank=True)
    queue_sla_started_at = models.DateTimeField(null=True, blank=True, db_index=True)
    queue_priority_at = models.DateTimeField(null=True, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = OperationalSupportRequestQuerySet.as_manager()

    class Meta:
        db_table = "operational_support_request"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["agent", "status"]),
            models.Index(
                fields=["request_type", "status", "created_at"],
                name="ops_req_type_status_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.subject} ({self.status})"

    def save(self, *args, **kwargs):
        if not self._state.adding and not getattr(self, "_allow_workflow_update", False):
            raise ValidationError(
                "Solicitações de suporte só podem ser alteradas pelo workflow auditado."
            )
        return super().save(*args, **kwargs)


class OperationalSupportEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Eventos do histórico são imutáveis.")

    def delete(self):
        raise ValidationError("Eventos do histórico não podem ser excluídos.")


class OperationalSupportEvent(models.Model):
    class EventType(models.TextChoices):
        CREATED = "created", "Criação"
        AUTO_APPROVED = "auto_approved", "Aprovação automática"
        APPROVED = "approved", "Aprovação"
        REJECTED = "rejected", "Recusa"
        CANCELLED = "cancelled", "Cancelamento"
        ASSIGNED = "assigned", "Atribuição"
        QUEUE_ASSIGNED = "queue_assigned", "Fila automática"
        QUEUE_DIRECTED = "queue_directed", "Direcionamento"
        QUEUE_PRIORITIZED = "queue_prioritized", "Priorização da fila"
        QUEUE_TIMEOUT = "queue_timeout", "Timeout da fila"
        QUEUE_RELEASED = "queue_released", "Liberação da fila"
        PRESENCE_CHANGED = "presence_changed", "Status de presença"
        ANSWERED = "answered", "Resposta"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request = models.ForeignKey(
        OperationalSupportRequest,
        on_delete=models.PROTECT,
        related_name="events",
    )
    sequence = models.PositiveIntegerField()
    event_version = models.PositiveSmallIntegerField(default=1)
    event_type = models.CharField(max_length=32, choices=EventType.choices)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operational_support_events",
    )
    actor_username = models.CharField(max_length=150, blank=True, default="")
    actor_name = models.CharField(max_length=300, blank=True, default="")
    actor_roles = models.JSONField(blank=True, default=list)
    from_status = models.CharField(max_length=32, blank=True, default="")
    to_status = models.CharField(max_length=32, blank=True, default="")
    note = models.TextField(blank=True, default="")
    snapshot = models.JSONField(blank=True, default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = OperationalSupportEventQuerySet.as_manager()

    class Meta:
        db_table = "operational_support_event"
        ordering = ["request_id", "sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["request", "sequence"],
                name="ops_evt_req_seq_uniq",
            ),
        ]
        indexes = [
            models.Index(fields=["created_at"], name="ops_evt_created_idx"),
            models.Index(
                fields=["event_type", "created_at"],
                name="ops_evt_type_created_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Eventos do histórico são imutáveis.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Eventos do histórico não podem ser excluídos.")

    def __str__(self) -> str:
        return f"{self.event_type} → {self.to_status}"


class OperationalSupportAgentPresence(models.Model):
    STATUS_ONLINE = "online"
    STATUS_OFFLINE = "offline"
    STATUS_PRESENCIAL = "presencial"
    STATUS_CHOICES = [
        (STATUS_ONLINE, "Online"),
        (STATUS_OFFLINE, "Offline"),
        (STATUS_PRESENCIAL, "Presencial"),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="operational_support_presence",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OFFLINE,
        db_index=True,
    )
    status_changed_at = models.DateTimeField(auto_now_add=True, db_index=True)
    last_assigned_at = models.DateTimeField(null=True, blank=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operational_support_agent_presence"
        ordering = ["user__username"]

    def __str__(self) -> str:
        return f"{self.user_id} ({self.status})"


class OperationalSupportNotice(models.Model):
    """Recado simples e exclusivo do mural de Suporte Operacional."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=160)
    message = models.TextField(max_length=3000)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="operational_support_notices",
    )
    active = models.BooleanField(default=True)
    inactive_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "operational_support_notice"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["active", "-created_at"], name="ops_notice_active_idx"),
        ]

    def __str__(self) -> str:
        return self.title
