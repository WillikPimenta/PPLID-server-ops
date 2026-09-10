# -*- coding: utf-8 -*-
"""Smoke das rotas Vue (requer frontend DEV rodando)."""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from typing import Any

SUITE = "spa"

PORTAL_ROUTES = (
    "/home",
    "/noticias",
    "/dashboard",
    "/agents",
    "/secao/indicadores/falhas",
    "/secao/indicadores/falhas/diagnostico",
    "/secao/indicadores/falhas/reincidencia",
    "/secao/indicadores/falhas/comparativo",
    "/secao/indicadores/falhas/suporte",
    "/secao/indicadores/falhas/treinamentos",
    "/secao/indicadores/falhas/base-auditoria",
    "/secao/indicadores/falhas/chamados",
    "/secao/indicadores/produtividade",
    "/planejamento/escalas",
    "/planejamento/automacao",
    "/planejamento/portal-ops",
    "/planejamento/psa-cyber",
    "/planejamento/monitoramento/painel",
    "/operacao/controle-de-jornada/painel",
)


def _frontend_base() -> str:
    return os.environ.get("PPLID_FRONTEND_URL", "http://localhost:5174").rstrip("/")


def run_full() -> list[dict[str, Any]]:
    base = _frontend_base()
    results: list[dict[str, Any]] = []
    for route in PORTAL_ROUTES:
        url = f"{base}{route}"
        try:
            req = urllib.request.Request(url, headers={"Accept": "text/html"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read()
                has_app = b'id="app"' in html or b"id='app'" in html
                results.append(
                    {
                        "ok": resp.status == 200 and has_app,
                        "label": f"spa:{route}",
                        "status": resp.status,
                        "path": route,
                        "extra": " app" if has_app else " NO_APP",
                        "duration_ms": 0,
                        "suite": SUITE,
                    }
                )
        except urllib.error.HTTPError as exc:
            results.append(
                {
                    "ok": False,
                    "label": f"spa:{route}",
                    "status": exc.code,
                    "path": route,
                    "extra": str(exc.reason),
                    "duration_ms": 0,
                    "suite": SUITE,
                }
            )
        except Exception as exc:
            results.append(
                {
                    "ok": False,
                    "label": f"spa:{route}",
                    "status": 0,
                    "path": route,
                    "extra": str(exc),
                    "duration_ms": 0,
                    "suite": SUITE,
                }
            )
    return results
