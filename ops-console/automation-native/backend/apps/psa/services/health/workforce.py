# -*- coding: utf-8 -*-
from __future__ import annotations

from django.test import Client

from apps.psa.services.health.base import check_path

SUITE = "workforce"

QUICK = (("/api/v1/dashboard/overview/", "dashboard-overview"),)

FULL_EXTRA = (("/api/v1/agents/?page_size=5", "agents-list"),)


def run_quick(client: Client) -> list[dict]:
    return [check_path(client, p, label, suite=SUITE) for p, label in QUICK]


def run_full(client: Client) -> list[dict]:
    items = run_quick(client)
    for path, label in FULL_EXTRA:
        items.append(check_path(client, path, label, suite=SUITE))
    return items
