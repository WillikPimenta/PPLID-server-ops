# -*- coding: utf-8 -*-
"""CLI: automação Jira Suporte Claro via Okta (subprocess do portal)."""
from __future__ import annotations

import json
import os
import sys

from app.core.path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from app.bots.suporte_claro_jira.jira_ui import run_jira_batch_flow, run_jira_create_flow


def main() -> int:
    payload_raw = os.environ.get("SUPORTE_CLARO_JIRA_PAYLOAD", "").strip()
    if not payload_raw:
        print(json.dumps({"ok": False, "message": "Payload SUPORTE_CLARO_JIRA_PAYLOAD ausente."}))
        return 1

    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "message": f"Payload JSON inválido: {exc}"}))
        return 1

    matricula = (os.environ.get("OKTA_USER") or os.environ.get("ROBOT_USER") or "").strip()
    senha = os.environ.get("OKTA_PASS") or os.environ.get("ROBOT_PASS") or ""
    if not matricula or not senha:
        print(json.dumps({"ok": False, "message": "Credenciais Okta ausentes (OKTA_USER/OKTA_PASS)."}))
        return 1

    headless = (os.environ.get("SUPORTE_CLARO_JIRA_HEADLESS", "0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
    }

    profile_key = str(payload.get("profile") or "planejamento")
    kind = str(payload.get("kind") or "").strip().lower()
    items = payload.get("items")

    if kind == "batch" or (isinstance(items, list) and items):
        result = run_jira_batch_flow(
            matricula,
            senha,
            profile_key=profile_key,
            items=list(items or []),
            headless=headless,
        )
    else:
        result = run_jira_create_flow(
            matricula,
            senha,
            profile_key=profile_key,
            summary=str(payload.get("summary") or "").strip(),
            description=str(payload.get("description") or "").strip(),
            headless=headless,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
