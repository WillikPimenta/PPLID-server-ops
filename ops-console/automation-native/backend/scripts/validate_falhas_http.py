# -*- coding: utf-8 -*-
"""Validação via HTTP real do módulo falhas_criticas."""
import argparse
import json
import os
import sys
import urllib.request
import http.cookiejar

DEFAULT_BASE = "http://127.0.0.1:8001"


def resolve_base_url(cli_base: str | None) -> str:
    if cli_base:
        return cli_base.rstrip("/")
    env_base = os.environ.get("PPLID_BACKEND_URL", "").strip()
    if env_base:
        return env_base.rstrip("/")
    return DEFAULT_BASE.rstrip("/")


def req(cj, url, method="GET", data=None, headers=None):
    h = dict(headers or {})
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
    body = json.dumps(data).encode() if data is not None else None
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    with opener.open(r, timeout=120) as resp:
        return resp.status, resp.read(), resp.headers


def main():
    parser = argparse.ArgumentParser(description="Valida rotas HTTP do módulo falhas.")
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base do backend (ex.: http://127.0.0.1:8001). "
        "Fallback: env PPLID_BACKEND_URL ou default DEV.",
    )
    args = parser.parse_args()
    base = resolve_base_url(args.base_url)

    cj = http.cookiejar.CookieJar()
    status, body, hdrs = req(cj, f"{base}/api/v1/auth/csrf/")
    csrf = json.loads(body).get("csrfToken", "")
    status, body, _ = req(
        cj,
        f"{base}/api/v1/auth/login/",
        method="POST",
        data={"username": "gerente.dev", "password": "dev12345"},
        headers={"X-CSRFToken": csrf, "Referer": base},
    )
    if status != 200:
        print(f"FAIL login {status}: {body[:200]}")
        sys.exit(1)
    print(f"OK login gerente.dev (base={base})")

    paths = [
        "/api/v1/falhas/me/",
        "/api/v1/falhas/filters/",
        "/api/v1/falhas/kpis/?start_date=2026-06-01&end_date=2026-06-30&oficial=true",
        "/api/v1/falhas/support/?start_date=2026-06-01&end_date=2026-06-30",
        "/api/v1/falhas/treinamentos/?start_date=2026-06-01&end_date=2026-06-30",
        "/api/v1/falhas/reincidence/?start_date=2026-06-01&end_date=2026-06-30",
        "/api/v1/falhas/comparativo-bsb-sc/?start_date=2026-06-01&end_date=2026-06-30",
        "/falhas/",
    ]
    failed = 0
    for path in paths:
        status, body, hdrs = req(cj, f"{base}{path}", headers={"Referer": base})
        ok = status == 200
        extra = ""
        ct = hdrs.get("Content-Type", "")
        if ok and "json" in ct:
            data = json.loads(body)
            if "scope" in data:
                extra = (
                    f" scope={data.get('scope')} can_sync={data.get('can_sync')}"
                    f" total_failures={data.get('total_failures')}"
                )
            elif "total_falhas" in data:
                extra = f" total_falhas={data.get('total_falhas')}"
            elif "localidades" in data:
                extra = f" locs={len(data.get('localidades') or [])}"
            elif "brasilia" in data:
                extra = " comparativo OK"
        elif ok and path == "/falhas/":
            extra = " portal OK" if b"portal-tpl" in body else " SEM TEMPLATE"
            if b"PPLID_HOME_URL" in body or b"pplidHomeUrl" in body:
                extra += " voltar-menu OK"
        print(f"{'OK' if ok else 'FAIL'} {status} {path}{extra}")
        if not ok:
            failed += 1
            print(" ", body[:150])
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
