# -*- coding: utf-8 -*-
"""Ativação e desativação segura da fonte PostgreSQL para config D-1."""
from __future__ import annotations

from django.db import transaction

from apps.replicacao_d1.exceptions import ConfigIncompletaError
from apps.replicacao_d1.models import ReplicacaoD1ConfigGeral
from apps.replicacao_d1.services.config_audit import registrar_historico
from apps.replicacao_d1.services.config_snapshot import (
    bump_config_version,
    load_persistent_config,
    validate_persistent_config,
)


@transaction.atomic
def ativar_fonte_banco(user, *, force: bool = False) -> ReplicacaoD1ConfigGeral:
    geral = ReplicacaoD1ConfigGeral.objects.select_for_update().get(pk=ReplicacaoD1ConfigGeral.SINGLETON_PK)
    before = {"fonte_banco_ativa": geral.fonte_banco_ativa}

    cfg = load_persistent_config()
    errors = validate_persistent_config(cfg)
    if errors and not force:
        raise ConfigIncompletaError(errors, message="Configuração incompleta — ativação bloqueada.")

    geral.fonte_banco_ativa = True
    if user and getattr(user, "is_authenticated", False):
        geral.updated_by = user
    geral.save(update_fields=["fonte_banco_ativa", "updated_at", "updated_by"])
    bump_config_version(user=user)

    after = {"fonte_banco_ativa": True, "force": force, "validation_errors": errors if force else []}
    registrar_historico(
        "ReplicacaoD1ConfigGeral",
        geral.pk,
        "ativar_fonte_banco",
        user,
        before,
        after,
    )
    return geral


@transaction.atomic
def desativar_fonte_banco(user) -> ReplicacaoD1ConfigGeral:
    geral = ReplicacaoD1ConfigGeral.objects.select_for_update().get(pk=ReplicacaoD1ConfigGeral.SINGLETON_PK)
    before = {"fonte_banco_ativa": geral.fonte_banco_ativa}
    geral.fonte_banco_ativa = False
    if user and getattr(user, "is_authenticated", False):
        geral.updated_by = user
    geral.save(update_fields=["fonte_banco_ativa", "updated_at", "updated_by"])
    bump_config_version(user=user)
    registrar_historico(
        "ReplicacaoD1ConfigGeral",
        geral.pk,
        "desativar_fonte_banco",
        user,
        before,
        {"fonte_banco_ativa": False},
    )
    return geral
