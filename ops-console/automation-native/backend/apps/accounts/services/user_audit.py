# -*- coding: utf-8 -*-
from __future__ import annotations

from apps.accounts.models import UserChangeHistorico
from apps.access.constants import ALL_ROLES

FIELD_LABELS = {
    "is_active": "Status",
    "email": "E-mail",
    "first_name": "Nome",
    "last_name": "Sobrenome",
    "roles": "Perfis RBAC",
    "must_change_password": "Trocar senha no login",
    "username": "Matrícula",
}

ROLE_LABELS = {role: role.replace("_", " ").title() for role in ALL_ROLES}


def _display_value(field_name: str, value: str) -> str:
    if field_name == "is_active":
        return "Ativo" if value in {"True", "true", "1"} else "Inativo"
    if field_name == "must_change_password":
        return "Sim" if value in {"True", "true", "1"} else "Não"
    if field_name == "roles":
        parts = [p.strip() for p in (value or "").split(",") if p.strip()]
        if not parts:
            return "-"
        return ", ".join(ROLE_LABELS.get(p, p) for p in parts)
    text = (value or "").strip()
    return text if text else "-"


def log_user_change(
    *,
    target_user,
    actor,
    action: str,
    field_name: str = "",
    old_value: str = "",
    new_value: str = "",
) -> UserChangeHistorico | None:
    old_norm = (old_value or "").strip()
    new_norm = (new_value or "").strip()
    if field_name and old_norm == new_norm:
        return None
    return UserChangeHistorico.objects.create(
        target_user=target_user,
        actor=actor,
        action=action,
        field_name=field_name,
        old_value=old_norm,
        new_value=new_norm,
    )


def historico_summary(entry: UserChangeHistorico) -> str:
    label = FIELD_LABELS.get(entry.field_name, entry.field_name or "Campo")
    old_disp = _display_value(entry.field_name, entry.old_value)
    new_disp = _display_value(entry.field_name, entry.new_value)

    if entry.action == UserChangeHistorico.ACTION_PASSWORD_RESET:
        return "Senha redefinida para padrão do ambiente (troca obrigatória no próximo login)"
    if entry.action == UserChangeHistorico.ACTION_CREATE:
        return f"Conta criada ({new_disp or entry.target_user.username})"
    if entry.action == UserChangeHistorico.ACTION_ACTIVATE:
        return "Conta ativada"
    if entry.action == UserChangeHistorico.ACTION_DEACTIVATE:
        return "Conta desativada"
    if entry.action == UserChangeHistorico.ACTION_BULK:
        return f"{label}: {old_disp} → {new_disp}"
    if entry.action == UserChangeHistorico.ACTION_ROLES:
        return f"Perfis alterados: {old_disp} → {new_disp}"
    return f"{label} alterado de \"{old_disp}\" para \"{new_disp}\""
