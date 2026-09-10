import uuid

from django.db import models


class StatusType(models.Model):
    """Catálogo de status operacionais (dimStatusType)."""

    id = models.PositiveSmallIntegerField(primary_key=True)
    name = models.CharField("nome", max_length=128)
    color = models.CharField("cor hex", max_length=32, blank=True)
    active = models.BooleanField(default=True)
    logged_in = models.BooleanField(default=False)
    observation = models.TextField("observação", blank=True)
    deducts_logged_time = models.BooleanField("abate tempo logado", default=False)
    default_time_seconds = models.PositiveIntegerField(
        "tempo padrão (segundos)",
        null=True,
        blank=True,
    )
    deducts_production = models.BooleanField("abate produção", default=False)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_status_type"
        verbose_name = "tipo de status"
        verbose_name_plural = "tipos de status"
        ordering = ["id"]

    def __str__(self):
        return self.name


class AbsenceType(models.Model):
    """Catálogo de ausências na escala (FOLGA, FÉRIAS, BH, etc.)."""

    id = models.PositiveSmallIntegerField(primary_key=True)
    code = models.CharField(
        "código na escala",
        max_length=32,
        unique=True,
        help_text="Valor gravado em dia_escala (ex.: FOLGA).",
    )
    name = models.CharField("nome", max_length=128)
    color = models.CharField("cor hex", max_length=32, blank=True)
    active = models.BooleanField(default=True)
    observation = models.TextField("observação", blank=True)

    class Meta:
        db_table = "ef_absence_type"
        ordering = ["name"]
        verbose_name = "tipo de ausência"
        verbose_name_plural = "tipos de ausência"

    def __str__(self):
        return self.name


class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    city_name = models.CharField("cidade", max_length=128)
    state_name = models.CharField("estado", max_length=64, blank=True)
    display_name = models.CharField("nome exibição", max_length=255, blank=True)
    active = models.BooleanField(default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_location"
        ordering = ["city_name"]

    def __str__(self):
        return self.display_name or self.city_name


class JobActivity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("atividade", max_length=255)
    active = models.BooleanField(default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_job_activity"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Journey(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("jornada", max_length=128)
    entry_time = models.TimeField(null=True, blank=True)
    exit_time = models.TimeField(null=True, blank=True)
    shift = models.CharField("turno", max_length=64, blank=True)
    active = models.BooleanField(default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_journey"
        ordering = ["name"]

    def __str__(self):
        return self.name


class HierarchicalLevel(models.Model):
    """Nível hierárquico de atendimento (Megazord + combobox Monitoramento/NH)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("nível hierárquico", max_length=255)
    active = models.BooleanField(default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_hierarchical_level"
        ordering = ["name"]

    def __str__(self):
        return self.name


class BreakTime(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    agent_lan_id = models.CharField("UserLanID", max_length=64, db_index=True)
    week = models.CharField("intervalo semanal", max_length=32, blank=True)
    weekend = models.CharField("intervalo extra", max_length=32, blank=True)
    active = models.BooleanField(default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_break_time"
        indexes = [models.Index(fields=["agent_lan_id", "active"])]

    def __str__(self):
        return f"{self.agent_lan_id} ({self.week})"


class RequestType(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True)
    name = models.CharField("tipo", max_length=128)
    icon = models.CharField(max_length=64, blank=True)
    active = models.BooleanField(default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_request_type"
        ordering = ["id"]

    def __str__(self):
        return self.name


class AppUser(models.Model):
    """Usuários ativos no app (dimUsers)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_lan_id = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=True)
    sharepoint_id = models.PositiveIntegerField(null=True, blank=True, unique=True)

    class Meta:
        db_table = "ef_app_user"

    def __str__(self):
        return self.user_lan_id
