import uuid

from django.conf import settings
from django.db import models

from apps.workforce.models import Agent


class EscalaImportBatch(models.Model):
    """Lote de importação de escala via Excel."""

    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PROCESSING, "Em andamento"),
        (STATUS_COMPLETED, "Concluída"),
        (STATUS_FAILED, "Falhou"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    filename = models.CharField("arquivo", max_length=255)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_import_batches",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_COMPLETED,
    )
    failure_detail = models.TextField(blank=True)
    sheets_processed = models.JSONField(default=list, blank=True)
    rows_upserted = models.PositiveIntegerField(default=0)
    schedules_synced = models.PositiveIntegerField(default=0)
    dates_rebuilt = models.JSONField(default=list, blank=True)
    errors = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "escala_import_batch"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.filename} ({self.created_at:%Y-%m-%d %H:%M})"


class EscalaGenerationRun(models.Model):
    """Execução versionada da geração automática de escala mensal."""

    STATUS_DRAFT = "draft"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_PUBLISHED = "published"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Rascunho"),
        (STATUS_PROCESSING, "Processando"),
        (STATUS_READY, "Pronta para revisão"),
        (STATUS_PUBLISHED, "Publicada"),
        (STATUS_FAILED, "Falhou"),
        (STATUS_CANCELLED, "Cancelada"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    reference_month = models.DateField("mês de referência")
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )
    configuration = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)
    agents_considered = models.PositiveIntegerField(default=0)
    entries_generated = models.PositiveIntegerField(default=0)
    conflicts_count = models.PositiveIntegerField(default=0)
    failure_detail = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_generation_runs_created",
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_generation_runs_published",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "escala_generation_run"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Geração {self.reference_month:%Y-%m} ({self.status})"


class EscalaGenerationEntry(models.Model):
    """Item da prévia mensal (1 agente × 1 dia)."""

    SOURCE_GENERATED = "generated"
    SOURCE_ABSENCE = "absence"
    SOURCE_HOLIDAY = "holiday"
    SOURCE_MANUAL = "manual"
    SOURCE_PRESERVED = "preserved"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        EscalaGenerationRun,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="escala_generation_entries",
    )
    date = models.DateField()
    leader = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_escala_generation_entries",
    )
    job_activity = models.ForeignKey(
        "escala_flex.JobActivity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_entries",
    )
    location = models.ForeignKey(
        "escala_flex.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_entries",
    )
    team = models.CharField(max_length=255, blank=True)
    sector = models.CharField(max_length=255, blank=True)
    schedule = models.CharField("horário padrão", max_length=32, blank=True)
    day_value = models.CharField("valor do dia", max_length=32, blank=True)
    source = models.CharField(max_length=32, default=SOURCE_GENERATED)
    has_conflict = models.BooleanField(default=False)
    adjusted_manually = models.BooleanField(default=False)
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_generation_entries_adjusted",
    )
    observation = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "escala_generation_entry"
        ordering = ["date", "agent__full_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["run", "agent", "date"],
                name="escala_generation_unique_run_agent_date",
            ),
        ]
        indexes = [
            models.Index(fields=["run", "date"]),
            models.Index(fields=["run", "has_conflict"]),
        ]

    def __str__(self):
        return f"{self.agent.user_lan_id} — {self.date}"


class EscalaGenerationConflict(models.Model):
    """Conflito detectado na prévia ou na revalidação."""

    SEVERITY_BLOCKING = "blocking"
    SEVERITY_WARNING = "warning"
    SEVERITY_CHOICES = [
        (SEVERITY_BLOCKING, "Impeditivo"),
        (SEVERITY_WARNING, "Aviso"),
    ]

    TYPE_MISSING_SCHEDULE = "missing_schedule"
    TYPE_MISSING_TEAM = "missing_team"
    TYPE_MISSING_ACTIVITY = "missing_activity"
    TYPE_MISSING_LEADER = "missing_leader"
    TYPE_MISSING_LOCATION = "missing_location"
    TYPE_COVERAGE_BELOW_MIN = "coverage_below_min"
    TYPE_SCHEDULE_OVER_MAX = "schedule_over_max"
    TYPE_INVALID_JOURNEY = "invalid_journey"
    TYPE_INSUFFICIENT_REST = "insufficient_rest"
    TYPE_NIGHT_SHIFT_DENIED = "night_shift_denied"
    TYPE_EXISTING_ESCALA = "existing_escala"
    TYPE_ABSENCE_CONFLICT = "absence_conflict"
    TYPE_INACTIVE_ON_DATE = "inactive_on_date"
    TYPE_UNBALANCED = "unbalanced"
    TYPE_HOLIDAY_WORK = "holiday_work"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    run = models.ForeignKey(
        EscalaGenerationRun,
        on_delete=models.CASCADE,
        related_name="conflicts",
    )
    entry = models.ForeignKey(
        EscalaGenerationEntry,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="conflicts",
    )
    agent = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_generation_conflicts",
    )
    date = models.DateField(null=True, blank=True)
    activity_name = models.CharField(max_length=255, blank=True)
    conflict_type = models.CharField(max_length=64)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)
    message = models.TextField()
    details = models.JSONField(default=dict, blank=True)
    resolved = models.BooleanField(default=False)
    resolution_note = models.TextField(blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_generation_conflicts_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "escala_generation_conflict"
        ordering = ["severity", "date", "created_at"]
        indexes = [
            models.Index(fields=["run", "severity", "resolved"]),
        ]

    def __str__(self):
        return f"{self.conflict_type} ({self.severity})"


class Escala(models.Model):
    """Escala planejada importada do Excel (wide → long)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="escala_entries",
    )
    leader = models.ForeignKey(
        Agent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="led_escala_entries",
    )
    job_activity = models.ForeignKey(
        "escala_flex.JobActivity",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_entries",
    )
    location = models.ForeignKey(
        "escala_flex.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="escala_entries",
    )
    bloco = models.PositiveSmallIntegerField(null=True, blank=True)
    equipe = models.CharField(max_length=255, blank=True)
    horario = models.CharField("horário padrão", max_length=32, blank=True)
    data = models.DateField("data")
    dia_escala = models.CharField("valor do dia", max_length=32, blank=True)
    import_batch = models.ForeignKey(
        EscalaImportBatch,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "escala"
        indexes = [
            models.Index(fields=["data", "agent"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["agent", "data"],
                name="escala_unique_agent_date",
            ),
        ]

    def __str__(self):
        return f"{self.agent.user_lan_id} — {self.data}"
