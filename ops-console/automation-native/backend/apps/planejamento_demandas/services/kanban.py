"""Agrupamento de demandas Jira para visão Kanban."""

from __future__ import annotations

from apps.planejamento_demandas.services.status_utils import (
    classify_status_name,
    inactive_open_statuses,
)

KANBAN_STATUS_HINTS: tuple[tuple[str, int], ...] = (
    ("backlog", 10),
    ("to do", 20),
    ("open", 25),
    ("nova", 30),
    ("novo", 30),
    ("aberto", 35),
    ("em andamento", 40),
    ("in progress", 45),
    ("progresso", 50),
    ("aguardando", 60),
    ("pausado", 65),
    ("on hold", 70),
    ("waiting", 75),
    ("blocked", 80),
    ("bloqueado", 85),
    ("review", 90),
    ("revisão", 95),
    ("teste", 100),
    ("done", 200),
    ("conclu", 210),
    ("resolv", 220),
    ("cancel", 300),
    ("rejeit", 310),
    ("arquiv", 320),
)


def status_column_order(status_name: str) -> tuple[int, str]:
    lower = (status_name or "").strip().lower()
    order = 150
    for hint, hint_order in KANBAN_STATUS_HINTS:
        if hint in lower:
            order = min(order, hint_order)
    kind = classify_status_name(status_name)
    if kind == "done":
        order = max(order, 200)
    elif kind == "cancelled":
        order = max(order, 300)
    elif lower in inactive_open_statuses():
        order = max(order, 60)
    return (order, status_name or "")


def build_kanban_columns(
    items: list,
    *,
    per_column_limit: int = 40,
) -> list[dict]:
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item.status_name or "Sem status", []).append(item)

    columns: list[dict] = []
    for status_name in sorted(grouped.keys(), key=status_column_order):
        col_items = grouped[status_name]
        col_items.sort(
            key=lambda row: (
                row.updated_at_jira or row.created_at_jira,
                row.issue_key,
            ),
            reverse=True,
        )
        sample = col_items[0]
        columns.append(
            {
                "status_name": status_name,
                "status_kind": sample.status_kind,
                "total": len(col_items),
                "items": col_items[:per_column_limit],
                "has_more": len(col_items) > per_column_limit,
            }
        )
    return columns
