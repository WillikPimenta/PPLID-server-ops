from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class NhPrioridadeFluxo(models.Model):
    """
    Snapshot vigente: prioridade NH × workflow/etapa no BrFlow.
    Sync diferencial: só cria/atualiza o que mudou; remove ausentes da carga.
    """

    prk_cliente = models.PositiveIntegerField()
    nom_cliente = models.CharField(max_length=512)
    prk_workflow = models.PositiveIntegerField()
    nom_workflow = models.CharField(max_length=512, blank=True, default="")
    prk_nivel_hierarquico = models.PositiveIntegerField(db_index=True)
    nom_nivel_hierarquico = models.CharField(max_length=512)
    prk_fluxo = models.PositiveIntegerField()
    nom_fluxo = models.CharField(max_length=512)
    prk_modulo = models.PositiveIntegerField(null=True, blank=True)
    nom_modulo = models.CharField(max_length=255, blank=True, default="")
    num_prioridade_fluxo = models.IntegerField()
    cod_analise = models.CharField(max_length=64, blank=True, default="")
    hierarchical_level_id = models.UUIDField(null=True, blank=True, db_index=True)
    synced_at = models.DateTimeField(
        help_text="Última alteração efetiva desta linha (não atualiza se o conteúdo for igual).",
    )

    class Meta:
        db_table = "prioridades_nh_fluxo"
        ordering = ["nom_nivel_hierarquico", "num_prioridade_fluxo", "nom_workflow"]
        constraints = [
            models.UniqueConstraint(
                fields=["prk_nivel_hierarquico", "prk_fluxo"],
                name="uq_prioridades_nh_nivel_fluxo",
            ),
        ]
        indexes = [
            models.Index(fields=["prk_nivel_hierarquico", "num_prioridade_fluxo"]),
            models.Index(fields=["prk_workflow"]),
            models.Index(fields=["synced_at"]),
        ]

    def __str__(self) -> str:
        return (
            f"{self.nom_nivel_hierarquico} · {self.nom_fluxo} "
            f"(prio {self.num_prioridade_fluxo})"
        )


class PrioridadesNhSyncLog(models.Model):
    TRIGGER_SYSTEM = "system"
    TRIGGER_USER = "user"
    TRIGGER_CHOICES = (
        (TRIGGER_SYSTEM, "Sistema"),
        (TRIGGER_USER, "Usuário"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    path = models.CharField(max_length=1024, blank=True, default="")
    trigger_source = models.CharField(
        max_length=16, choices=TRIGGER_CHOICES, default=TRIGGER_SYSTEM
    )
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(default=False)
    message = models.TextField(blank=True, default="")
    duration_seconds = models.FloatField(null=True, blank=True)
    inserted = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    unchanged = models.PositiveIntegerField(default=0)
    deleted = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "prioridades_nh_sync_log"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Sync #{self.pk} success={self.success}"
