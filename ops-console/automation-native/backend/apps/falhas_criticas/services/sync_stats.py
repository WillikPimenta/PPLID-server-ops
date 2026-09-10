# -*- coding: utf-8 -*-
"""Montagem de estatísticas da importação Excel → PostgreSQL."""
from __future__ import annotations

import pandas as pd


def _resumo_int(resumo: pd.DataFrame | None, col: str, default: int = 0) -> int:
    if resumo is None or resumo.empty or col not in resumo.columns:
        return default
    try:
        return int(resumo[col].iloc[0])
    except (TypeError, ValueError):
        return default


def _sheet(
    *,
    rows_read: int | None = 0,
    rows_valid: int | None = 0,
    rows_persisted: int | None = 0,
    rows_removed_by_contestation: int | None = None,
    rows_removed_by_removed_failures: int | None = None,
    warnings: list[str] | None = None,
) -> dict:
    out: dict = {
        'rows_read': rows_read,
        'rows_valid': rows_valid,
        'rows_persisted': rows_persisted,
        'warnings': list(warnings or []),
    }
    if rows_removed_by_contestation is not None:
        out['rows_removed_by_contestation'] = rows_removed_by_contestation
    if rows_removed_by_removed_failures is not None:
        out['rows_removed_by_removed_failures'] = rows_removed_by_removed_failures
    return out


def build_sync_stats(
    *,
    contest_resumo: pd.DataFrame | None,
    removidas_resumo: pd.DataFrame | None,
    df_base_len: int,
    failures_persisted: int,
    hc_rows_read: int,
    agents_persisted: int,
    support_rows_read: int,
    support_persisted: int,
    training_rows_read: int,
    training_persisted: int,
    contest_rows_read: int,
    contest_persisted: int,
    extra_warnings: list[str] | None = None,
) -> dict:
    """Monta payload JSON de stats a partir de contagens coletadas no fluxo de sync."""
    base_before = _resumo_int(contest_resumo, 'Registros base antes', df_base_len)
    removed_contest = _resumo_int(contest_resumo, 'Linhas removidas')
    removed_removidas = _resumo_int(removidas_resumo, 'Linhas removidas')
    base_after_rules = _resumo_int(removidas_resumo, 'Registros base depois', df_base_len)

    base_warnings: list[str] = [
        'Linhas lidas da Base consideram apenas registros com Data de Análise válida '
        '(linhas sem data são descartadas na leitura).',
    ]
    if removed_contest == 0 and removed_removidas == 0 and base_before > base_after_rules:
        base_warnings.append(
            'Diferença entre linhas lidas e persistidas pode incluir linhas descartadas '
            'sem contagem separada disponível.'
        )

    sheets = {
        'base': _sheet(
            rows_read=base_before,
            rows_valid=base_after_rules,
            rows_persisted=failures_persisted,
            rows_removed_by_contestation=removed_contest,
            rows_removed_by_removed_failures=removed_removidas,
            warnings=base_warnings,
        ),
        'hc': _sheet(
            rows_read=hc_rows_read,
            rows_valid=agents_persisted,
            rows_persisted=agents_persisted,
        ),
        'support': _sheet(
            rows_read=support_rows_read,
            rows_valid=support_persisted,
            rows_persisted=support_persisted,
        ),
        'training': _sheet(
            rows_read=training_rows_read,
            rows_valid=training_persisted,
            rows_persisted=training_persisted,
            warnings=(
                ['Aba Treinamentos ausente ou vazia.']
                if training_rows_read == 0 and training_persisted == 0
                else []
            ),
        ),
        'contestations': _sheet(
            rows_read=contest_rows_read,
            rows_valid=contest_persisted,
            rows_persisted=contest_persisted,
            warnings=(
                ['Abas de contestação ausentes ou vazias.']
                if contest_rows_read == 0 and contest_persisted == 0
                else []
            ),
        ),
    }

    rows_removed = removed_contest + removed_removidas
    totals = {
        'rows_read': sum(int(s.get('rows_read') or 0) for s in sheets.values()),
        'rows_valid': sum(int(s.get('rows_valid') or 0) for s in sheets.values()),
        'rows_removed': rows_removed,
        'rows_persisted': sum(int(s.get('rows_persisted') or 0) for s in sheets.values()),
    }

    if extra_warnings:
        sheets['base']['warnings'].extend(extra_warnings)

    return {'sheets': sheets, 'totals': totals}


def build_sync_stats_empty(message: str) -> dict:
    empty = _sheet(rows_read=0, rows_valid=0, rows_persisted=0, warnings=[message])
    return {
        'sheets': {
            'base': empty,
            'hc': dict(empty),
            'support': dict(empty),
            'training': dict(empty),
            'contestations': dict(empty),
        },
        'totals': {
            'rows_read': 0,
            'rows_valid': 0,
            'rows_removed': 0,
            'rows_persisted': 0,
        },
    }
