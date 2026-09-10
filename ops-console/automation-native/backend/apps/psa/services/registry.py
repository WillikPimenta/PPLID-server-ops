# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "registry.json"
REPO_ROOT = Path(__file__).resolve().parents[4]
LIVE_REPORT_PATH = REPO_ROOT / "documentation" / "psa-live-report.json"


def load_registry() -> dict:
    if not REGISTRY_PATH.is_file():
        return {
            "meta": {"title": "PSA Portal PPLID"},
            "modules": [],
            "roadmap": [],
            "risks": [],
            "backlog": [],
            "links": [],
        }
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_last_health_report() -> dict | None:
    if not LIVE_REPORT_PATH.is_file():
        return None
    try:
        data = json.loads(LIVE_REPORT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def live_report_path() -> Path:
    return LIVE_REPORT_PATH


def summarize_modules(modules: list[dict]) -> dict:
    by_section: dict[str, dict[str, int]] = {}
    totals = {"live": 0, "placeholder": 0, "partial": 0}
    for mod in modules:
        section = str(mod.get("section") or "outros")
        status = str(mod.get("status") or "placeholder")
        if status not in totals:
            status = "placeholder"
        totals[status] = totals.get(status, 0) + 1
        bucket = by_section.setdefault(section, {"live": 0, "placeholder": 0, "partial": 0})
        bucket[status] = bucket.get(status, 0) + 1
    return {"totals": totals, "by_section": by_section}
