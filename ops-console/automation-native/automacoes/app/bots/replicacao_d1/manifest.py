# -*- coding: utf-8 -*-
"""Manifesto versionado de execução D-1."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_STDOUT_PREFIX = "REPLICACAO_D1_MANIFEST|"


def write_manifest_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    fd, tmp_name = tempfile.mkstemp(prefix=".manifest_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def build_manifest_payload(
    *,
    run_id: str,
    run_result: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
    counts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "snapshot": snapshot or {},
        "result": run_result,
        "counts": counts or {},
    }


def emit_manifest_event(run_id: str, manifest_path: Path) -> None:
    print(f"{MANIFEST_STDOUT_PREFIX}{run_id}|{manifest_path}", flush=True)
