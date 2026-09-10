# -*- coding: utf-8 -*-
"""Opções de filtro de coluna da grade de workflows (cadastro D-1)."""
from __future__ import annotations

from apps.replicacao_d1.models import ReplicacaoD1Workflow

EMPTY_LABEL = "—"


def workflow_amostra_label(amostra_pct_especial, amostra_100: bool) -> str:
    if amostra_pct_especial is not None:
        return f"{amostra_pct_especial}%"
    if amostra_100:
        return "100%"
    return "Padrão"


def cliente_segmento_nome(cliente) -> str:
    if not cliente:
        return ""
    if cliente.segmento_id and cliente.segmento:
        return cliente.segmento.nome
    return cliente.segmento_nome or ""


def cliente_categoria_nome(cliente) -> str:
    if not cliente:
        return ""
    if cliente.categoria_id and cliente.categoria:
        return cliente.categoria.nome
    return cliente.categoria_nome or ""


def _sorted_labels(values: set[str]) -> list[str]:
    return sorted(values, key=lambda value: value.casefold())


def build_workflow_column_filter_options() -> dict[str, list[str]]:
    qs = ReplicacaoD1Workflow.objects.select_related(
        "cliente",
        "cliente__segmento",
        "cliente__categoria",
    )

    nomes: set[str] = set()
    clientes: set[str] = set()
    segmentos: set[str] = set()
    categorias: set[str] = set()
    filas: set[str] = set()
    amostras: set[str] = set()
    has_null_cliente = False

    for wf in qs.iterator(chunk_size=500):
        if wf.nome_canonico:
            nomes.add(wf.nome_canonico)
        if wf.fila:
            filas.add(wf.fila)
        amostras.add(workflow_amostra_label(wf.amostra_pct_especial, wf.amostra_100))
        if wf.cliente_id and wf.cliente:
            clientes.add(wf.cliente.nome)
            segmentos.add(cliente_segmento_nome(wf.cliente) or EMPTY_LABEL)
            categorias.add(cliente_categoria_nome(wf.cliente) or EMPTY_LABEL)
        else:
            has_null_cliente = True
            segmentos.add(EMPTY_LABEL)
            categorias.add(EMPTY_LABEL)

    if has_null_cliente:
        clientes.add(EMPTY_LABEL)

    return {
        "nome_canonico": _sorted_labels(nomes),
        "cliente_nome": _sorted_labels(clientes),
        "segmento_nome": _sorted_labels(segmentos),
        "categoria_nome": _sorted_labels(categorias),
        "fila": _sorted_labels(filas),
        "amostra": _sorted_labels(amostras),
    }
