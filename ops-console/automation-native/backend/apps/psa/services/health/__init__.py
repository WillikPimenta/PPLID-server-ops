# -*- coding: utf-8 -*-
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.test import Client

from apps.automacoes.permissions import can_access_automacoes
from . import automacoes, core, escala_flex, falhas, produtividade, spa, workforce
from apps.psa.services.registry import live_report_path


def run_quick_health(client: Client, user: AbstractBaseUser) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    items.extend(core.run_quick(client))
    items.extend(workforce.run_quick(client))
    items.extend(falhas.run_quick(client))
    items.extend(produtividade.run_quick(client))
    items.extend(escala_flex.run_quick(client))
    if can_access_automacoes(user):
        items.extend(automacoes.run_quick(client))
    return items


def run_full_checks(client: Client, user: AbstractBaseUser, *, include_spa: bool = True) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    items.extend(core.run_full(client))
    items.extend(workforce.run_full(client))
    items.extend(falhas.run_full(client))
    items.extend(produtividade.run_full(client))
    items.extend(escala_flex.run_full(client))
    if can_access_automacoes(user):
        items.extend(automacoes.run_full(client))
    if include_spa:
        items.extend(spa.run_full())
    return items


def summarize_results(items: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(1 for item in items if item.get("ok"))
    failed = len(items) - passed
    suites: dict[str, dict[str, int]] = {}
    for item in items:
        suite = str(item.get("suite") or "unknown")
        bucket = suites.setdefault(suite, {"passed": 0, "failed": 0, "total": 0})
        bucket["total"] += 1
        if item.get("ok"):
            bucket["passed"] += 1
        else:
            bucket["failed"] += 1
    return {
        "passed": passed,
        "failed": failed,
        "total": len(items),
        "all_ok": failed == 0,
        "suites": suites,
    }


def save_health_report(items: list[dict[str, Any]], *, duration_ms: int) -> dict[str, Any]:
    summary = summarize_results(items)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_ms": duration_ms,
        "summary": summary,
        "items": items,
    }
    path = live_report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    import json

    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def run_and_save_full_checks(
    client: Client,
    user: AbstractBaseUser,
    *,
    include_spa: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    items = run_full_checks(client, user, include_spa=include_spa)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return save_health_report(items, duration_ms=duration_ms)
