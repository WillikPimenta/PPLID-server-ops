"""Adapter: grupos legados Falhas → perfis RBAC."""

from __future__ import annotations

from apps.access.constants import (
    LEGACY_GROUP_BRASILIA,
    LEGACY_GROUP_GLOBAL,
    LEGACY_GROUP_SAO_CARLOS,
    ROLE_QUAL_GERENCIA,
    ROLE_QUAL_USUARIO,
    SCOPE_GLOBAL,
    SCOPE_LOCALITY,
)
from apps.falhas_criticas.constants import GROUP_TO_LOCALIDADE


def roles_from_legacy_groups(group_names: set[str]) -> tuple[set[str], dict[str, str]]:
    """
    Retorna (roles, scope_overrides) a partir de grupos legados.
    scope_overrides mapeia módulo → escopo quando aplicável.
    """
    roles: set[str] = set()
    scope_overrides: dict[str, str] = {}

    if LEGACY_GROUP_GLOBAL in group_names:
        roles.add(ROLE_QUAL_GERENCIA)
        scope_overrides["qual.falhas"] = SCOPE_GLOBAL
        return roles, scope_overrides

    for group_name, locality in GROUP_TO_LOCALIDADE.items():
        if group_name in group_names:
            roles.add(ROLE_QUAL_USUARIO)
            scope_overrides["qual.falhas"] = SCOPE_LOCALITY
            scope_overrides["qual.falhas.locality"] = locality
            break

    return roles, scope_overrides
