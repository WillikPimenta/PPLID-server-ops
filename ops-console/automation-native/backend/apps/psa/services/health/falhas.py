# -*- coding: utf-8 -*-
"""Suite de health checks — Falhas Críticas."""
from __future__ import annotations

from typing import Any

from django.test import Client

from apps.psa.services.health.base import check_path, parse_json_response

SUITE = "falhas"

DEFAULT_FILTER_QS = "start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Geral"
DEFAULT_FILTER_QS_MONTH = "start_date=2026-06-01&end_date=2026-06-30&oficial=true"

QUICK_CHECKS = (
    ("/api/v1/falhas/me/", "me"),
    ("/api/v1/falhas/filters/", "filters"),
    (f"/api/v1/falhas/dashboard-summary/?{DEFAULT_FILTER_QS}", "dashboard-summary"),
    ("/api/v1/falhas/base-overview/?localidade=Geral", "base-overview"),
)

MODULE_ENDPOINTS: dict[str, list[str]] = {
    "dashboard": [f"/api/v1/falhas/dashboard-summary/?{{f}}"],
    "diagnostico": [f"/api/v1/falhas/diagnostico/?{{f}}"],
    "reincidencia": [f"/api/v1/falhas/reincidence-summary/?{{f}}"],
    "comparativo": [f"/api/v1/falhas/comparativo-bsb-sc/?{{f}}"],
    "suporte": [f"/api/v1/falhas/support/?{{f}}"],
    "treinamentos": [f"/api/v1/falhas/treinamentos/?{{f}}"],
    "base-auditoria": [
        "/api/v1/falhas/base-overview/?localidade=Geral",
        f"/api/v1/falhas/contestacoes/?{{f}}",
        "/api/v1/falhas/executive/history/",
    ],
    "chamados": ["/api/v1/falhas/tickets/"],
}

LOCAL_ENDPOINTS = (
    ("/api/v1/falhas/me/", "me"),
    ("/api/v1/falhas/filters/", "filters"),
    (f"/api/v1/falhas/kpis/?{DEFAULT_FILTER_QS_MONTH}", "kpis"),
    (f"/api/v1/falhas/dashboard-summary/?{DEFAULT_FILTER_QS_MONTH}", "dashboard-summary"),
    (f"/api/v1/falhas/diagnostico/?{DEFAULT_FILTER_QS_MONTH}", "diagnostico"),
    (f"/api/v1/falhas/reincidence-summary/?{DEFAULT_FILTER_QS_MONTH}", "reincidence-summary"),
    (f"/api/v1/falhas/alerts/?{DEFAULT_FILTER_QS_MONTH}", "alerts"),
    ("/api/v1/falhas/base-overview/", "base-overview"),
    (f"/api/v1/falhas/matrix/?{DEFAULT_FILTER_QS_MONTH}", "matrix"),
    (f"/api/v1/falhas/rank-delta/?{DEFAULT_FILTER_QS_MONTH}", "rank-delta"),
    (f"/api/v1/falhas/consolidado/?{DEFAULT_FILTER_QS_MONTH}", "consolidado"),
    (f"/api/v1/falhas/clientes-workflows/?{DEFAULT_FILTER_QS_MONTH}", "clientes-workflows"),
    ("/api/v1/falhas/executive/history/", "executive-history"),
    (f"/api/v1/falhas/support/?{DEFAULT_FILTER_QS_MONTH}", "support"),
    (f"/api/v1/falhas/treinamentos/?{DEFAULT_FILTER_QS_MONTH}", "treinamentos"),
    (f"/api/v1/falhas/reincidence/?{DEFAULT_FILTER_QS_MONTH}", "reincidence"),
    (f"/api/v1/falhas/comparativo-bsb-sc/?{DEFAULT_FILTER_QS_MONTH}", "comparativo"),
    ("/falhas/", "portal-redirect"),
)

FILTER_VARIANTS = (
    DEFAULT_FILTER_QS,
    "start_date=2026-06-01&end_date=2026-06-25&oficial=false&localidade=Geral",
    "start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=Bras%C3%ADlia",
    "start_date=2026-06-01&end_date=2026-06-25&oficial=true&localidade=S%C3%A3o%20Carlos",
)

LEGACY_PHASE4 = (
    f"/api/v1/falhas/kpis/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/executive/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/charts/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/rank-delta/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/matrix/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/reincidence/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/reincidence-by-turno/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/ult3m/?{DEFAULT_FILTER_QS}",
    f"/api/v1/falhas/support/?{DEFAULT_FILTER_QS}&bridge_only=true",
    f"/api/v1/falhas/treinamentos/?{DEFAULT_FILTER_QS}&bridge_only=true",
)


