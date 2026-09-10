# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from typing import Any

from django.test import Client


def parse_json_response(content: bytes, content_type: str) -> dict | list | None:
    if "json" not in (content_type or ""):
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def check_path(
    client: Client,
    path: str,
    label: str,
    *,
    suite: str,
    evaluator=None,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = client.get(path, HTTP_HOST="localhost")
    duration_ms = int((time.perf_counter() - started) * 1000)
    if evaluator:
        ok, extra = evaluator(path, response.status_code, response.content, response.get("Content-Type", ""))
    else:
        ok = response.status_code == 200
        extra = "" if ok else f"status={response.status_code}"
    return {
        "ok": ok,
        "label": label,
        "status": response.status_code,
        "path": path,
        "extra": extra,
        "duration_ms": duration_ms,
        "suite": suite,
    }


def tag_suite(items: list[dict[str, Any]], suite: str) -> list[dict[str, Any]]:
    for item in items:
        item.setdefault("suite", suite)
    return items
