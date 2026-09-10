# -*- coding: utf-8 -*-
from __future__ import annotations

from django.test import Client

from apps.psa.services.health.base import check_path

SUITE = "core"

QUICK = (
    ("/api/v1/health/", "health"),
    ("/api/v1/auth/me/", "auth-me"),
)

FULL_EXTRA = (
    ("/api/v1/news/", "news-list"),
)


def run_quick(client: Client) -> list[dict]:
    return [check_path(client, p, label, suite=SUITE) for p, label in QUICK]


def run_full(client: Client) -> list[dict]:
    items = run_quick(client)
    for path, label in FULL_EXTRA:
        items.append(check_path(client, path, label, suite=SUITE))
    return items
