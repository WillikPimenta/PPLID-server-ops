# -*- coding: utf-8 -*-
"""Descoberta idempotente de workflows pendentes."""
from __future__ import annotations

from django.db import IntegrityError, transaction

from apps.replicacao_d1.models import ReplicacaoD1Cliente, ReplicacaoD1Workflow
from apps.replicacao_d1.normalization import normalize_key


@transaction.atomic
def get_or_create_workflow_pendente(
    nome_observado: str,
    *,
    cliente_nome: str | None = None,
) -> tuple[ReplicacaoD1Workflow, bool]:
    """Cria workflow pendente/inativo ou retorna existente (race-safe)."""
    nome = str(nome_observado or "").strip()
    if not nome:
        raise ValueError("nome_observado obrigatório")
    chave = normalize_key(nome)
    cliente = None
    if cliente_nome:
        cliente = ReplicacaoD1Cliente.objects.filter(
            chave_normalizada=normalize_key(cliente_nome)
        ).first()

    existing = ReplicacaoD1Workflow.objects.filter(chave_normalizada=chave).first()
    if existing:
        return existing, False

    try:
        wf = ReplicacaoD1Workflow.objects.create(
            nome_canonico=nome,
            nome_observado_original=nome,
            chave_normalizada=chave,
            status=ReplicacaoD1Workflow.STATUS_PENDENTE,
            ativo=False,
            cliente=cliente,
        )
        return wf, True
    except IntegrityError:
        wf = ReplicacaoD1Workflow.objects.get(chave_normalizada=chave)
        return wf, False


def workflows_ativos_para_planejamento():
    """Queryset de workflows elegíveis ao planejamento (ativos, status ATIVO)."""
    return ReplicacaoD1Workflow.objects.filter(
        ativo=True,
        status=ReplicacaoD1Workflow.STATUS_ATIVO,
    ).select_related("cliente")


def workflows_pendentes_queryset():
    return ReplicacaoD1Workflow.objects.filter(
        status=ReplicacaoD1Workflow.STATUS_PENDENTE,
    ).select_related("cliente")
