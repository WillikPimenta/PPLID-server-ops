# -*- coding: utf-8 -*-
"""Duplicar workflow D-1 para replicar o mesmo volume em outra fila/destino BRFlow."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from apps.replicacao_d1.config_models import ReplicacaoD1Workflow
from apps.replicacao_d1.normalization import normalize_key

if TYPE_CHECKING:
    from django.contrib.auth.models import AbstractBaseUser


def _normalize_fila(value: str) -> str:
    return (value or "").strip().casefold()


def _normalize_regra(value: str) -> str:
    return normalize_key(value)


def _resolve_origem(source: ReplicacaoD1Workflow) -> ReplicacaoD1Workflow:
    return source.workflow_origem or source


def find_conflicting_destino(
    *,
    chave_d1: str,
    fila: str,
    nome_regra_brflow: str,
    nome_selenium: str,
    exclude_pk: int | None = None,
) -> ReplicacaoD1Workflow | None:
    """Retorna workflow ATIVO com mesmo destino lógico (D-1 + fila + regra + selenium)."""
    chave_d1 = (chave_d1 or "").strip()
    if not chave_d1:
        return None
    fila_norm = _normalize_fila(fila)
    regra_norm = _normalize_regra(nome_regra_brflow)
    sel_norm = normalize_key(nome_selenium)
    qs = ReplicacaoD1Workflow.objects.filter(
        status=ReplicacaoD1Workflow.STATUS_ATIVO,
        chave_d1_normalizada=chave_d1,
    )
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    for row in qs.only(
        "id",
        "nome_canonico",
        "fila",
        "nome_regra_brflow",
        "nome_selenium",
        "chave_d1_normalizada",
    ):
        if _normalize_fila(row.fila) != fila_norm:
            continue
        if _normalize_regra(row.nome_regra_brflow) != regra_norm:
            continue
        if normalize_key(row.nome_selenium) != sel_norm:
            continue
        return row
    return None


def _strip_duplicate_suffix(nome: str) -> str:
    nome = (nome or "").strip()
    return re.sub(r"\s*\[[^\]]+\](?:\s*#\d+)?\s*$", "", nome).strip() or nome


def suggest_duplicate_nome_canonico(
    source: ReplicacaoD1Workflow,
    *,
    fila: str,
    nome_regra_brflow: str = "",
) -> str:
    origem = _resolve_origem(source)
    base = _strip_duplicate_suffix(origem.nome_canonico)
    fila_label = (fila or source.fila or "destino").strip()
    regra = (nome_regra_brflow or "").strip()
    suffix = f"{fila_label} — {regra}" if regra else fila_label
    candidate = f"{base} [{suffix}]"
    if not ReplicacaoD1Workflow.objects.filter(chave_normalizada=normalize_key(candidate)).exists():
        return candidate
    n = 2
    while True:
        alt = f"{base} [{suffix} #{n}]"
        if not ReplicacaoD1Workflow.objects.filter(chave_normalizada=normalize_key(alt)).exists():
            return alt
        n += 1


def duplicate_workflow(
    source: ReplicacaoD1Workflow,
    *,
    fila: str,
    nome_regra_brflow: str = "",
    nome_canonico: str = "",
    nome_selenium: str = "",
    status: str | None = None,
    user: AbstractBaseUser | None = None,
) -> ReplicacaoD1Workflow:
    origem = _resolve_origem(source)
    fila_final = (fila or source.fila or "G auditoria").strip()
    regra_final = (nome_regra_brflow or "").strip()
    if _normalize_fila(fila_final) == "redoc" and not regra_final:
        raise ValueError("nome_regra_brflow é obrigatório para fila Redoc.")

    nome_d1 = (source.nome_d1 or origem.nome_d1 or origem.nome_canonico).strip()
    nome_sel = (nome_selenium or source.nome_selenium or origem.nome_selenium or nome_d1).strip()
    chave_d1 = normalize_key(nome_d1)

    conflict = find_conflicting_destino(
        chave_d1=chave_d1,
        fila=fila_final,
        nome_regra_brflow=regra_final,
        nome_selenium=nome_sel,
    )
    if conflict:
        raise ValueError(
            f"Já existe destino ativo para este workflow D-1 ({conflict.nome_canonico})."
        )

    nome_final = (nome_canonico or suggest_duplicate_nome_canonico(
        source,
        fila=fila_final,
        nome_regra_brflow=regra_final,
    )).strip()
    if not nome_final:
        raise ValueError("nome_canonico inválido.")

    if ReplicacaoD1Workflow.objects.filter(chave_normalizada=normalize_key(nome_final)).exists():
        raise ValueError("nome_canonico já cadastrado.")

    target_status = status or (
        ReplicacaoD1Workflow.STATUS_ATIVO
        if source.status == ReplicacaoD1Workflow.STATUS_ATIVO
        else source.status
    )

    return ReplicacaoD1Workflow.objects.create(
        nome_canonico=nome_final,
        nome_d1=nome_d1,
        nome_selenium=nome_sel,
        nome_regra_brflow=regra_final,
        fila=fila_final,
        cliente=source.cliente,
        status=target_status,
        amostra_pct_especial=source.amostra_pct_especial,
        amostra_100=source.amostra_100,
        usar_arquivo_csv=source.usar_arquivo_csv,
        nome_observado_original=source.nome_observado_original,
        workflow_origem=origem,
        created_by=user,
        updated_by=user,
    )
