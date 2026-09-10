# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.test import Client

from apps.cyber_psa.services.env_profile import get_env_profile
from apps.cyber_psa.services.registry import findings_path, load_registry
from apps.cyber_psa.services.risk_overrides import apply_overrides_to_risks
from apps.cyber_psa.services.scanner import apply_governance_auto_checks, run_security_scan


def history_dir() -> Path:
    return findings_path().parent / "cyber-psa" / "history"


def _risk_from_finding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "title": item["title"],
        "severity": item["severity"],
        "impact": item.get("description", ""),
        "mitigation": item.get("remediation", ""),
        "status": item.get("status", "open"),
        "source": "auto",
        "owner": "ADM",
        "category": item.get("category", ""),
        "extra": item.get("extra", ""),
    }


def merge_risks(
    manual_risks: list[dict],
    previous_auto: list[dict],
    current_scan: list[dict],
) -> tuple[list[dict], dict[str, int], dict[str, set[str]]]:
    prev_by_id = {r["id"]: r for r in previous_auto}
    current_by_id = {item["id"]: item for item in current_scan}

    merged_auto: list[dict] = []
    stats = {"new": 0, "resolved": 0, "open": 0, "unchanged": 0}
    diff_tags: dict[str, set[str]] = {}

    for item in current_scan:
        risk = _risk_from_finding(item)
        prev = prev_by_id.get(item["id"])
        tags: set[str] = set()
        if not item["ok"]:
            risk["status"] = "open"
            stats["open"] += 1
            if prev and prev.get("status") == "open":
                stats["unchanged"] += 1
            else:
                stats["new"] += 1
                tags.add("new")
        else:
            risk["status"] = "resolved"
            if prev and prev.get("status") == "open":
                stats["resolved"] += 1
                tags.add("resolved")
        if tags:
            diff_tags[risk["id"]] = tags
        merged_auto.append(risk)

    for old_id, old in prev_by_id.items():
        if old_id not in current_by_id and old.get("status") == "open":
            resolved = dict(old)
            resolved["status"] = "resolved"
            resolved["extra"] = (resolved.get("extra") or "") + " | removido do scan"
            merged_auto.append(resolved)
            stats["resolved"] += 1
            diff_tags[old_id] = {"resolved"}

    all_risks = list(manual_risks) + merged_auto
    return all_risks, stats, diff_tags


def _attach_diff_tags(risks: list[dict], diff_tags: dict[str, set[str]]) -> list[dict]:
    result = []
    for risk in risks:
        row = dict(risk)
        tags = diff_tags.get(str(row.get("id", "")))
        if tags:
            row["diff_tags"] = sorted(tags)
        result.append(row)
    return result


def list_scan_history() -> list[dict[str, Any]]:
    directory = history_dir()
    if not directory.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        summary = data.get("summary") or {}
        items.append(
            {
                "id": path.stem,
                "generated_at": data.get("generated_at"),
                "generated_by": data.get("generated_by"),
                "passed": summary.get("passed", 0),
                "total": summary.get("total", 0),
                "open_risks": summary.get("open_risks", 0),
                "diff": summary.get("diff"),
            }
        )
    return items


def load_scan_history_item(scan_id: str) -> dict | None:
    if not scan_id or ".." in scan_id or "/" in scan_id or "\\" in scan_id:
        return None
    path = history_dir() / f"{scan_id}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def run_and_save_scan(client: Client, user: AbstractBaseUser) -> dict[str, Any]:
    registry = load_registry()
    started = time.perf_counter()
    scan_items = run_security_scan(client, user)
    duration_ms = int((time.perf_counter() - started) * 1000)

    previous = {}
    path = findings_path()
    if path.is_file():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            previous = {}

    prev_auto = [r for r in (previous.get("risks") or []) if r.get("source") == "auto"]
    manual = registry.get("manual_risks") or []
    risks, diff_stats, diff_tags = merge_risks(manual, prev_auto, scan_items)
    risks = _attach_diff_tags(risks, diff_tags)
    risks = apply_overrides_to_risks(risks)
    governance = apply_governance_auto_checks(registry.get("governance") or [], scan_items)

    summary = {
        "passed": sum(1 for i in scan_items if i["ok"]),
        "failed": sum(1 for i in scan_items if not i["ok"]),
        "total": len(scan_items),
        "all_ok": all(i["ok"] for i in scan_items),
        "open_risks": sum(1 for r in risks if r.get("status") == "open"),
        "diff": diff_stats,
    }

    username = getattr(user, "username", "unknown")
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_by": username,
        "duration_ms": duration_ms,
        "env_profile": get_env_profile(),
        "summary": summary,
        "scan_items": scan_items,
        "governance": governance,
        "risks": risks,
        "risk_diff_tags": {k: sorted(v) for k, v in diff_tags.items()},
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    hist = history_dir()
    hist.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    hist_file = hist / f"{stamp}.json"
    hist_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    return report
