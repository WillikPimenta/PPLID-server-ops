"""Matriz de distribuição e exportação GED × portal para Reinspeção / Compliance."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from django.utils import timezone

from apps.auditoria.services.reinspecao_sla import (
    brasilia_date,
    data_final_sla,
    is_falha_aberta_para_sla,
    sla_farol,
    sla_panel_reference_dates,
)
from apps.rotina_bruto.services.parsers import parse_protocolo


def _format_br_date(value: date | None) -> str:
    if not value:
        return ""
    return value.strftime("%d/%m/%Y")


def build_distribuicao_matrix(*, falhas: list) -> dict[str, Any]:
    """Cruzamento data de contestação × data de vencimento para protocolos em aberto."""
    counts: dict[tuple[str, str], int] = {}
    row_totals: dict[str, int] = defaultdict(int)
    col_totals: dict[str, int] = defaultdict(int)
    details: list[dict[str, Any]] = []
    seen_protocolos: set[str] = set()

    for falha in falhas:
        if not is_falha_aberta_para_sla(falha):
            continue
        protocolo = (getattr(falha, "protocolo", None) or "").strip()
        if not protocolo:
            continue
        data_contestacao = getattr(falha, "data_contestacao", None)
        if not data_contestacao:
            continue
        contestacao = brasilia_date(data_contestacao)
        vencimento = data_final_sla(contestacao)
        if not vencimento:
            continue

        row_key = contestacao.isoformat()
        col_key = vencimento.isoformat()
        counts[(row_key, col_key)] = counts.get((row_key, col_key), 0) + 1
        row_totals[row_key] += 1
        col_totals[col_key] += 1

        if protocolo not in seen_protocolos:
            seen_protocolos.add(protocolo)
            details.append(
                {
                    "protocolo": protocolo,
                    "data_contestacao": contestacao.isoformat(),
                    "data_vencimento": vencimento.isoformat(),
                    "data_contestacao_label": _format_br_date(contestacao),
                    "data_vencimento_label": _format_br_date(vencimento),
                    "descricao_irregularidades": (
                        getattr(falha, "descricao_irregularidades", None) or ""
                    ).strip(),
                    "auditor": (getattr(falha, "auditor", None) or "").strip(),
                    "agente": (getattr(falha, "usuario", None) or "").strip(),
                }
            )

    rows = sorted(row_totals.keys())
    cols = sorted(col_totals.keys())
    today = brasilia_date(timezone.now())
    cells: list[dict[str, Any]] = []
    for row_key in rows:
        for col_key in cols:
            value = counts.get((row_key, col_key), 0)
            if value <= 0:
                continue
            row_date = date.fromisoformat(row_key)
            col_date = date.fromisoformat(col_key)
            cells.append(
                {
                    "data_contestacao": row_key,
                    "data_vencimento": col_key,
                    "data_contestacao_label": _format_br_date(row_date),
                    "data_vencimento_label": _format_br_date(col_date),
                    "total": value,
                    "farol": sla_farol(row_date, as_of=today),
                }
            )

    grand_total = sum(row_totals.values())
    return {
        "title": "Reinspeção",
        "rows": [
            {
                "key": key,
                "label": _format_br_date(date.fromisoformat(key)),
                "total": row_totals[key],
                "farol": sla_farol(date.fromisoformat(key), as_of=today),
            }
            for key in rows
        ],
        "cols": [
            {
                "key": key,
                "label": _format_br_date(date.fromisoformat(key)),
                "total": col_totals[key],
            }
            for key in cols
        ],
        "cells": cells,
        "row_totals": row_totals,
        "col_totals": col_totals,
        "grand_total": grand_total,
        "details": sorted(details, key=lambda item: (item["data_contestacao"], item["protocolo"])),
        "sla_reference_dates": sla_panel_reference_dates(),
        "generated_at": timezone.now().isoformat(),
    }


def protocolo_int_or_none(value: str | None) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    return parse_protocolo(text)
