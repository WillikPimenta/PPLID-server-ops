# -*- coding: utf-8 -*-
"""Fase 2.5 — QA funcional HTTP do portal Falhas (gerente.dev)."""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path

from apps.psa.services.health.falhas import (
    DEFAULT_FILTER_QS,
    FILTER_VARIANTS,
    LEGACY_PHASE4,
    MODULE_ENDPOINTS,
    evaluate_response,
)

DEFAULT_BASE = "http://127.0.0.1:8001"

LEGACY_REDIRECTS = {
    "evolucao": "dashboard",
    "clientes": "diagnostico",
    "ult3m": "reincidencia",
    "contestacoes": "base-auditoria",
}


def resolve_base() -> str:
    return (os.environ.get("PPLID_BACKEND_URL") or DEFAULT_BASE).rstrip("/")


def http_req(cj, url, method="GET", data=None, headers=None):
    h = dict(headers or {})
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    with opener.open(req, timeout=120) as resp:
        return resp.status, resp.read(), dict(resp.headers)


def login(cj, base: str) -> dict:
    _, body, _ = http_req(cj, f"{base}/api/v1/auth/csrf/")
    csrf = json.loads(body).get("csrfToken", "")
    status, body, _ = http_req(
        cj,
        f"{base}/api/v1/auth/login/",
        method="POST",
        data={"username": "gerente.dev", "password": "dev12345"},
        headers={"X-CSRFToken": csrf, "Referer": base},
    )
    if status != 200:
        raise RuntimeError(f"login failed {status}: {body[:200]!r}")
    return json.loads(body)


def check_path_http(cj, base: str, path: str, label: str) -> dict:
    try:
        status, body, hdrs = http_req(cj, f"{base}{path}", headers={"Referer": base})
        ok, extra = evaluate_response(path, status, body, hdrs.get("Content-Type", ""))
        return {"ok": ok, "label": label, "status": status, "path": path, "extra": extra, "duration_ms": 0}
    except urllib.error.HTTPError as e:
        return {"ok": False, "label": label, "status": e.code, "path": path, "extra": str(e.reason), "duration_ms": 0}
    except Exception as e:
        return {"ok": False, "label": label, "status": 0, "path": path, "extra": str(e), "duration_ms": 0}


def main():
    base = resolve_base()
    cj = http.cookiejar.CookieJar()
    results: list[dict] = []

    print(f"=== Fase 2.5 QA — base={base} ===\n")

    try:
        user = login(cj, base)
        print(f"OK login gerente.dev scope={user.get('scope', '?')}\n")
    except Exception as exc:
        print(f"FAIL login: {exc}")
        sys.exit(1)

    for item in (
        check_path_http(cj, base, "/api/v1/falhas/me/", "me"),
        check_path_http(cj, base, "/api/v1/falhas/filters/", "filters"),
    ):
        results.append(item)

    for module, paths in MODULE_ENDPOINTS.items():
        for tpl in paths:
            path = tpl.format(f=DEFAULT_FILTER_QS) if "{f}" in tpl else tpl
            results.append(check_path_http(cj, base, path, f"module:{module}"))

    for path in LEGACY_PHASE4:
        results.append(check_path_http(cj, base, path, "legacy-phase4"))

    for i, f in enumerate(FILTER_VARIANTS):
        results.append(check_path_http(cj, base, f"/api/v1/falhas/kpis/?{f}", f"filters-variant-{i}"))

    results.append(
        check_path_http(
            cj,
            base,
            f"/api/v1/falhas/reincidence/?{DEFAULT_FILTER_QS}&filtro=reincidentes",
            "reincidence-oficiais",
        )
    )
    results.append(
        check_path_http(
            cj,
            base,
            f"/api/v1/falhas/reincidence/?{DEFAULT_FILTER_QS}&filtro=alta_frequencia",
            "reincidence-alta-freq",
        )
    )

    for loc, label in (
        ("Geral", "base-overview-geral"),
        ("Bras%C3%ADlia", "base-overview-bsb"),
        ("S%C3%A3o%20Carlos", "base-overview-sc"),
    ):
        path = f"/api/v1/falhas/base-overview/?localidade={loc}"
        item = check_path_http(cj, base, path, label)
        results.append(item)

    _, body, _ = http_req(cj, f"{base}/api/v1/falhas/executive/history/", headers={"Referer": base})
    hist = json.loads(body)
    items = hist.get("items") or []
    if items:
        archive_id = items[0]["id"]
        status, _, _ = http_req(
            cj,
            f"{base}/api/v1/falhas/executive/history/{archive_id}/download/",
            headers={"Referer": base},
        )
        results.append(
            {
                "ok": status == 200,
                "label": "executive-download",
                "status": status,
                "path": f"/history/{archive_id}/download/",
                "extra": "",
                "duration_ms": 0,
            }
        )
    else:
        results.append(
            {
                "ok": True,
                "label": "executive-download",
                "status": 0,
                "path": "skip-empty",
                "extra": "no archives",
                "duration_ms": 0,
            }
        )

    print("\n--- Redirects legados (mapa esperado) ---")
    for legacy, target in LEGACY_REDIRECTS.items():
        print(f"  /secao/indicadores/falhas/{legacy} -> {target}")

    frontend = os.environ.get("PPLID_FRONTEND_URL", "http://localhost:5174").rstrip("/")
    spa_routes = [
        "/secao/indicadores/falhas",
        "/secao/indicadores/falhas/diagnostico",
        "/secao/indicadores/falhas/reincidencia",
        "/secao/indicadores/falhas/comparativo",
        "/secao/indicadores/falhas/suporte",
        "/secao/indicadores/falhas/treinamentos",
        "/secao/indicadores/falhas/base-auditoria",
        "/secao/indicadores/falhas/chamados",
    ]
    print(f"\n--- Frontend SPA ({frontend}) ---")
    for route in spa_routes:
        try:
            req = urllib.request.Request(f"{frontend}{route}", headers={"Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read()
                ok = resp.status == 200
                has_app = b'id="app"' in html or b"id='app'" in html
                results.append(
                    {
                        "ok": ok and has_app,
                        "label": f"spa:{route}",
                        "status": resp.status,
                        "path": route,
                        "extra": " app" if has_app else " NO_APP",
                        "duration_ms": 0,
                    }
                )
        except Exception as e:
            results.append(
                {
                    "ok": False,
                    "label": f"spa:{route}",
                    "status": 0,
                    "path": route,
                    "extra": str(e),
                    "duration_ms": 0,
                }
            )

    print("\n--- Resultados API ---")
    failed = 0
    for item in results:
        mark = "OK" if item["ok"] else "FAIL"
        if not item["ok"]:
            failed += 1
        extra = item.get("extra") or ""
        print(f"{mark} [{item['label']}] {item['status']} {item['path']}{extra}")

    report_path = Path(__file__).resolve().parents[2] / "documentation" / "fase25-qa-report.json"
    report_path.write_text(
        json.dumps(
            [
                {
                    "ok": r["ok"],
                    "label": r["label"],
                    "status": r["status"],
                    "path": r["path"],
                    "extra": r["extra"],
                }
                for r in results
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"\nRelatório JSON: {report_path}")
    print(f"\nTotal: {len(results) - failed}/{len(results)} OK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    BACKEND_ROOT = Path(__file__).resolve().parents[1]
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    main()
