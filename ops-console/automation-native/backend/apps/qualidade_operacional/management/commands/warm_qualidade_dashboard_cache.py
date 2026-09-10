# -*- coding: utf-8 -*-
"""Pré-aquecimento de cache do dashboard Qualidade EO."""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

from django.core.management.base import BaseCommand

from apps.qualidade_operacional.services.dashboard import build_dashboard
from apps.qualidade_operacional.services.performance_cache import bump_quality_cache_version


def _month_bounds(reference: date) -> tuple[date, date]:
    start = reference.replace(day=1)
    if reference.month == 12:
        next_month = date(reference.year + 1, 1, 1)
    else:
        next_month = date(reference.year, reference.month + 1, 1)
    end = next_month - timedelta(days=1)
    return start, end


def warm_scenarios(reference: date | None = None) -> list[dict[str, str]]:
    today = reference or date.today()
    one_month_end = today
    one_month_start = today.replace(day=1)
    three_month_start = (one_month_start - timedelta(days=62)).replace(day=1)
    twelve_month_start = date(one_month_start.year - 1, one_month_start.month, 1)
    scenarios = [
        ("1m", one_month_start, one_month_end),
        ("3m", three_month_start, one_month_end),
        ("12m", twelve_month_start, one_month_end),
    ]
    rows: list[dict[str, str]] = []
    for label, start, end in scenarios:
        for date_axis in ("auditoria", "analise"):
            rows.append(
                {
                    "label": label,
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "date_axis": date_axis,
                }
            )
    return rows


def warm_qualidade_dashboard_cache(*, modules: list[str] | None = None) -> dict:
    modules = modules or ["resumo", "agentes"]
    bump_quality_cache_version()
    results: list[dict] = []
    started = time.perf_counter()
    for spec in warm_scenarios():
        for module in modules:
            params = {
                "module": module,
                "start_date": spec["start_date"],
                "end_date": spec["end_date"],
                "date_axis": spec["date_axis"],
                "grain": "etapa",
            }
            t0 = time.perf_counter()
            payload = build_dashboard(params)
            elapsed_ms = round((time.perf_counter() - t0) * 1000, 1)
            results.append(
                {
                    "module": module,
                    "label": spec["label"],
                    "date_axis": spec["date_axis"],
                    "elapsed_ms": elapsed_ms,
                    "ok": bool(payload.get("ok", True)),
                }
            )
    return {
        "warmed": len(results),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "results": results,
    }


class Command(BaseCommand):
    help = "Pré-aquece cache frio do dashboard Qualidade EO (1/3/12 meses)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--module",
            action="append",
            default=[],
            help="Limita módulos (resumo, agentes). Repetível.",
        )

    def handle(self, *args, **options):
        modules = options["module"] or None
        payload = warm_qualidade_dashboard_cache(modules=modules)
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
