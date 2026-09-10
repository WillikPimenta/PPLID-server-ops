# -*- coding: utf-8 -*-
"""Validação rápida do módulo falhas (localhost) — delega suite falhas ao PSA."""
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.contrib.auth import get_user_model
from django.test import Client

from apps.psa.services.health import falhas, save_health_report, summarize_results
import time

User = get_user_model()


def main():
    user = User.objects.filter(username="gerente.dev").first()
    if not user:
        print("FAIL: usuario gerente.dev nao encontrado")
        sys.exit(1)

    client = Client()
    client.force_login(user)

    started = time.perf_counter()
    results = falhas.run_full(client)
    duration_ms = int((time.perf_counter() - started) * 1000)

    if os.environ.get("PPLID_PSA_SAVE_REPORT", "1") == "1":
        save_health_report(results, duration_ms=duration_ms)

    failed = 0
    for item in results:
        ok = item["ok"]
        mark = "OK" if ok else "FAIL"
        extra = item.get("extra") or ""
        suffix = f" {extra}" if extra else ""
        print(f"{mark} {item['status']} {item['path']}{suffix}")
        if not ok:
            failed += 1

    summary = summarize_results(results)
    print(f"\nFalhas suite: {summary['passed']}/{summary['total']} OK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
