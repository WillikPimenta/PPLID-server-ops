# -*- coding: utf-8 -*-
"""Mapeamentos de filtros do formulário de pesquisa BRFlow."""

from app.config import brflow


def css_select_filtro_pesquisa(select_name: str) -> str:
    """CSS do <select> do formulário de pesquisa (não do painel de edição)."""
    mapping = {
        "codClienteDestino": brflow.B_replicacao_cliente_destino_pesquisa,
        "codWorkFlowDestino": brflow.B_replicacao_workflow_destino_pesquisa,
    }
    css = mapping.get(select_name)
    if not css:
        raise ValueError(f"Select de pesquisa não mapeado: {select_name}")
    return css