def evaluate_response(path: str, status_code: int, content: bytes, content_type: str) -> tuple[bool, str]:
    ok = status_code == 200
    if path == "/falhas/" and status_code == 302:
        return True, "redirect OK"

    data = parse_json_response(content, content_type)
    extra = ""

    if not ok:
        return False, f"status={status_code}"

    if data is None:
        if path == "/falhas/":
            return True, "portal OK" if b"portal-tpl" in content else "SEM TEMPLATE"
        return True, ""

    if path.startswith("/api/v1/falhas/base-overview"):
        required = ("db_totals", "scoped_totals", "scope_label", "is_global_scope", "import_note")
        missing = [k for k in required if k not in data]
        if missing:
            return False, f"missing={missing}"
        if "localidade=Geral" in path or path.endswith("/base-overview/"):
            if data.get("scope_label") != "Geral" or data.get("scoped_totals") is not None:
                return False, "expected Geral + scoped_totals=null"
            if not data.get("is_global_scope"):
                return False, "expected is_global_scope=true"
        elif "Bras" in path:
            if data.get("scoped_totals") is None:
                return False, "expected scoped_totals object (BSB)"
        elif "S%C3%A3o" in path or "Sao" in path:
            if data.get("scoped_totals") is None:
                return False, "expected scoped_totals object (SC)"
        sync_stats = (data.get("last_sync") or {}).get("stats")
        extra = (
            f"failures={data['db_totals'].get('failures')}"
            f" scope={data.get('scope_label')}"
            f" sync_stats={'yes' if sync_stats else 'no'}"
        )
    elif "scope" in data and path.endswith("/me/"):
        extra = (
            f"scope={data.get('scope')} can_sync={data.get('can_sync')}"
            f" last_sync={bool(data.get('last_sync'))}"
        )
    elif "total_falhas" in data:
        extra = f"total_falhas={data.get('total_falhas')}"
    elif "kpis" in data and "executive" in data:
        extra = (
            f"kpis={data['kpis'].get('total_falhas')}"
            f" alerts={len(data.get('alerts') or [])}"
        )
    elif "rank_delta" in data and "matrix" in data:
        extra = f"diagnostico OK ms={data.get('meta', {}).get('duration_ms', '?')}"
    elif "by_turno" in data and "list" in data:
        extra = f"reincidence-summary OK ms={data.get('meta', {}).get('duration_ms', '?')}"
    elif "localidades" in data:
        extra = f"locs={len(data.get('localidades') or [])}"
    elif "items" in data and "executive/history" in path:
        extra = f"items={len(data.get('items') or [])}"
    elif "brasilia" in data:
        extra = "comparativo OK"
    elif "results" in data and "reincidence" in path:
        extra = f"reinc={data.get('count', len(data.get('results', [])))}"
    elif "bridge_falhas" in data:
        extra = f"bridge_r3={data['bridge_falhas']['summary'].get('agentes_com_regra3_e_falha')}"
    elif "bridge_reincidencia" in data:
        extra = (
            f"bridge_cap="
            f"{data['bridge_reincidencia']['summary'].get('agentes_criticos_com_pendencia')}"
        )

    return True, extra


def _check(client: Client, path: str, label: str) -> dict[str, Any]:
    return check_path(client, path, label, suite=SUITE, evaluator=evaluate_response)


def run_quick(client: Client) -> list[dict[str, Any]]:
    return [_check(client, path, label) for path, label in QUICK_CHECKS]


def run_full(client: Client) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    for path, label in LOCAL_ENDPOINTS:
        results.append(_check(client, path, label))

    for module, templates in MODULE_ENDPOINTS.items():
        for tpl in templates:
            path = tpl.format(f=DEFAULT_FILTER_QS) if "{f}" in tpl else tpl
            results.append(_check(client, path, f"module:{module}"))

    for path in LEGACY_PHASE4:
        results.append(_check(client, path, "legacy-phase4"))

    for idx, filt in enumerate(FILTER_VARIANTS):
        results.append(_check(client, f"/api/v1/falhas/kpis/?{filt}", f"filters-variant-{idx}"))

    results.append(
        _check(
            client,
            f"/api/v1/falhas/reincidence/?{DEFAULT_FILTER_QS}&filtro=reincidentes",
            "reincidence-oficiais",
        )
    )
    results.append(
        _check(
            client,
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
        item = _check(client, path, label)
        results.append(item)
        if item["ok"] and loc == "Geral":
            response = client.get(path, HTTP_HOST="localhost")
            data = parse_json_response(response.content, response.get("Content-Type", ""))
            if isinstance(data, dict) and data.get("scoped_totals") is not None:
                results.append(
                    {
                        "ok": False,
                        "label": f"{label}-scoped",
                        "status": 200,
                        "path": path,
                        "extra": "expected scoped_totals=null",
                        "duration_ms": 0,
                        "suite": SUITE,
                    }
                )

    hist = client.get("/api/v1/falhas/executive/history/", HTTP_HOST="localhost")
    hist_data = parse_json_response(hist.content, hist.get("Content-Type", ""))
    items = (hist_data or {}).get("items") if isinstance(hist_data, dict) else []
    if items:
        archive_id = items[0]["id"]
        dl = client.get(
            f"/api/v1/falhas/executive/history/{archive_id}/download/",
            HTTP_HOST="localhost",
        )
        results.append(
            {
                "ok": dl.status_code == 200,
                "label": "executive-download",
                "status": dl.status_code,
                "path": f"/executive/history/{archive_id}/download/",
                "extra": "",
                "duration_ms": 0,
                "suite": SUITE,
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
                "suite": SUITE,
            }
        )

    return results
