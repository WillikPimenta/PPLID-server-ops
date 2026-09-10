# -*- coding: utf-8 -*-
"""Pipeline de geração Quality Pulse / relatórios cliente."""
from __future__ import annotations

import re
import shutil
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from django.conf import settings

import report_brb.config_brb as config_brb
from report_brb.brb_analytics import compute_analytics, enrich_falhas_gerais, enrich_na_falhas
from report_brb.brb_enrich_excel import enrich_workbook
from report_brb.brb_loaders import (
    load_bundle_hybrid,
    load_workbook,
    preview_workbook,
    validate_portfolio_contestacao_workbook,
    validate_workbook,
)
from report_brb.brb_metrics import compute_metrics
from report_brb.brb_render_excel import render_excel
from report_brb.brb_render_excel_cliente import render_excel_cliente
from report_brb.brb_render_html import render_html
from report_brb.brb_render_pulse import render_pulse_html
from report_brb.brb_render_rca import render_rca_pdf
from apps.brb_report.services.client_catalog import (
    is_report_client_enabled,
    list_report_clients,
    list_scheduled_report_client_slugs,
    preview_client_eo,
    report_uses_eo_db,
    resolve_client_config,
)
from report_brb.client_registry import is_client_enabled
from report_brb.supplement_stub import write_empty_supplement_workbook

SOURCE_FILENAME = "source.xlsx"
_WINDOWS_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _artifact_label(client_slug: str) -> str:
    """Identificador seguro para nomes de arquivo (Windows). Usa slug, não nome_curto."""
    label = re.sub(r"\s+", "-", (client_slug or "cliente").strip().lower())
    label = _WINDOWS_INVALID_CHARS.sub("-", label)
    label = re.sub(r"-+", "-", label).strip("-")
    return label[:80] or "cliente"


def _storage_root() -> Path:
    root = Path(getattr(settings, "BRB_REPORT_STORAGE", settings.MEDIA_ROOT)) / "brb_report"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _period_label(inicio: date | None, fim: date | None) -> str:
    if inicio and fim:
        return f"{inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
    if inicio:
        return f"a partir de {inicio.strftime('%d/%m/%Y')}"
    if fim:
        return f"até {fim.strftime('%d/%m/%Y')}"
    return "Todos os registros (sem filtro de período)"


def _closed_month_end(fim: date | None) -> date | None:
    """Retorna o fim do último mês fechado no recorte solicitado."""
    if fim is None:
        return None
    next_month = date(fim.year + (fim.month == 12), 1 if fim.month == 12 else fim.month + 1, 1)
    month_end = next_month - timedelta(days=1)
    if fim == month_end:
        return fim
    return date(fim.year, fim.month, 1) - timedelta(days=1)


def _apply_client_context(client_slug: str) -> dict[str, Any]:
    client = resolve_client_config(client_slug)
    config_brb.CLIENT = client
    return client


def _client_enabled(client_slug: str) -> bool:
    if report_uses_eo_db():
        return is_report_client_enabled(client_slug)
    return is_client_enabled(client_slug)


def list_available_clients() -> list[dict[str, Any]]:
    return list_report_clients(enabled_only=False)


def report_runtime_config() -> dict[str, Any]:
    return {
        "eo_db_mode": report_uses_eo_db(),
        "supplement_sheets": ["NA_Demandas", "NA_Falhas", "Treinamentos", "TreinamentosComHoras"],
        "legacy_sheets": 7,
    }


def preview_uploaded_workbook(path: Path, *, client_slug: str = "brb", inicio=None, fim=None) -> dict[str, Any]:
    if report_uses_eo_db():
        base = preview_client_eo(client_slug, inicio=inicio, fim=fim, supplement_path=path)
        try:
            xl_preview = preview_workbook(path)
            base["supplement"]["sheets"] = xl_preview.get("sheets") or base["supplement"].get("sheets", {})
            base["supplement"]["filename"] = xl_preview.get("filename")
            base["supplement"]["valid"] = True
            has_na = any(
                int(base["supplement"]["sheets"].get(s, 0)) > 0 for s in ("NA_Demandas", "NA_Falhas")
            )
            base["completeness"] = (
                "complete"
                if base["eo"]["auditados"] and base["eo"]["falhas_gerais"] and has_na
                else "partial"
            )
        except (ValueError, FileNotFoundError) as exc:
            base["supplement"]["error"] = str(exc)
            base["supplement"]["valid"] = False
        return base
    return preview_workbook(path)


def preview_client_from_db(
    client_slug: str,
    *,
    inicio: date | None = None,
    fim: date | None = None,
) -> dict[str, Any]:
    return preview_client_eo(client_slug, inicio=inicio, fim=fim, supplement_path=None)


