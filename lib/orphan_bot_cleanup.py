"""Small, platform-neutral helpers for orphan robot-runner decisions."""
from __future__ import annotations

from typing import Any, Mapping


def classify_bot(parent_pid: int | None, chain: list[int], managed_pids: set[int]) -> str:
    """Classify a sampled process without making a destructive OS call."""
    if any(pid in managed_pids for pid in chain):
        return "managed"
    # A robot is safe to preserve only when a known PPLID supervisor appears
    # in its chain; an existing but unrelated parent is still orphaned.
    return "orphan"


def build_summary(items: list[Mapping[str, Any]], *, mode: str = "cleanup", scanned_at: str = "") -> dict[str, Any]:
    return {
        "mode": mode,
        "scannedAt": scanned_at,
        "detected": sum(1 for item in items if item.get("classification") == "orphan"),
        "stopped": sum(1 for item in items if item.get("result") == "stopped"),
        "failed": sum(1 for item in items if item.get("result") == "failed"),
        "items": list(items),
    }
