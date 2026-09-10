"""Testa upload de importação via proxy Vite (5174) e direto (8001)."""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from io import BytesIO

import pandas as pd


def build_xlsx() -> bytes:
    df = pd.DataFrame(
        [
            {
                "BLOCO": 1,
                "COLABORADOR": "Agente Teste",
                "MATRÍCULA": "agent001",
                "LIDERANCA": "0",
                "HORÁRIO": "08:00 - 14:00",
                "ATIVIDADE": "Teste",
                "UF": "Brasília",
                "EQUIPE": "CONFER",
                "01/06/2026": "",
            }
        ]
    )
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="__JUNHO26", index=False)
    return buf.getvalue()


def multipart_body(content: bytes, boundary: str = "----TestBoundary") -> bytes:
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="test.xlsx"\r\n'
        "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n"
        "\r\n"
    ).encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    return prefix + content + suffix


class Client:
    def __init__(self, base: str, origin: str):
        self.base = base.rstrip("/")
        self.origin = origin
        self.jar = CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _csrf(self) -> str:
        with self.opener.open(f"{self.base}/api/v1/auth/csrf/", timeout=30) as resp:
            data = json.loads(resp.read().decode())
        return data["csrfToken"]

    def login(self, username: str, password: str) -> None:
        csrf = self._csrf()
        payload = json.dumps({"username": username, "password": password}).encode()
        req = urllib.request.Request(
            f"{self.base}/api/v1/auth/login/",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-CSRFToken": csrf,
                "Origin": self.origin,
                "Referer": f"{self.origin}/",
            },
            method="POST",
        )
        with self.opener.open(req, timeout=30):
            pass

    def import_file(self) -> tuple[int, str]:
        csrf = self._csrf()
        body = multipart_body(build_xlsx())
        req = urllib.request.Request(
            f"{self.base}/api/v1/escala-flex/planejamento/import/",
            data=body,
            headers={
                "Content-Type": "multipart/form-data; boundary=----TestBoundary",
                "X-CSRFToken": csrf,
                "Origin": self.origin,
                "Referer": f"{self.origin}/planejamento/escalas",
            },
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=120) as resp:
                return resp.status, resp.read(500).decode(errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(500).decode(errors="replace")


def main() -> int:
    username = sys.argv[1] if len(sys.argv) > 1 else "gerente.dev"
    password = sys.argv[2] if len(sys.argv) > 2 else "dev12345"

    for label, base, origin in [
        ("direct-8001", "http://127.0.0.1:8001", "http://127.0.0.1:5174"),
        ("proxy-5174", "http://127.0.0.1:5174", "http://127.0.0.1:5174"),
    ]:
        client = Client(base, origin)
        try:
            client.login(username, password)
            status, body = client.import_file()
            print(f"{label}: HTTP {status}")
            print(body[:300])
        except Exception as exc:
            print(f"{label}: ERROR {exc}")
        print("---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
