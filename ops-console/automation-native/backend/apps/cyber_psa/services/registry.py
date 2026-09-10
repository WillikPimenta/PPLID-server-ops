# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path

REGISTRY_PATH = Path(__file__).resolve().parent.parent / "registry.json"
REPO_ROOT = Path(__file__).resolve().parents[4]
FINDINGS_PATH = REPO_ROOT / "documentation" / "cyber-psa-findings.json"


def load_registry() -> dict:
    if not REGISTRY_PATH.is_file():
        return {
            "meta": {"title": "PSA Cyber — PPLID"},
            "governance": [],
            "presentation": [],
            "manual_risks": [],
            "documents": [],
        }
    data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def load_findings() -> dict | None:
    if not FINDINGS_PATH.is_file():
        return None
    try:
        data = json.loads(FINDINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def findings_path() -> Path:
    return FINDINGS_PATH
