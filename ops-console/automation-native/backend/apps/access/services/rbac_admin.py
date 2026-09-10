"""Helpers compartilhados para CRUD de perfis/rotas RBAC pela UI."""

from __future__ import annotations

import re
from typing import Iterable

from django.contrib.auth.models import Group

from apps.access.constants import ALL_ROLES, LEGACY_ROLES, ROLE_ADM_PORTAL, role_group_name
from apps.access.models import PortalRoleDefinition
from apps.access.portal_route_registry import PORTAL_ROUTE_DEFINITIONS, SYSTEM_ROUTE_NAMES

ROLE_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,62}$")
VALID_AREAS = frozenset(
    {"planejamento", "operacao", "processos", "qualidade", "administracao", "outros"}
)


def known_role_ids() -> set[str]:
    """Perfis builtins + customizados persistidos."""
    ids = set(ALL_ROLES) | set(LEGACY_ROLES)
    ids.update(PortalRoleDefinition.objects.values_list("role", flat=True))
    return ids


def is_builtin_role(role: str) -> bool:
    return role in ALL_ROLES or role in LEGACY_ROLES


def is_protected_role(role: str) -> bool:
    return role == ROLE_ADM_PORTAL


def validate_role_slug(role: str) -> str:
    value = (role or "").strip().lower()
    if not ROLE_SLUG_RE.match(value):
        raise ValueError(
            "Código do perfil inválido. Use letras minúsculas, números e underscore "
            "(ex.: qual_auditoria_fraud)."
        )
    return value


def ensure_role_group(role: str) -> Group:
    group, _ = Group.objects.get_or_create(name=role_group_name(role))
    return group


def users_with_role_count(role: str) -> int:
    from django.contrib.auth import get_user_model

    return (
        get_user_model()
        .objects.filter(groups__name=role_group_name(role))
        .distinct()
        .count()
    )


def rename_role_definition(old_role: str, new_role: str, *, actor=None) -> PortalRoleDefinition:
    """Renomeia o código (PK) de um perfil customizado e migra o grupo Django."""
    from django.db import transaction

    new_role = validate_role_slug(new_role)
    if new_role == old_role:
        return PortalRoleDefinition.objects.get(role=old_role)

    if is_builtin_role(old_role) or is_protected_role(old_role):
        raise ValueError(
            "O código de perfis do catálogo padrão não pode ser alterado."
        )
    if is_builtin_role(new_role) or is_protected_role(new_role):
        raise ValueError(f"O código '{new_role}' é reservado.")
    if PortalRoleDefinition.objects.filter(role=new_role).exists():
        raise ValueError(f"Já existe perfil '{new_role}'.")

    with transaction.atomic():
        row = PortalRoleDefinition.objects.select_for_update().get(role=old_role)
        PortalRoleDefinition.objects.create(
            role=new_role,
            label=row.label,
            area=row.area,
            permissions=list(row.permissions or []),
            granted_routes=row.granted_routes,
            default_scope=row.default_scope,
            is_editable=row.is_editable,
            updated_by=actor or row.updated_by,
        )
        row.delete()

        old_group_name = role_group_name(old_role)
        new_group = ensure_role_group(new_role)
        old_group = Group.objects.filter(name=old_group_name).first()
        if old_group:
            user_ids = list(old_group.user_set.values_list("pk", flat=True))
            if user_ids:
                new_group.user_set.add(*user_ids)
            old_group.delete()

    return PortalRoleDefinition.objects.get(role=new_role)


def normalize_route_path(path: str) -> str:
    value = (path or "").strip()
    if not value:
        raise ValueError("Informe o caminho da rota (ex.: /secao/indicadores/falhas).")
    if not value.startswith("/"):
        value = "/" + value
    # Remove query/hash; mantém placeholders Vue (:module?)
    value = value.split("?", 1)[0].split("#", 1)[0]
    if len(value) > 1:
        value = value.rstrip("/")
    return value


def path_base(path: str) -> str:
    """Caminho sem segmentos dinâmicos (:param / *)."""
    parts: list[str] = []
    for segment in path.strip("/").split("/"):
        if not segment or segment.startswith(":") or segment.startswith("*"):
            break
        parts.append(segment)
    return "/" + "/".join(parts) if parts else "/"


def suggest_route_name(path: str, *, explicit: str | None = None) -> str:
    if explicit:
        name = explicit.strip().lower().replace(" ", "-")
        name = re.sub(r"[^a-z0-9_-]+", "-", name).strip("-")
        if not name:
            raise ValueError("route_name inválido.")
        return name

    normalized = normalize_route_path(path)
    base = path_base(normalized)
    for definition in PORTAL_ROUTE_DEFINITIONS:
        if path_base(definition.path) == base:
            return definition.route_name

    slug = re.sub(r"[^a-z0-9]+", "-", base.strip("/").lower()).strip("-")
    return slug or "rota-custom"


def is_system_route(route_name: str) -> bool:
    return route_name in SYSTEM_ROUTE_NAMES


def validate_area(area: str | None) -> str:
    value = (area or "outros").strip().lower()
    if value not in VALID_AREAS:
        raise ValueError(f"Área inválida. Use uma de: {', '.join(sorted(VALID_AREAS))}")
    return value


def iterable_role_ids_for_access_matrix() -> Iterable[str]:
    return sorted(known_role_ids() - set(LEGACY_ROLES))
