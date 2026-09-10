# -*- coding: utf-8 -*-
"""Exportação CSV dos cadastros e dados operacionais D-1."""
from __future__ import annotations

import csv
import io
import json
from typing import Iterable

from django.db.models import Count

from apps.replicacao_d1.models import (
    ReplicacaoD1Categoria,
    ReplicacaoD1Cliente,
    ReplicacaoD1ConfigHistorico,
    ReplicacaoD1EscalaDia,
    ReplicacaoD1LedgerConsumo,
    ReplicacaoD1MetaMensal,
    ReplicacaoD1Segmento,
    ReplicacaoD1Workflow,
)


def _cliente_segmento_nome(cliente) -> str:
    if not cliente:
        return ""
    if cliente.segmento_id and cliente.segmento:
        return cliente.segmento.nome
    return cliente.segmento_nome or ""


def _cliente_categoria_nome(cliente) -> str:
    if not cliente:
        return ""
    if cliente.categoria_id and cliente.categoria:
        return cliente.categoria.nome
    return cliente.categoria_nome or ""


def build_config_csv(resource: str) -> tuple[str, str]:
    resource = str(resource or "").strip().lower()
    builders = {
        "segmentos": _segmentos,
        "categorias": _categorias,
        "clientes": _clientes,
        "workflows": _workflows,
        "escala": _escala,
        "metas": _metas,
        "ledger": _ledger,
        "historico": _historico,
    }
    builder = builders.get(resource)
    if builder is None:
        raise ValueError(f"Tabela inválida para exportação: {resource}")
    headers, rows = builder()
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\n")
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_safe_cell(value) for value in row])
    return f"replicacao_d1_{resource}.csv", "\ufeff" + output.getvalue()


def _safe_cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if text.startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def _segmentos() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1Segmento.objects.annotate(
        categorias_count=Count("categorias", distinct=True),
        clientes_count=Count("clientes", distinct=True),
    ).order_by("nome")
    return ["id", "nome", "chave_normalizada", "categorias", "clientes", "ativo", "updated_at"], (
        (row.id, row.nome, row.chave_normalizada, row.categorias_count, row.clientes_count, row.ativo, row.updated_at)
        for row in qs
    )


def _categorias() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1Categoria.objects.select_related("segmento").annotate(
        clientes_count=Count("clientes", distinct=True),
    ).order_by("nome")
    return ["id", "nome", "chave_normalizada", "segmento_nome", "clientes", "ativo", "updated_at"], (
        (row.id, row.nome, row.chave_normalizada, row.segmento.nome if row.segmento_id else "", row.clientes_count, row.ativo, row.updated_at)
        for row in qs
    )


def _clientes() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1Cliente.objects.select_related("segmento", "categoria").annotate(
        workflows_count=Count("workflows", distinct=True)
    ).order_by("nome")
    return ["id", "nome", "chave_normalizada", "segmento_nome", "categoria_nome", "meta_mensal", "workflows", "ativo", "updated_at"], (
        (
            row.id,
            row.nome,
            row.chave_normalizada,
            row.segmento.nome if row.segmento_id else row.segmento_nome,
            row.categoria.nome if row.categoria_id else row.categoria_nome,
            row.meta_mensal,
            row.workflows_count,
            row.ativo,
            row.updated_at,
        )
        for row in qs
    )


def _workflows() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1Workflow.objects.select_related(
        "cliente",
        "cliente__segmento",
        "cliente__categoria",
    ).order_by("nome_canonico")
    return [
        "id", "nome_canonico", "nome_d1", "nome_selenium", "nome_regra_brflow", "workflow_origem_id",
        "cliente_nome",
        "segmento_nome", "categoria_nome", "fila", "status",
        "amostra_pct_especial", "amostra_100", "usar_arquivo_csv", "ativo", "updated_at",
    ], (
        (
            row.id, row.nome_canonico, row.nome_d1, row.nome_selenium, row.nome_regra_brflow,
            row.workflow_origem_id,
            row.cliente.nome if row.cliente_id else "",
            _cliente_segmento_nome(row.cliente if row.cliente_id else None),
            _cliente_categoria_nome(row.cliente if row.cliente_id else None),
            row.fila, row.status,
            row.amostra_pct_especial, row.amostra_100, row.usar_arquivo_csv,
            row.ativo, row.updated_at,
        )
        for row in qs
    )


def _escala() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1EscalaDia.objects.order_by("data")
    return [
        "id", "data", "auditores_brflow", "auditores_case",
        "auditores_bio", "auditores_redoc", "updated_at",
    ], (
        (
            row.id, row.data, row.auditores_brflow, row.auditores_case,
            row.auditores_bio, row.auditores_redoc, row.updated_at,
        )
        for row in qs
    )


def _metas() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1MetaMensal.objects.select_related("cliente").order_by("-competencia", "cliente__nome")
    return ["id", "cliente_nome", "competencia", "meta", "updated_at"], (
        (row.id, row.cliente.nome, row.competencia, row.meta, row.updated_at)
        for row in qs
    )


def _ledger() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1LedgerConsumo.objects.order_by("-data_execucao", "workflow_chave")
    return [
        "id", "competencia", "data_execucao", "workflow_nome", "workflow_chave", "cliente_nome",
        "protocolos", "origem", "run_id", "observacao", "ajuste", "config_version",
    ], (
        (
            row.id, row.competencia, row.data_execucao, row.workflow_nome, row.workflow_chave,
            row.cliente_nome, row.protocolos, row.origem, row.run_id, row.observacao,
            row.ajuste, row.config_version,
        )
        for row in qs
    )


def _historico() -> tuple[list[str], Iterable[tuple]]:
    qs = ReplicacaoD1ConfigHistorico.objects.select_related("usuario").order_by("-created_at")
    return [
        "id", "created_at", "usuario", "entidade", "entidade_id", "operacao",
        "valores_anteriores", "valores_novos", "lote_id",
    ], (
        (
            row.id, row.created_at, row.usuario.username if row.usuario_id else "", row.entidade,
            row.entidade_id, row.operacao,
            json.dumps(row.valores_anteriores, ensure_ascii=False, default=str),
            json.dumps(row.valores_novos, ensure_ascii=False, default=str), row.lote_id,
        )
        for row in qs
    )
