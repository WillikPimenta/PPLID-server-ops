# -*- coding: utf-8 -*-
"""Feature flags de rollout D-1 (Fase 15.3)."""
from __future__ import annotations

from django.conf import settings


def _flag(name: str, default: bool = False) -> bool:
    return bool(getattr(settings, name, default))


def ingestion_enabled() -> bool:
    """Nova ingestão bot→DB com BotDataIngestion."""
    return _flag("REPLICACAO_D1_FF_NEW_INGESTION", default=True)


def reconciliation_enabled() -> bool:
    """Reconciliação protocolo run ↔ replicados."""
    return _flag("REPLICACAO_D1_FF_NEW_RECONCILIATION", default=True)


def dashboard_db_enabled() -> bool:
    """Dashboard oficial lê somente banco (sem Excel/parquet)."""
    return _flag("REPLICACAO_D1_FF_DASHBOARD_DB", default=True)


def dashboard_no_file_fallback() -> bool:
    """Desativa fallback para arquivos no dashboard."""
    return _flag("REPLICACAO_D1_FF_DASHBOARD_NO_FILES", default=False)


def shadow_mode_enabled() -> bool:
    """Compara fluxo novo vs legado sem dupla escrita."""
    return _flag("REPLICACAO_D1_FF_SHADOW_MODE", default=False)


def optimized_planning_enabled() -> bool:
    """Habilita o planejador otimizado; desligado por padrÃ£o para rollout seguro."""
    return _flag("REPLICACAO_D1_FF_OPTIMIZED_PLANNING", default=False)


def optimized_planning_shadow_enabled() -> bool:
    """Executa comparaÃ§Ã£o shadow do planejador otimizado sem trocar a saÃ­da oficial."""
    return _flag("REPLICACAO_D1_FF_OPTIMIZED_PLANNING_SHADOW", default=False)


def rollout_flags_snapshot() -> dict[str, bool]:
    return {
        "new_ingestion": ingestion_enabled(),
        "new_reconciliation": reconciliation_enabled(),
        "dashboard_db": dashboard_db_enabled(),
        "dashboard_no_files": dashboard_no_file_fallback(),
        "shadow_mode": shadow_mode_enabled(),
        "optimized_planning": optimized_planning_enabled(),
        "optimized_planning_shadow": optimized_planning_shadow_enabled(),
    }
