import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


class Agent(models.Model):
    """Colaborador real da empresa (headcount)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    full_name = models.CharField("nome completo", max_length=255)
    user_lan_id = models.CharField("ID de rede", max_length=64, unique=True)
    email = models.EmailField("e-mail", blank=True)
    hire_date = models.DateField("data de admissão", null=True, blank=True)
    time_tracking_id = models.CharField("ID ponto", max_length=64, blank=True)
    oracle_id = models.CharField("ID Oracle", max_length=64, blank=True)
    # PAT pessoal do Jira Data Center (Bearer). Username Jira = user_lan_id.
    jira_api_token = models.CharField("token API Jira", max_length=512, blank=True, default="")
    active = models.BooleanField("ativo", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agents_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="agents_updated",
    )

    class Meta:
        db_table = "agent"
        verbose_name = "colaborador"
        verbose_name_plural = "colaboradores"
        indexes = [
            models.Index(fields=["active"]),
        ]

    def __str__(self):
        return self.full_name


class UserProfile(models.Model):
    """Vínculo opcional entre conta digital e colaborador."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    agent = models.OneToOneField(
        Agent,
        on_delete=models.CASCADE,
        related_name="user_profile",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_profile"
        verbose_name = "perfil de usuário"
        verbose_name_plural = "perfis de usuário"

    def __str__(self):
        return f"{self.user.username} ↔ {self.agent.full_name}"


class AgentHistory(models.Model):
    """Histórico temporal de movimentações do colaborador."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="history",
    )
    leader = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_histories",
    )
    facilitator = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="facilitated_histories",
    )
    location = models.CharField("localização", max_length=255, blank=True)
    team = models.CharField("time", max_length=255, blank=True)
    job_title = models.CharField("cargo", max_length=255, blank=True)
    job_activity = models.CharField("atividade", max_length=255, blank=True)
    journey = models.CharField("jornada", max_length=128, blank=True)
    team_sector = models.CharField("setor do time", max_length=255, blank=True)
    job_title_sector = models.CharField("setor do cargo", max_length=255, blank=True)
    job_title_activity = models.CharField("atividade do cargo", max_length=128, blank=True)
    journey_shift = models.CharField("turno", max_length=64, blank=True)
    band = models.CharField("faixa", max_length=64, blank=True)
    inss_type = models.CharField("tipo INSS", max_length=64, blank=True)
    external_movement_type = models.CharField(
        "tipo movimentação externa",
        max_length=128,
        blank=True,
    )
    start_date = models.DateField("data início")
    final_date = models.DateField("data fim", null=True, blank=True)
    productivity_discount = models.DecimalField(
        "desconto produtividade",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    pcd = models.BooleanField("PCD", default=False)
    jira = models.CharField("Jira", max_length=512, blank=True)
    formalization = models.CharField("formalização", max_length=255, blank=True)
    active = models.BooleanField("ativo", default=True)
    sharepoint_item_id = models.CharField(
        "ID item SharePoint",
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="Identificador de origem (lista tbHeadcount) para reimportações.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_history"
        verbose_name = "histórico do colaborador"
        verbose_name_plural = "históricos dos colaboradores"
        ordering = ["-start_date"]
        indexes = [
            models.Index(fields=["agent", "-start_date"]),
            models.Index(fields=["agent", "active"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["agent"],
                condition=Q(active=True, final_date__isnull=True),
                name="unique_active_open_history_per_agent",
            ),
        ]

    def __str__(self):
        return f"{self.agent.full_name} — {self.team} ({self.start_date})"


class HeadcountCatalogItem(models.Model):
    """Opções padronizadas dos campos de ciclo de HC (listas suspensas)."""

    CATALOG_TEAM = "team"
    CATALOG_JOB_TITLE = "job_title"
    CATALOG_JOB_ACTIVITY = "job_activity"
    CATALOG_LOCATION = "location"
    CATALOG_TEAM_SECTOR = "team_sector"
    CATALOG_JOB_TITLE_SECTOR = "job_title_sector"
    CATALOG_JOB_TITLE_ACTIVITY = "job_title_activity"
    CATALOG_BAND = "band"

    CATALOG_CHOICES = [
        (CATALOG_TEAM, "Time"),
        (CATALOG_JOB_TITLE, "Cargo"),
        (CATALOG_JOB_ACTIVITY, "Atividade"),
        (CATALOG_LOCATION, "Local"),
        (CATALOG_TEAM_SECTOR, "Setor do time"),
        (CATALOG_JOB_TITLE_SECTOR, "Setor do cargo"),
        (CATALOG_JOB_TITLE_ACTIVITY, "Atividade do cargo"),
        (CATALOG_BAND, "Band"),
    ]

    catalog = models.CharField(max_length=32, choices=CATALOG_CHOICES, db_index=True)
    value = models.CharField(max_length=255)
    label = models.CharField(max_length=255, blank=True, default="")
    active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "headcount_catalog_item"
        ordering = ["catalog", "sort_order", "value"]
        constraints = [
            models.UniqueConstraint(
                fields=["catalog", "value"],
                name="uniq_headcount_catalog_value",
            ),
        ]

    def __str__(self):
        return f"{self.catalog}: {self.value}"


class CycleChangeAudit(models.Model):
    """Auditoria de alterações de ciclo / cadastro de Headcount."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    operation_id = models.UUIDField(db_index=True)
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="cycle_change_audits",
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="cycle_change_audits",
    )
    action = models.CharField(max_length=32)
    success = models.BooleanField(default=True)
    error_detail = models.TextField(blank=True, default="")
    closed_history_id = models.UUIDField(null=True, blank=True)
    created_history_id = models.UUIDField(null=True, blank=True)
    movement_date = models.DateField(null=True, blank=True)
    field_changes = models.JSONField(default=list, blank=True)
    entities = models.JSONField(default=list, blank=True)
    technical = models.JSONField(default=list, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "cycle_change_audit"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["agent", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.operation_id} · {self.action} · {self.agent_id}"
