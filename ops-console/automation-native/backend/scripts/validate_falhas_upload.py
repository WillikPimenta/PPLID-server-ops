# -*- coding: utf-8 -*-
"""Valida upload de FALHAS_CRITICAS_MANUAL.xlsx via API."""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from report_falhas.config_report import require_excel_path

DEFAULT_BASE = "http://127.0.0.1:8001"
DEFAULT_XLSX = Path(require_excel_path())


def req(opener, url, method="GET", data=None, headers=None, raw=None, timeout=30):
    h = dict(headers or {})
    body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
    if data is not None and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=h, method=method)
    with opener.open(request, timeout=timeout) as resp:
        return resp.status, resp.read()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX)
    parser.add_argument("--username", default="gerente.dev")
    parser.add_argument("--password", default="dev12345")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    xlsx: Path = args.xlsx
    if not xlsx.is_file():
        print(f"FAIL: arquivo não encontrado: {xlsx}")
        return 1

    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

    _, body = req(opener, f"{base}/api/v1/auth/csrf/")
    csrf = json.loads(body)["csrfToken"]
    status, body = req(
        opener,
        f"{base}/api/v1/auth/login/",
        method="POST",
        data={"username": args.username, "password": args.password},
        headers={"X-CSRFToken": csrf, "Referer": base},
    )
    if status != 200:
        print(f"FAIL login {status}: {body[:200]}")
        return 1
    print(f"OK login {args.username}")

    _, body = req(opener, f"{base}/api/v1/falhas/me/", headers={"Referer": base})
    me_before = json.loads(body)
    print(f"me before: total_failures={me_before.get('total_failures')}")

    boundary = "----FalhasUploadBoundary"
    content = xlsx.read_bytes()
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{xlsx.name}"\r\n'
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n"
        "\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    multipart = prefix + content + suffix

    _, body = req(opener, f"{base}/api/v1/auth/csrf/")
    csrf = json.loads(body)["csrfToken"]
    status, body = req(
        opener,
        f"{base}/api/v1/falhas/sync/upload/",
        method="POST",
        raw=multipart,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-CSRFToken": csrf,
            "Referer": base,
        },
        timeout=300,
    )
    upload = json.loads(body)
    print(f"upload HTTP {status}: {upload}")
    if status != 200 or upload.get("status") != "success":
        return 1

    _, body = req(opener, f"{base}/api/v1/falhas/sync/imports/", headers={"Referer": base})
    imports = json.loads(body)
    print(f"imports: {len(imports)} registro(s)")
    if imports:
        print(f"latest: success={imports[0].get('success')} filename={imports[0].get('filename')}")

    _, body = req(opener, f"{base}/api/v1/falhas/me/", headers={"Referer": base})
    me_after = json.loads(body)
    print(f"me after: total_failures={me_after.get('total_failures')}")

    _, body = req(
        opener,
        f"{base}/api/v1/falhas/kpis/?start_date=2026-06-01&end_date=2026-06-30&oficial=true",
        headers={"Referer": base},
    )
    kpis = json.loads(body)
    print(f"kpis: total_falhas={kpis.get('total_falhas')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
