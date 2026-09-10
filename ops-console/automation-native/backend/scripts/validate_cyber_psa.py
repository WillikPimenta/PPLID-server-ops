# -*- coding: utf-8 -*-
"""Validação PSA Cyber do portal PPLID."""
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

from apps.cyber_psa.services.risk_store import run_and_save_scan

User = get_user_model()


def main():
    user = User.objects.filter(is_staff=True).first()
    if not user:
        user = User.objects.filter(username="gerente.dev").first()
    if not user:
        print("FAIL: nenhum usuario staff ou gerente.dev encontrado")
        sys.exit(1)

    client = Client()
    client.force_login(user)
    report = run_and_save_scan(client, user)
    summary = report["summary"]
    failed = summary["failed"]

    for item in report["scan_items"]:
        mark = "OK" if item["ok"] else "FAIL"
        extra = item.get("extra") or ""
        suffix = f" {extra}" if extra else ""
        print(f"{mark} [{item.get('category')}] {item['title']}{suffix}")

    print(f"\nPerfil: {report.get('env_profile', 'local')}")
    print(f"Total: {summary['passed']}/{summary['total']} OK ({report['duration_ms']}ms)")
    print(f"Riscos abertos: {summary.get('open_risks', 0)}")
    print(f"Diff: {summary.get('diff')}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
