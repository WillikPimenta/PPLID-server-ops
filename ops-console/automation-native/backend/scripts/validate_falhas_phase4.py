# -*- coding: utf-8 -*-
"""Fase 4 — validação dos agregadores dashboard-summary e diagnostico."""
from __future__ import annotations

import json
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

User = get_user_model()

FILTERS = "start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral"


def check_dashboard(c: Client) -> tuple[int, str]:
    r = c.get(f"/api/v1/falhas/dashboard-summary/?{FILTERS}", HTTP_HOST="localhost")
    if r.status_code != 200:
        return 1, f"dashboard-summary HTTP {r.status_code}"
    data = json.loads(r.content)
    required = (
        "kpis", "executive", "charts_preview", "top_reincidentes",
        "alerts", "comparativo_preview", "meta",
    )
    missing = [k for k in required if k not in data]
    if missing:
        return 1, f"dashboard-summary missing keys: {missing}"
    if "total_falhas" not in data["kpis"]:
        return 1, "dashboard-summary kpis sem total_falhas"
    if data["comparativo_preview"] is None:
        return 1, "dashboard-summary comparativo_preview null (esperado global)"
    if "duration_ms" not in data.get("meta", {}):
        return 1, "dashboard-summary missing meta.duration_ms"
    extra = (
        f" falhas={data['kpis'].get('total_falhas')}"
        f" alerts={len(data.get('alerts') or [])}"
        f" reinc={len(data.get('top_reincidentes') or [])}"
    )
    return 0, extra


def check_diagnostico(c: Client) -> tuple[int, str]:
    r = c.get(f"/api/v1/falhas/diagnostico/?{FILTERS}", HTTP_HOST="localhost")
    if r.status_code != 200:
        return 1, f"diagnostico HTTP {r.status_code}"
    data = json.loads(r.content)
    required = (
        "rank_delta", "matrix", "clientes_workflows", "consolidado",
        "support_bridge_preview", "training_bridge_preview", "meta",
    )
    missing = [k for k in required if k not in data]
    if missing:
        return 1, f"diagnostico missing keys: {missing}"
    if "duration_ms" not in data.get("meta", {}):
        return 1, "diagnostico missing meta.duration_ms"
    if "por_localidade" in data.get("consolidado", {}):
        return 1, "diagnostico should omit consolidado.por_localidade"
    bridge_r3 = data["support_bridge_preview"]["summary"].get("agentes_com_regra3_e_falha", -1)
    bridge_cap = data["training_bridge_preview"]["summary"].get("agentes_criticos_com_pendencia", -1)
    extra = f" bridge_r3={bridge_r3} bridge_cap={bridge_cap}"
    return 0, extra


def check_reincidence(c: Client) -> tuple[int, str]:
    r = c.get(f"/api/v1/falhas/reincidence-summary/?{FILTERS}", HTTP_HOST="localhost")
    if r.status_code != 200:
        return 1, f"reincidence-summary HTTP {r.status_code}"
    data = json.loads(r.content)
    required = ("by_turno", "list", "ult3m", "charts", "meta")
    missing = [k for k in required if k not in data]
    if missing:
        return 1, f"reincidence-summary missing keys: {missing}"
    if "duration_ms" not in data.get("meta", {}):
        return 1, "reincidence-summary missing meta.duration_ms"
    extra = f" list={data['list'].get('count', 0)} ms={data['meta']['duration_ms']}"
    return 0, extra


def check_legacy_endpoints(c: Client) -> tuple[int, str]:
    legacy = [
        f"/api/v1/falhas/kpis/?{FILTERS}",
        f"/api/v1/falhas/rank-delta/?{FILTERS}",
        f"/api/v1/falhas/consolidado/?{FILTERS}",
        f"/api/v1/falhas/support/?{FILTERS}&bridge_only=true",
        f"/api/v1/falhas/treinamentos/?{FILTERS}&bridge_only=true",
        "/api/v1/falhas/base-overview/",
    ]
    failed = []
    for path in legacy:
        r = c.get(path, HTTP_HOST="localhost")
        if r.status_code != 200:
            failed.append(f"{path} -> {r.status_code}")
    if failed:
        return 1, "; ".join(failed)
    return 0, f"{len(legacy)} legados OK"


def main():
    c = Client()
    user = User.objects.filter(username="gerente.dev").first()
    if not user:
        print("FAIL: usuario gerente.dev nao encontrado")
        sys.exit(1)
    c.force_login(user)

    checks = [
        ("dashboard-summary", check_dashboard),
        ("diagnostico", check_diagnostico),
        ("reincidence-summary", check_reincidence),
        ("legacy", check_legacy_endpoints),
    ]
    failed = 0
    for name, fn in checks:
        code, msg = fn(c)
        ok = code == 0
        print(f"{'OK' if ok else 'FAIL'} {name}{msg}")
        if not ok:
            failed += 1
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
