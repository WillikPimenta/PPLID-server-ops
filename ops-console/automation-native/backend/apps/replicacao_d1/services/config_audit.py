# -*- coding: utf-8 -*-
"""Auditoria de alterações na configuração D-1."""
from __future__ import annotations

from typing import Any

from apps.replicacao_d1.models import ReplicacaoD1ConfigHistorico


def registrar_historico(
    entidade: str,
    entidade_id: str | int,
    operacao: str,
    user,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    lote_id: str = "",
) -> ReplicacaoD1ConfigHistorico:
    return ReplicacaoD1ConfigHistorico.objects.create(
        entidade=str(entidade),
        entidade_id=str(entidade_id),
        operacao=str(operacao),
        usuario=user if getattr(user, "is_authenticated", False) else None,
        valores_anteriores=dict(before or {}),
        valores_novos=dict(after or {}),
        lote_id=str(lote_id or ""),
    )
