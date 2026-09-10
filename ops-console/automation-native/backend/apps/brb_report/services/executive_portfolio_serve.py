# -*- coding: utf-8 -*-
"""Serve HTML do portfólio executivo com gate anti-sobrecarga."""
from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from apps.brb_report.services.client_catalog import resolve_client_config
from apps.brb_report.services.executive_portfolio import (
    _load_workbook_contestacao_sheet,
    _period_label,
    build_executive_portfolio,
)
from apps.common.heavy_request_gate import BuildTimeoutError, HeavyRequestGate, QueueTimeoutError
from report_brb.brb_loaders import load_bundle_hybrid
from report_brb.brb_metrics import compute_metrics
from report_brb.brb_render_executive import render_executive_portfolio_html
from report_brb.brb_render_rca import render_rca_pdf

_gate = HeavyRequestGate(
    label="brb_executive_portfolio",
    max_concurrent_setting="BRB_EXECUTIVE_PORTFOLIO_MAX_CONCURRENT",
    queue_wait_ms_setting="BRB_EXECUTIVE_PORTFOLIO_QUEUE_WAIT_MS",
    build_wait_s_setting="BRB_EXECUTIVE_PORTFOLIO_BUILD_WAIT_S",
    default_max_concurrent=1,
    default_queue_wait_ms=120_000,
    default_build_wait_s=900,
)


def _portfolio_cache_key(
    *,
    inicio: date,
    fim: date,
    top_n: int,
    client_slugs: list[str] | None,
    workbook_fingerprint: str | None,
    mode: str = "portfolio",
    client_slug: str | None = None,
) -> str:
    fp = workbook_fingerprint or "eo"
    slug_part = client_slug or ",".join(sorted(client_slugs or []))
    return f"{mode}:{inicio.isoformat()}:{fim.isoformat()}:{top_n}:{slug_part}:{fp}"


def serve_executive_portfolio_html(
    *,
    inicio: date,
    fim: date,
    top_n: int = 10,
    client_slugs: list[str] | None = None,
    workbook_path: Path | None = None,
    workbook_fingerprint: str | None = None,
    standalone_client_slug: str | None = None,
    export_api_base: str | None = None,
    supplement_key: str | None = None,
) -> tuple[str | None, str | None]:
    key = _portfolio_cache_key(
        inicio=inicio,
        fim=fim,
        top_n=top_n,
        client_slugs=client_slugs,
        workbook_fingerprint=workbook_fingerprint,
        mode="client" if standalone_client_slug else "portfolio",
        client_slug=standalone_client_slug,
    )

    def builder() -> str:
        target_slugs = [standalone_client_slug] if standalone_client_slug else client_slugs
        payload: dict[str, Any] = build_executive_portfolio(
            inicio=inicio,
            fim=fim,
            top_n=top_n,
            client_slugs=target_slugs,
            workbook_path=workbook_path,
        )
        return render_executive_portfolio_html(
            payload,
            standalone_client_slug=standalone_client_slug,
            export_api_base=export_api_base,
            supplement_key=supplement_key,
        )

    try:
        return _gate.run(key, builder), None
    except QueueTimeoutError:
        return None, "queue_timeout"
    except BuildTimeoutError:
        return None, "build_timeout"


def serve_executive_client_rca_pdf(
    *,
    client_slug: str,
    inicio: date,
    fim: date,
    workbook_path: Path | None = None,
    workbook_fingerprint: str | None = None,
) -> tuple[bytes | None, str | None]:
    slug = (client_slug or "").strip().lower()
    if not slug:
        return None, "invalid_client"
    key = _portfolio_cache_key(
        inicio=inicio,
        fim=fim,
        top_n=1,
        client_slugs=[slug],
        workbook_fingerprint=workbook_fingerprint,
        mode="rca",
        client_slug=slug,
    )

    def builder() -> bytes:
        excel_contestacao = _load_workbook_contestacao_sheet(workbook_path)
        bundle = load_bundle_hybrid(
            workbook_path,
            inicio=inicio,
            fim=fim,
            client_slug=slug,
            use_eo_db=True,
            excel_contestacao=excel_contestacao,
        )
        metrics = compute_metrics(bundle)
        client = resolve_client_config(slug)
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            render_rca_pdf(
                bundle,
                metrics,
                tmp_path,
                client_name=client.get("nome_curto", slug),
                periodo_label=_period_label(inicio, fim),
            )
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)

    try:
        return _gate.run(key, builder), None
    except QueueTimeoutError:
        return None, "queue_timeout"
    except BuildTimeoutError:
        return None, "build_timeout"


def executive_portfolio_busy_response(error: str) -> tuple[dict[str, Any], int, dict[str, str]]:
    if error == "queue_timeout":
        return (
            {
                "detail": "Portfólio executivo ocupado. Aguarde a geração em andamento e tente de novo.",
                "retry_after": 5,
            },
            503,
            {"Retry-After": "5"},
        )
    return (
        {
            "detail": "Tempo esgotado ao consolidar o portfólio executivo. Reduza o período ou tente novamente.",
            "retry_after": 10,
        },
        503,
        {"Retry-After": "10"},
    )
