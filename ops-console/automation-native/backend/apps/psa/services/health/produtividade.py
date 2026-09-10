# -*- coding: utf-8 -*-
from __future__ import annotations

from django.test import Client

from apps.psa.services.health.base import check_path

SUITE = "produtividade"
FILTER_QS = "start_date=2026-06-01&end_date=2026-06-25"

QUICK = (
    ("/api/v1/produtividade/status/", "status"),
    ("/api/v1/produtividade/filters/", "filters"),
)

FULL_EXTRA = (
    (f"/api/v1/produtividade/kpis/?{FILTER_QS}", "kpis"),
    (f"/api/v1/produtividade/dashboard/?{FILTER_QS}", "dashboard"),
)


def run_quick(client: Client) -> list[dict]:
    return [check_path(client, p, label, suite=SUITE) for p, label in QUICK]


def run_full(client: Client) -> list[dict]:
    items = run_quick(client)
    for path, label in FULL_EXTRA:
        items.append(check_path(client, path, label, suite=SUITE))
    return items
