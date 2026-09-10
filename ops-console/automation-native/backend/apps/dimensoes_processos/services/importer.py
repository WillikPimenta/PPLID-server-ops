"""Importador do workbook Identificação dos Processos."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from apps.dimensoes_processos.services.identificacao_processos.capacity_invalidation_helper import (
    invalidate_capacity_after_import,
)
from apps.dimensoes_processos.services.identificacao_processos.import_scope import (
    ImportScope,
    normalize_import_scope,
)
from apps.dimensoes_processos.services.identificacao_processos.reader import DEFAULT_XLSX, load_workbook_data
from apps.dimensoes_processos.services.identificacao_processos.sync import persist_workbook_data

__all__ = ["DEFAULT_XLSX", "import_identificacao_processos"]

log = logging.getLogger(__name__)

ImportMode = Literal["sync", "replace"]


def import_identificacao_processos(
    path: Path | str | None = None,
    *,
    mode: ImportMode = "sync",
    clear: bool | None = None,
    import_scope: ImportScope | str = "full",
) -> dict[str, Any]:
    """Importa dimensões e regras do workbook Identificação dos Processos.

    ``clear=True`` (legado) equivale a ``mode='replace'``.
    ``clear=False`` equivale a ``mode='sync'``.
    """
    if clear is not None:
        mode = "replace" if clear else "sync"

    scope = normalize_import_scope(import_scope)
    path = Path(path) if path else DEFAULT_XLSX
    log.info("Abrindo %s (mode=%s scope=%s)", path, mode, scope)
    data = load_workbook_data(path)
    result = persist_workbook_data(data, mode=mode, import_scope=scope)
    summary = result.as_summary()
    summary["capacity_snapshots_invalidated"] = invalidate_capacity_after_import(result)
    log.info("Import concluído: %s", summary)
    return summary
