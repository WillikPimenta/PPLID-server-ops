"""Escopos de import do workbook Identificação dos Processos."""

from __future__ import annotations

from typing import Literal

from apps.dimensoes_processos.services.identificacao_processos.reader import WorkbookData

ImportScope = Literal["cadastros", "projecao_sla", "full"]

IMPORT_SCOPES: frozenset[str] = frozenset({"cadastros", "projecao_sla", "full"})

SCOPE_LABELS: dict[str, str] = {
    "cadastros": "Só cadastros",
    "projecao_sla": "Cadastros + Projeção SLA",
    "full": "Completo",
}

# Abas obrigatórias por escopo (nomes lógicos do reader).
REQUIRED_SHEETS_BY_SCOPE: dict[str, tuple[str, ...]] = {
    "cadastros": ("clientes", "workflow", "nivel_hierarquico"),
    "projecao_sla": ("clientes", "workflow", "nivel_hierarquico", "projecao_sla"),
    "full": ("clientes", "workflow", "nivel_hierarquico", "projecao_sla"),
}

OPTIONAL_SHEETS_BY_SCOPE: dict[str, tuple[str, ...]] = {
    "cadastros": ("produto",),
    "projecao_sla": ("produto", "grupo_servico", "servico"),
    "full": (),
}


def normalize_import_scope(raw: str | None) -> ImportScope:
    value = (raw or "full").strip().casefold()
    aliases = {
        "cadastro": "cadastros",
        "sla": "projecao_sla",
        "projecao": "projecao_sla",
        "complete": "full",
        "completo": "full",
    }
    normalized = aliases.get(value, value)
    return normalized if normalized in IMPORT_SCOPES else "full"


def filter_workbook_for_scope(data: WorkbookData, scope: ImportScope) -> WorkbookData:
    """Retorna cópia do workbook limitada às abas do escopo."""
    if scope == "full":
        return data

    if scope == "cadastros":
        return WorkbookData(
            sheet_names=data.sheet_names,
            produtos=data.produtos if data.produtos else [],
            clientes=data.clientes,
            niveis_hierarquicos=data.niveis_hierarquicos,
            workflows=data.workflows,
        )

    # projecao_sla: cadastros mínimos + dependências opcionais + SLA.
    return WorkbookData(
        sheet_names=data.sheet_names,
        produtos=data.produtos,
        grupos_servico=data.grupos_servico,
        servicos=data.servicos,
        clientes=data.clientes,
        niveis_hierarquicos=data.niveis_hierarquicos,
        workflows=data.workflows,
        projecao_sla=data.projecao_sla,
    )
