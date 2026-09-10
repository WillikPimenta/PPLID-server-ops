"""Modelos de configuração RBAC persistida."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class PortalRoutePolicy(models.Model):
    """Política de acesso por rota do frontend (Vue Router name)."""

    route_name = models.CharField(max_length=128, unique=True, db_index=True)
    path = models.CharField(max_length=255)
    label = models.CharField(max_length=255)
    section = models.CharField(max_length=64, blank=True, default="")
    permissions_any = models.JSONField(default=list, blank=True)
    permissions_all = models.JSONField(default=list, blank=True)
    requires_auth = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    is_editable = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="route_policy_updates",
    )

    class Meta:
        ordering = ["section", "path"]
        verbose_name = "Política de rota RBAC"
        verbose_name_plural = "Políticas de rotas RBAC"

    def __str__(self) -> str:
        return f"{self.route_name} ({self.path})"


class PortalMenuOption(models.Model):
    """Opção exibida nos menus e cards do portal, vinculada a rotas RBAC."""

    menu_key = models.CharField(max_length=128, unique=True, db_index=True)
    label = models.CharField(max_length=128)
    description = models.CharField(max_length=255, blank=True, default="")
    section = models.CharField(max_length=64)
    route_names = models.JSONField(default=list, blank=True)
    parent_menu_key = models.CharField(max_length=128, blank=True, default="", db_index=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    is_builtin = models.BooleanField(default=False)
    is_editable = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="menu_option_updates",
    )

    class Meta:
        ordering = ["section", "label", "menu_key"]
        verbose_name = "Opção de menu RBAC"
        verbose_name_plural = "Opções de menu RBAC"

    def __str__(self) -> str:
        return f"{self.label} ({self.menu_key})"


class PortalRoleDefinition(models.Model):
    """Definição persistida de perfil RBAC (permissões + escopo)."""

    role = models.CharField(max_length=64, primary_key=True)
    label = models.CharField(max_length=128)
    area = models.CharField(max_length=32, blank=True, default="outros")
    permissions = models.JSONField(default=list, blank=True)
    # None = sem restrição por rota (só permissões). Lista = somente estas rotas (+ sistema).
    granted_routes = models.JSONField(default=None, null=True, blank=True)
    default_scope = models.CharField(max_length=16, default="global")
    is_editable = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="role_definition_updates",
    )

    class Meta:
        ordering = ["role"]
        verbose_name = "Definição de perfil RBAC"
        verbose_name_plural = "Definições de perfis RBAC"

    def __str__(self) -> str:
        return f"{self.label} ({self.role})"