def _use_eo_db() -> bool:
    return report_uses_eo_db()


def _resolve_workbook_path(workbook_path: Path | None) -> Path:
    """Garante path utilizável: upload, stub suplemento ou erro no modo legado."""
    if workbook_path and Path(workbook_path).is_file():
        return Path(workbook_path)
    if _use_eo_db():
        stub_dir = _storage_root() / "_stubs"
        stub_dir.mkdir(parents=True, exist_ok=True)
        stub = stub_dir / f"supplement_{uuid.uuid4().hex}.xlsx"
        return write_empty_supplement_workbook(stub)
    raise ValueError("Envie a planilha .xlsx com as 7 abas obrigatórias.")


def _load_bundle(
    workbook_path: Path | None,
    *,
    inicio: date | None,
    fim: date | None,
    client_slug: str,
):
    path = _resolve_workbook_path(workbook_path)
    if _use_eo_db():
        if workbook_path and Path(workbook_path).is_file():
            validate_workbook(path, mode="supplement")
        return load_bundle_hybrid(
            path,
            inicio=inicio,
            fim=fim,
            client_slug=client_slug,
            use_eo_db=True,
        )
    validate_workbook(path, mode="full")
    return load_workbook(path, inicio=inicio, fim=fim, client_slug=client_slug)


def _import_supplement_if_uploaded(path: Path | None, *, user=None) -> dict[str, Any] | None:
    """Persiste NA/Treinamentos no DB quando há upload suplemento."""
    if not path or not Path(path).is_file() or not _use_eo_db():
        return None
    from apps.brb_report.services.supplement_import import import_supplement_workbook

    return import_supplement_workbook(path, user=user, source_filename=Path(path).name)


def store_cs_source_workbook(workbook_path: Path | None = None, *, user=None) -> str:
    """Armazena base Visão CS — suplemento Excel ou stub vazio em modo EO."""
    path = _resolve_workbook_path(workbook_path)
    if workbook_path and Path(workbook_path).is_file():
        mode = "supplement" if _use_eo_db() else "full"
        validate_workbook(path, mode=mode)
        _import_supplement_if_uploaded(path, user=user)
    storage_key = uuid.uuid4().hex
    out_dir = _storage_root() / storage_key
    out_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(path, out_dir / SOURCE_FILENAME)
    return storage_key


def store_portfolio_supplement_workbook(workbook_path: Path) -> str:
    """Armazena planilha de contestação para complementar o portfólio executivo."""
    path = Path(workbook_path)
    validate_portfolio_contestacao_workbook(path)
    storage_key = uuid.uuid4().hex
    out_dir = _storage_root() / storage_key
    out_dir.mkdir(parents=True, exist_ok=False)
    shutil.copy2(path, out_dir / SOURCE_FILENAME)
    return storage_key


