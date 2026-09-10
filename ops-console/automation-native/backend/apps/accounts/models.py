import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Conta digital de acesso à intranet (não representa o colaborador)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField("e-mail", blank=True, unique=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users_created",
    )
    updated_by = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="users_updated",
    )
    must_change_password = models.BooleanField(
        "deve trocar senha",
        default=False,
        help_text="True quando a conta usa senha padrão do seed e precisa definir senha pessoal.",
    )

    class Meta:
        db_table = "auth_user"
        verbose_name = "usuário"
        verbose_name_plural = "usuários"

    def __str__(self):
        return self.username


class UserChangeHistorico(models.Model):
    """Auditoria de alterações administrativas em contas do portal."""

    ACTION_CREATE = "create"
    ACTION_EDIT = "edit"
    ACTION_ACTIVATE = "activate"
    ACTION_DEACTIVATE = "deactivate"
    ACTION_BULK = "bulk"
    ACTION_PASSWORD_RESET = "password_reset"
    ACTION_ROLES = "roles"

    ACTION_CHOICES = [
        (ACTION_CREATE, "Criação"),
        (ACTION_EDIT, "Edição"),
        (ACTION_ACTIVATE, "Ativação"),
        (ACTION_DEACTIVATE, "Desativação"),
        (ACTION_BULK, "Ação em massa"),
        (ACTION_PASSWORD_RESET, "Reset de senha"),
        (ACTION_ROLES, "Perfis RBAC"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    target_user = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="change_history",
    )
    actor = models.ForeignKey(
        "User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_changes_made",
    )
    action = models.CharField(max_length=32, choices=ACTION_CHOICES)
    field_name = models.CharField(max_length=64, blank=True, default="")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_user_change_historico"
        verbose_name = "histórico de usuário"
        verbose_name_plural = "históricos de usuário"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["target_user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.target_user.username} — {self.action} ({self.created_at})"
