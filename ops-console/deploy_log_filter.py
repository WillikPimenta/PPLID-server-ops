"""Filtro temporal de logs de deploy por etapa (espelha deploy-progress.js)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

DEPLOY_STEP_ORDER = [
    "prepare",
    "git_fetch",
    "deps_backend",
    "build_backend",
    "build_frontend",
    "validate",
    "restart_services",
    "health_check",
    "publish_done",
]

TOLERANCE_MS = 1000


def parse_log_timestamp(time_str: str | None) -> float:
    if not time_str:
        return float("nan")
    normalized = str(time_str).strip().replace(" ", "T")
    try:
        return datetime.fromisoformat(normalized).timestamp() * 1000
    except ValueError:
        return float("nan")


def parse_step_timestamp(iso_str: str | None) -> float:
    if not iso_str:
        return float("nan")
    try:
        return datetime.fromisoformat(iso_str).timestamp() * 1000
    except ValueError:
        return float("nan")


def find_next_step_on_log_file(step: dict[str, Any], all_steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    step_map = {s["id"]: s for s in all_steps}
    try:
        idx = DEPLOY_STEP_ORDER.index(step["id"])
    except ValueError:
        return None
    for step_id in DEPLOY_STEP_ORDER[idx + 1 :]:
        candidate = step_map.get(step_id)
        if candidate and candidate.get("logFile") == step.get("logFile"):
            return candidate
    return None


def get_step_log_window(step: dict[str, Any], all_steps: list[dict[str, Any]]) -> dict[str, float] | None:
    if not step.get("startedAt"):
        return None
    start = parse_step_timestamp(step["startedAt"])
    if start != start:  # NaN
        return None
    nxt = find_next_step_on_log_file(step, all_steps)
    if nxt and nxt.get("startedAt"):
        end_exclusive = parse_step_timestamp(nxt["startedAt"])
    elif step.get("finishedAt"):
        end_exclusive = parse_step_timestamp(step["finishedAt"]) + 1000
    else:
        end_exclusive = datetime.now().timestamp() * 1000 + 1000
    if end_exclusive != end_exclusive or end_exclusive <= start:
        end_exclusive = start + 1000
    return {"start": start, "end_exclusive": end_exclusive}


def filter_logs_for_step_group(
    parsed: list[dict[str, Any]],
    group_steps: list[dict[str, Any]],
    all_steps: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    if not group_steps:
        return []
    if all(not s.get("startedAt") for s in group_steps):
        return None
    seen: set[str] = set()
    filtered: list[dict[str, Any]] = []
    for step in group_steps:
        window = get_step_log_window(step, all_steps)
        if not window:
            continue
        for idx, row in enumerate(parsed or []):
            key = f"{row.get('time', '')}|{row.get('text') or row.get('raw', '')}|{idx}"
            if key in seen:
                continue
            ts = parse_log_timestamp(row.get("time"))
            if ts != ts:
                continue
            if ts >= window["start"] - TOLERANCE_MS and ts < window["end_exclusive"] + TOLERANCE_MS:
                seen.add(key)
                filtered.append(row)
    filtered.sort(key=lambda row: parse_log_timestamp(row.get("time")))
    return filtered


def classify_plain_log_line(line: str) -> dict[str, str]:
    import re

    text = str(line or "")
    pipe_match = re.match(r"^\[([\d\-: ]+)\]\s*\[(INFO|OK|WARN|ERROR|SUCCESS)\]\s*(.+)$", text)
    if pipe_match:
        return {"time": pipe_match.group(1).strip(), "level": pipe_match.group(2), "text": pipe_match.group(3).strip()}
    if re.search(r"(?:^|\s)(?:error|falhou|failed|fatal)(?:\s|:|$)", text, re.I):
        return {"level": "ERROR", "text": text}
    if re.search(r"\|\s*\d+\s*\+", text) or re.match(r"^\s*\+", text):
        return {"level": "OK", "text": text}
    if re.search(r"\|\s*\d+\s*\-", text) or re.match(r"^\s*\-", text):
        return {"level": "WARN", "text": text}
    return {"level": "INFO", "text": text}


def is_history_row_active(row: dict, env_data: dict) -> bool:
    active_sha = env_data.get("deployedSha") or env_data.get("activeSha") or ""
    if not active_sha or active_sha == "—" or not row.get("sha") or row.get("sha") == "—":
        return False
    row_sha = row["sha"]
    sha_match = row_sha == active_sha or row_sha.startswith(active_sha) or active_sha.startswith(row_sha)
    if not sha_match:
        return False
    if row.get("isRunning") or row.get("statusKey") == "failed" or row.get("result") == "failed":
        return False
    if row.get("statusKey") in ("failed", "error", "building", "validating", "promoting", "deploying"):
        return False
    if row.get("isPrimary"):
        return env_data.get("phase") != "failed" and env_data.get("lastDeployResult") != "failed"
    return row.get("statusKey") == "success" or row.get("result") == "success"


def should_follow_log_scroll(
    at_bottom: bool | None,
    scroll_top: float,
    scroll_height: float,
    client_height: float,
    *,
    user_locked: bool = False,
    interacting: bool = False,
) -> bool:
    """Retorna True quando o auto-scroll deve ir ao final."""
    if user_locked or interacting:
        return False
    if at_bottom is None:
        return True
    if at_bottom is False:
        return False
    distance = scroll_height - scroll_top - client_height
    return distance < 40