def generate_reports(
    *,
    workbook_path: Path | None = None,
    client_slug: str = "brb",
    inicio: date | None = None,
    fim: date | None = None,
    enrich: bool = True,
    include_pulse: bool = True,
    include_excel_cliente: bool = True,
    include_excel_interno: bool = False,
    include_dashboard: bool = False,
) -> dict[str, Any]:
    client_slug = (client_slug or "brb").strip().lower()
    if not _client_enabled(client_slug):
        raise ValueError(f"Cliente não habilitado: {client_slug}")

    client = _apply_client_context(client_slug)
    uploaded = Path(workbook_path) if workbook_path and Path(workbook_path).is_file() else None
    import_stats = None
    if uploaded and _use_eo_db():
        validate_workbook(uploaded, mode="supplement")
        import_stats = _import_supplement_if_uploaded(uploaded, user=None)
    elif uploaded and not _use_eo_db():
        validate_workbook(uploaded, mode="full")

    working_path = uploaded
    if enrich and uploaded and not _use_eo_db():
        working_path = uploaded.parent / f"_enriched_{uploaded.name}"
        shutil.copy2(uploaded, working_path)
        try:
            enrich_workbook(working_path, backup=False)
        except PermissionError:
            working_path = uploaded

    bundle = _load_bundle(working_path, inicio=inicio, fim=fim, client_slug=client_slug)
    bundle.falhas_gerais = enrich_falhas_gerais(bundle.falhas_gerais)
    bundle.na_falhas = enrich_na_falhas(bundle.na_falhas)
    metrics = compute_metrics(bundle)
    analytics = compute_analytics(bundle)

    bundle_full = None
    metrics_full = None
    if inicio is not None or fim is not None:
        bundle_full = _load_bundle(working_path, inicio=None, fim=None, client_slug=client_slug)
        bundle_full.falhas_gerais = enrich_falhas_gerais(bundle_full.falhas_gerais)
        bundle_full.na_falhas = enrich_na_falhas(bundle_full.na_falhas)
        metrics_full = compute_metrics(bundle_full)

    # O Quality Pulse gerado deve refletir o recorte solicitado. A base completa
    # fica restrita aos comparativos explicitamente históricos; usá-la nos KPIs
    # inflava, por exemplo, horas-pessoa com agentes fora do período selecionado.
    pulse_fim = _closed_month_end(fim) if include_pulse else fim
    pulse_bundle = bundle
    pulse_metrics = metrics
    pulse_analytics = analytics
    pulse_contestacao_historica = (
        bundle_full.contestacao if bundle_full is not None else bundle.contestacao
    )
    if include_pulse and pulse_fim != fim:
        # Para apresentação, o Pulse usa somente meses completos; os demais
        # artefatos continuam respeitando o período solicitado originalmente.
        pulse_bundle = _load_bundle(
            working_path, inicio=inicio, fim=pulse_fim, client_slug=client_slug
        )
        pulse_bundle.falhas_gerais = enrich_falhas_gerais(pulse_bundle.falhas_gerais)
        pulse_bundle.na_falhas = enrich_na_falhas(pulse_bundle.na_falhas)
        pulse_metrics = compute_metrics(pulse_bundle)
        pulse_analytics = compute_analytics(pulse_bundle)
        pulse_history = _load_bundle(
            working_path, inicio=None, fim=pulse_fim, client_slug=client_slug
        )
        pulse_contestacao_historica = pulse_history.contestacao

    periodo = _period_label(inicio, fim)
    storage_key = uuid.uuid4().hex
    out_dir = _storage_root() / storage_key
    out_dir.mkdir(parents=True, exist_ok=True)

    source_dest = out_dir / SOURCE_FILENAME
    stored_source = _resolve_workbook_path(working_path)
    if stored_source.resolve() != source_dest.resolve():
        shutil.copy2(stored_source, source_dest)

    slug = _artifact_label(client_slug)
    artifacts: dict[str, str] = {}

    xlsx_cliente_name = f"export_{slug}_cliente.xlsx"
    xlsx_cliente_path = out_dir / xlsx_cliente_name

    if include_excel_cliente:
        render_excel_cliente(bundle, metrics, xlsx_cliente_path, periodo_label=periodo)
        artifacts["excel_cliente"] = xlsx_cliente_name

    rca_name = f"rca_{slug}.pdf"
    render_rca_pdf(
        pulse_bundle,
        pulse_metrics,
        out_dir / rca_name,
        client_name=client.get("nome_curto", client_slug),
        periodo_label=_period_label(inicio, pulse_fim),
    )
    artifacts["rca"] = rca_name

    if include_pulse:
        pulse_name = f"quality_overview_{slug}.html"
        pulse_path = out_dir / pulse_name
        render_pulse_html(
            pulse_bundle,
            pulse_metrics,
            _period_label(inicio, pulse_fim),
            pulse_path,
            analytics=pulse_analytics,
            data_fim=pulse_fim,
            data_inicio=inicio,
            excel_export_href=xlsx_cliente_name if include_excel_cliente else None,
            rca_export_href=rca_name,
            contestacao_historica=pulse_contestacao_historica,
        )
        artifacts["quality_pulse"] = pulse_name

    if include_excel_interno:
        xlsx_int_name = f"relatorio_{slug}_executivo.xlsx"
        xlsx_int_path = out_dir / xlsx_int_name
        render_excel(
            bundle,
            metrics,
            xlsx_int_path,
            metrics_full=metrics_full,
            periodo_label=periodo,
            analytics=analytics,
        )
        artifacts["excel_interno"] = xlsx_int_name

    if include_dashboard:
        dash_name = f"relatorio_{slug}_dashboard.html"
        dash_path = out_dir / dash_name
        render_html(
            bundle,
            metrics,
            periodo,
            dash_path,
            metrics_full=metrics_full,
            analytics=analytics,
        )
        artifacts["dashboard_html"] = dash_name

    if enrich and working_path and uploaded and working_path != uploaded and working_path.exists():
        working_path.unlink(missing_ok=True)

    preview = preview_client_eo(
        client_slug,
        inicio=inicio,
        fim=fim,
        supplement_path=stored_source if stored_source.is_file() else None,
    )

    return {
        "storage_key": storage_key,
        "client_slug": client_slug,
        "client_nome": client.get("nome_curto", client_slug),
        "periodo": periodo,
        "period_start": inicio.isoformat() if inicio else None,
        "period_end": fim.isoformat() if fim else None,
        "artifacts": artifacts,
        "completeness": preview.get("completeness"),
        "warnings": preview.get("warnings") or [],
        "source_mode": preview.get("source_mode"),
        "supplement_import": import_stats,
        "kpis": {
            "demandas_na": metrics.demandas_na_registros,
            "na_falhas": metrics.na_falhas_registros,
            "casos_fg": metrics.casos_unicos_fg,
            "contestacao_procedente": metrics.conforme_nao,
            "contestacao_improcedente": metrics.conforme_sim,
            "auditados_casos": metrics.auditados_casos,
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def export_artifacts_to_dir(
    result: dict[str, Any],
    out_dir: Path,
    *,
    artifact_keys: Iterable[str] | None = None,
) -> dict[str, str]:
    """Copia artefatos gerados para pasta local (ex.: Downloads/report)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    storage_key = result.get("storage_key") or ""
    exported: dict[str, str] = {}
    keys = artifact_keys
    for key, filename in (result.get("artifacts") or {}).items():
        if keys is not None and key not in keys:
            continue
        src = resolve_artifact_path(storage_key, filename)
        dest = out_dir / filename
        shutil.copy2(src, dest)
        exported[key] = str(dest)
    return exported


def generate_reports_batch(
    *,
    client_slugs: list[str] | None = None,
    workbook_path: Path | None = None,
    inicio: date | None = None,
    fim: date | None = None,
    enrich: bool = False,
    include_pulse: bool = True,
    include_excel_cliente: bool = True,
    include_excel_interno: bool = False,
    include_dashboard: bool = False,
    output_dir: Path | str | None = None,
    html_only: bool = False,
) -> list[dict[str, Any]]:
    """Gera relatórios para vários clientes; opcionalmente exporta para pasta local."""
    targets = client_slugs or list_scheduled_report_client_slugs(enabled_only=True)
    if not targets:
        targets = ["brb"]

    out_path = Path(output_dir) if output_dir else None
    html_keys = ("quality_pulse", "dashboard_html") if html_only else None
    results: list[dict[str, Any]] = []

    for slug in targets:
        row: dict[str, Any] = {"client_slug": slug}
        try:
            result = generate_reports(
                workbook_path=workbook_path,
                client_slug=slug,
                inicio=inicio,
                fim=fim,
                enrich=enrich,
                include_pulse=include_pulse,
                include_excel_cliente=include_excel_cliente and not html_only,
                include_excel_interno=include_excel_interno and not html_only,
                include_dashboard=include_dashboard,
            )
            row.update({"ok": True, **result})
            if out_path is not None:
                row["exported"] = export_artifacts_to_dir(
                    result,
                    out_path,
                    artifact_keys=html_keys,
                )
        except Exception as exc:  # noqa: BLE001
            row.update({"ok": False, "error": str(exc)})
        results.append(row)
    return results


def resolve_source_workbook(storage_key: str) -> Path:
    if not storage_key or ".." in storage_key or "/" in storage_key or "\\" in storage_key:
        raise FileNotFoundError("Storage key inválido")
    path = _storage_root() / storage_key / SOURCE_FILENAME
    if not path.is_file():
        raise FileNotFoundError(
            f"Planilha fonte não encontrada para {storage_key}. Gere o relatório novamente com upload."
        )
    return path


def regenerate_reports(
    *,
    source_storage_key: str,
    client_slug: str = "brb",
    inicio: date | None = None,
    fim: date | None = None,
    enrich: bool = True,
    include_pulse: bool = True,
    include_excel_cliente: bool = True,
    include_excel_interno: bool = False,
    include_dashboard: bool = False,
) -> dict[str, Any]:
    """Regera artefatos reutilizando source.xlsx de uma geração anterior."""
    workbook_path = resolve_source_workbook(source_storage_key)
    return generate_reports(
        workbook_path=workbook_path,
        client_slug=client_slug,
        inicio=inicio,
        fim=fim,
        enrich=enrich,
        include_pulse=include_pulse,
        include_excel_cliente=include_excel_cliente,
        include_excel_interno=include_excel_interno,
        include_dashboard=include_dashboard,
    )


def resolve_artifact_path(storage_key: str, filename: str) -> Path:
    if not storage_key or not filename:
        raise FileNotFoundError("Artefato inválido")
    if ".." in storage_key or ".." in filename or "/" in filename or "\\" in filename:
        raise FileNotFoundError("Artefato inválido")
    path = _storage_root() / storage_key / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


def artifact_content_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".html"):
        return "text/html; charset=utf-8"
    if lower.endswith(".xlsx"):
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return "application/octet-stream"
