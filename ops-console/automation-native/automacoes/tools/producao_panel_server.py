#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Servidor local para o painel BRFlow: tratar CSVs em Downloads e limpar brutos."""

from __future__ import annotations

import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.bots.bot_production import METAS_EXCEL_PATH

_TOOLS = Path(__file__).resolve().parent
_KIT = _TOOLS / "producao-brflow-kit"
if str(_KIT) not in sys.path:
    sys.path.insert(0, str(_KIT))

from tratar_producao import default_downloads, processar_lote_producao  # noqa: E402

HOST = "127.0.0.1"
PORT = 8765
DEFAULT_DOWNLOADS = default_downloads()


class Handler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def log_message(self, fmt: str, *args) -> None:
        print(f"[producao_panel] {self.address_string()} — {fmt % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/status":
            self._json(
                200,
                {
                    "ok": True,
                    "metas": METAS_EXCEL_PATH.exists(),
                    "downloads": str(DEFAULT_DOWNLOADS),
                    "output": str(DEFAULT_DOWNLOADS),
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/tratar":
            self._json(404, {"error": "not found"})
            return

        body = self._read_body()
        since = body.get("since")
        if since is not None:
            try:
                since = float(since)
            except (TypeError, ValueError):
                since = None
        limpar = body.get("limpar", True)

        try:
            resultado = processar_lote_producao(
                pasta_origem=DEFAULT_DOWNLOADS,
                pasta_saida=DEFAULT_DOWNLOADS,
                since=since,
                limpar_csv=bool(limpar),
            )
        except FileNotFoundError as exc:
            self._json(400, {"error": str(exc)})
            return

        if not resultado["arquivos"]:
            self._json(500, {"error": "Nenhum arquivo gerado", "detalhes": resultado["erros"]})
            return

        self._json(200, resultado)


def main() -> None:
    if not METAS_EXCEL_PATH.exists():
        print(f"Aviso: {METAS_EXCEL_PATH} nao encontrado — Meta/Percentual ficarao vazios.")

    server = HTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}/status"
    print(f"Servidor em http://{HOST}:{PORT}")
    print(f"Pasta:   {DEFAULT_DOWNLOADS} (entrada e saida)")
    print("No BRFlow, execute brflow_baixar_3dias.js e depois tratar_producao.py")
    print("Ctrl+C para encerrar\n")

    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrado.")
        server.server_close()


if __name__ == "__main__":
    main()
