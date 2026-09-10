# -*- coding: utf-8 -*-
"""Indicadores nativos para a Visão CS do Quality Pulse."""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd
from django.utils import timezone

from apps.brb_report.models import BrbCsDataSource, BrbReportGeneration
from apps.brb_report.services.report_service import report_runtime_config, resolve_source_workbook
from apps.brb_report.services.client_catalog import resolve_client_config
from report_brb.brb_filters import (
    add_case_columns,
    dedupe_by_case_key,
    match_client,
    na_effective_date,
    parse_excel_date,
    safe_str,
)
from report_brb.brb_loaders import _prep_contestacao, load_bundle_hybrid, load_workbook
from report_brb.brb_normalize import strip_accents
from report_brb.brb_render_cliente import _find_fg_col, _normalize_tipo_falha_fg
from report_brb.brb_finding_guidance import (
    FINDING_TYPE_GUIDANCE,
    cs_validation_items,
    is_sensitive_scenario,
)
from report_brb.client_registry import get_client_config, is_client_enabled


RESULT_LABELS = {
    "falha": "Falha confirmada",
    "nao_falha": "Sem falha confirmada",
    "indefinido": "Sem decisão",
}
SNAPSHOT_FILENAME = "cs_snapshot.json"


def _latest_source(client_slug: str, storage_key: str = "") -> tuple[Any, Path]:
    sources = BrbCsDataSource.objects.filter(client_slug=client_slug)
    generations = BrbReportGeneration.objects.filter(client_slug=client_slug)
    if storage_key:
        sources = sources.filter(storage_key=storage_key)
        generations = generations.filter(storage_key=storage_key)
    candidates = [*sources.order_by("-created_at")[:30], *generations.order_by("-created_at")[:30]]
    candidates.sort(key=lambda item: item.created_at, reverse=True)
    for source in candidates:
        try:
            return source, resolve_source_workbook(source.storage_key)
        except FileNotFoundError:
            continue
    raise FileNotFoundError(
        "Nenhuma base processada foi encontrada para este cliente. "
        "Faça uma carga na área Gerar relatório para habilitar a Visão CS."
    )


def _iso_date(value) -> str | None:
    return value.date().isoformat() if pd.notna(value) else None


def _daily_bucket(store: dict[str, dict[str, float]], value) -> dict[str, float] | None:
    if pd.isna(value):
        return None
    key = value.date().isoformat()
    return store.setdefault(
        key,
        {
            "requests": 0,
            "protocols_informed": 0,
            "notified_failures": 0,
            "findings": 0,
            "audited_protocols": 0,
            "training_hours": 0.0,
            "training_sessions": 0,
        },
    )


def _use_eo_db() -> bool:
    return bool(report_runtime_config().get("eo_db_mode"))


def _load_report_bundle(source_path: Path, client_slug: str):
    if _use_eo_db():
        return load_bundle_hybrid(source_path, inicio=None, fim=None, client_slug=client_slug, use_eo_db=True)
    return load_workbook(source_path, inicio=None, fim=None, client_slug=client_slug)


def _client_cfg(client_slug: str) -> dict:
    try:
        return get_client_config(client_slug)
    except KeyError:
        return resolve_client_config(client_slug)


def _bundle_to_snapshot_payload(
    bundle,
    client_slug: str,
    *,
    source_path: Path | None = None,
) -> dict[str, Any]:
    """Monta payload leve (contestação + cross-source + achados) a partir do bundle."""
    daily: dict[str, dict[str, float]] = {}

    demands = bundle.na_demandas
    if not demands.empty and "Data da Abertura" in demands.columns:
        for _, row in demands.iterrows():
            bucket = _daily_bucket(daily, row.get("Data da Abertura"))
            if bucket is None:
                continue
            bucket["requests"] += 1
            protocols = pd.to_numeric(row.get("Quantidade de Protolocos"), errors="coerce")
            bucket["protocols_informed"] += float(protocols) if pd.notna(protocols) else 0.0

    notified = bundle.na_falhas
    if not notified.empty:
        effective_dates = na_effective_date(notified)
        for value in effective_dates:
            bucket = _daily_bucket(daily, value)
            if bucket is not None:
                bucket["notified_failures"] += 1

    findings = bundle.falhas_gerais
    if not findings.empty and "Data de Análise" in findings.columns:
        for value in findings["Data de Análise"]:
            bucket = _daily_bucket(daily, value)
            if bucket is not None:
                bucket["findings"] += 1

    audited = bundle.auditados
    if not audited.empty and "Data" in audited.columns:
        for _, group in audited.dropna(subset=["Data"]).groupby(audited["Data"].dt.date):
            bucket = _daily_bucket(daily, group["Data"].iloc[0])
            if bucket is not None:
                protocol_col = "_protocolo_norm" if "_protocolo_norm" in group.columns else "Protocolo"
                bucket["audited_protocols"] += int(group[protocol_col].replace("", pd.NA).dropna().nunique())

    training = bundle.treinamentos_horas
    if not training.empty:
        date_col = next(
            (column for column in ("_periodo_ref", "StartDate", "FinalDate") if column in training.columns),
            None,
        )
        if date_col:
            for _, row in training.iterrows():
                bucket = _daily_bucket(daily, row.get(date_col))
                if bucket is not None:
                    bucket["training_sessions"] += 1
                    hours = pd.to_numeric(row.get("horas"), errors="coerce")
                    bucket["training_hours"] += float(hours) if pd.notna(hours) else 0.0

    contestations = []
    for _, row in bundle.contestacao.iterrows():
        contestations.append(
            {
                "Protocolo": safe_str(row.get("Protocolo")),
                "Data": _iso_date(row.get("Data")),
                "Data de Análise": _iso_date(row.get("Data de Análise")),
                "classificacao_conforme": safe_str(row.get("classificacao_conforme")),
                "Tipo de falha": safe_str(row.get("Tipo de falha")),
                "Cenário": safe_str(row.get("Cenário")),
                "Etapa": safe_str(row.get("Etapa")),
                "Workflow": safe_str(row.get("Workflow")),
            }
        )

    findings_detail = []
    client_config = _client_cfg(client_slug)
    if not bundle.falhas_gerais.empty:
        fg = bundle.falhas_gerais.copy()
        type_col = _find_fg_col(fg, "tipo", "falha")
        scenario_col = next(
            (column for column in ("Novo cenário", "Cenário") if column in fg.columns),
            None,
        )
        etapa_col = next(
            (column for column in ("Etapa", "Etapa da Análise", "Etapa Análise") if column in fg.columns),
            None,
        )
        for _, row in fg.iterrows():
            findings_detail.append(
                {
                    "Protocolo": safe_str(row.get("Protocolo")),
                    "Data": _iso_date(row.get("Data de Análise")),
                    "chave_caso": safe_str(row.get("chave_caso")),
                    "Tipo de falha": safe_str(row.get(type_col)) if type_col else "",
                    "Cenário": safe_str(row.get(scenario_col)) if scenario_col else "",
                    "Etapa": safe_str(row.get(etapa_col)) if etapa_col else "",
                }
            )
    elif not _use_eo_db() and source_path is not None:
        findings_sheet = client_config.get("fg_sheet", "Falhas_Gerais-BRB")
        raw_findings = pd.read_excel(source_path, findings_sheet)
        if not raw_findings.empty and "Cliente" in raw_findings.columns:
            raw_findings = raw_findings[
                raw_findings["Cliente"].map(lambda value: match_client(value, client_slug))
            ].copy()
        if not raw_findings.empty:
            raw_findings = add_case_columns(raw_findings, "Protocolo", "Matrícula Agente")
            if "Data de Análise" in raw_findings.columns:
                raw_findings["Data de Análise"] = parse_excel_date(raw_findings["Data de Análise"])
            type_col = _find_fg_col(raw_findings, "tipo", "falha")
            scenario_col = next(
                (column for column in ("Novo cenário", "Cenário") if column in raw_findings.columns),
                None,
            )
            for _, row in raw_findings.iterrows():
                findings_detail.append(
                    {
                        "Protocolo": safe_str(row.get("Protocolo")),
                        "Data": _iso_date(row.get("Data de Análise")),
                        "chave_caso": safe_str(row.get("chave_caso")),
                        "Tipo de falha": safe_str(row.get(type_col)) if type_col else "",
                        "Cenário": safe_str(row.get(scenario_col)) if scenario_col else "",
                    }
                )

    return {
        "version": 2,
        "client_slug": client_slug,
        "daily": daily,
        "contestations": contestations,
        "findings_detail": findings_detail,
    }


def prepare_cs_source_snapshot(storage_key: str, client_slug: str) -> dict[str, Any]:
    """Processa a base uma vez e grava snapshot leve para consultas rápidas."""
    source_path = resolve_source_workbook(storage_key)
    bundle = _load_report_bundle(source_path, client_slug)
    payload = _bundle_to_snapshot_payload(bundle, client_slug, source_path=source_path)
    snapshot_path = source_path.parent / SNAPSHOT_FILENAME
    snapshot_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {
        "storage_key": storage_key,
        "client_slug": client_slug,
        "contestations": len(payload.get("contestations") or []),
        "days": len(payload.get("daily") or {}),
    }


def _payload_to_dataframes(
    payload: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, dict[str, float]], pd.DataFrame]:
    df = pd.DataFrame(payload.get("contestations") or [])
    for column in ("Data", "Data de Análise"):
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], errors="coerce")
    findings_df = pd.DataFrame(payload.get("findings_detail") or [])
    if "Data" in findings_df.columns:
        findings_df["Data"] = pd.to_datetime(findings_df["Data"], errors="coerce")
    return df, payload.get("daily") or {}, findings_df


def _load_snapshot(
    source_path: Path,
) -> tuple[pd.DataFrame | None, dict[str, dict[str, float]], pd.DataFrame]:
    snapshot_path = source_path.parent / SNAPSHOT_FILENAME
    if not snapshot_path.is_file():
        return None, {}, pd.DataFrame()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    df, daily, findings_df = _payload_to_dataframes(payload)
    return df, daily, findings_df


def _load_live_eo_data(client_slug: str) -> tuple[pd.DataFrame, dict[str, dict[str, float]], pd.DataFrame]:
    bundle = load_bundle_hybrid(None, inicio=None, fim=None, client_slug=client_slug, use_eo_db=True)
    payload = _bundle_to_snapshot_payload(bundle, client_slug)
    return _payload_to_dataframes(payload)


def _live_source_meta(client_slug: str) -> dict[str, Any]:
    client = _client_cfg(client_slug)
    return {
        "storage_key": "",
        "client_slug": client_slug,
        "client_name": client.get("nome_curto", client_slug),
        "updated_at": timezone.now().isoformat(),
        "independent_update": False,
        "original_filename": "Qualidade Operacional (ao vivo)",
        "source_mode": "eo_live",
        "generation_period_start": None,
        "generation_period_end": None,
    }


def _resolve_cs_data(
    client_slug: str,
    storage_key: str = "",
) -> tuple[pd.DataFrame, dict[str, dict[str, float]], pd.DataFrame, dict[str, Any]]:
    if _use_eo_db() and not storage_key:
        full_df, cross_daily, findings_df = _load_live_eo_data(client_slug)
        return full_df, cross_daily, findings_df, _live_source_meta(client_slug)

    generation, source_path = _latest_source(client_slug, storage_key)
    full_df, cross_daily, findings_df = _load_snapshot(source_path)
    if full_df is not None and (not cross_daily or findings_df.empty):
        prepare_cs_source_snapshot(generation.storage_key, client_slug)
        full_df, cross_daily, findings_df = _load_snapshot(source_path)
    if full_df is None:
        if _use_eo_db():
            bundle = _load_report_bundle(source_path, client_slug)
            payload = _bundle_to_snapshot_payload(bundle, client_slug, source_path=source_path)
            full_df, cross_daily, findings_df = _payload_to_dataframes(payload)
        else:
            raw = pd.read_excel(source_path, "Contestacao")
            full_df = _prep_contestacao(raw, None, None, client_slug)
            cross_daily = {}
            findings_df = pd.DataFrame()

    client = _client_cfg(client_slug)
    source_meta = {
        "storage_key": generation.storage_key,
        "client_slug": client_slug,
        "client_name": client.get("nome_curto", client_slug),
        "updated_at": generation.created_at.isoformat(),
        "independent_update": isinstance(generation, BrbCsDataSource),
        "original_filename": getattr(generation, "original_filename", ""),
        "source_mode": "snapshot",
        "generation_period_start": (
            generation.period_start.isoformat()
            if getattr(generation, "period_start", None)
            else None
        ),
        "generation_period_end": (
            generation.period_end.isoformat()
            if getattr(generation, "period_end", None)
            else None
        ),
    }
    return full_df, cross_daily, findings_df, source_meta


def _finding_period(df: pd.DataFrame, date_from: date | None, date_to: date | None) -> pd.DataFrame:
    if df.empty or "Data" not in df.columns:
        return df.copy()
    mask = df["Data"].notna()
    if date_from:
        mask &= df["Data"] >= pd.Timestamp(date_from)
    if date_to:
        mask &= df["Data"] < pd.Timestamp(date_to + timedelta(days=1))
    return df.loc[mask].copy()


def _finding_insights(
    df: pd.DataFrame,
    date_from: date | None,
    date_to: date | None,
) -> dict[str, Any]:
    work = _finding_period(df, date_from, date_to)
    if work.empty or "Tipo de falha" not in work.columns:
        return {"available": False, "total": 0, "unique_protocols": 0, "types": [], "focus": []}
    if "chave_caso" in work.columns:
        work = dedupe_by_case_key(work, "Data")
    work["_type"] = work["Tipo de falha"].map(_normalize_tipo_falha_fg)
    total = len(work)
    protocols = int(
        work.get("Protocolo", pd.Series(dtype=str))
        .fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
    )
    order = ["Automático", "Mapeamento", "Manual", "Processual", "Não informado"]
    guidance = FINDING_TYPE_GUIDANCE
    counts = work["_type"].value_counts()
    types = [
        {
            "label": label,
            "count": int(counts[label]),
            "pct": _pct(int(counts[label]), total),
            "opportunity": guidance.get(label, "Validar classificação e direcionamento."),
        }
        for label in order
        if label in counts.index
    ]
    focus = []
    for label in ("Automático", "Mapeamento", "Manual", "Processual"):
        subset = work[work["_type"].eq(label)]
        if subset.empty:
            continue
        scenarios = subset.get("Cenário", pd.Series(dtype=str)).fillna("").astype(str).str.strip()
        visible = scenarios[scenarios.ne("") & ~scenarios.map(is_sensitive_scenario)]
        scenario_counts = visible.value_counts().head(3)
        visible_rows = [
            {"label": str(scenario), "count": int(count), "pct": _pct(int(count), len(subset))}
            for scenario, count in scenario_counts.items()
        ]
        shown = sum(item["count"] for item in visible_rows)
        focus.append(
            {
                "type": label,
                "total": len(subset),
                "opportunity": guidance.get(label, "Validar classificação e direcionamento."),
                "scenarios": visible_rows,
                "other_count": max(0, len(subset) - shown),
                "cs_validation": cs_validation_items(label, subset, scenario_counts),
            }
        )
    return {
        "available": True,
        "total": total,
        "unique_protocols": protocols,
        "types": types,
        "focus": focus,
    }


def _values(df: pd.DataFrame, column: str, *, limit: int = 150) -> list[str]:
    if column not in df.columns or df.empty:
        return []
    values = {safe_str(value) for value in df[column].dropna().tolist()}
    return sorted((value for value in values if value), key=str.casefold)[:limit]


def _filter_period(df: pd.DataFrame, date_from: date | None, date_to: date | None) -> pd.DataFrame:
    if df.empty or "Data" not in df.columns:
        return df.copy()
    mask = df["Data"].notna()
    if date_from:
        mask &= df["Data"] >= pd.Timestamp(date_from)
    if date_to:
        mask &= df["Data"] < pd.Timestamp(date_to + timedelta(days=1))
    return df.loc[mask].copy()


def _apply_dimensions(
    df: pd.DataFrame,
    *,
    result: str = "",
    failure_type: str = "",
    stage: str = "",
    search: str = "",
    critical_only: bool = False,
) -> pd.DataFrame:
    out = df.copy()
    exact_filters = (
        ("classificacao_conforme", result),
        ("Tipo de falha", failure_type),
        ("Etapa", stage),
    )
    for column, value in exact_filters:
        if value and column in out.columns:
            out = out[out[column].fillna("").astype(str).eq(value)]

    if search and not out.empty:
        needle = search.casefold()
        columns = [
            column
            for column in ("Protocolo", "Cenário", "Tipo de falha", "Etapa", "Workflow")
            if column in out.columns
        ]
        if columns:
            mask = pd.Series(False, index=out.index)
            for column in columns:
                mask |= out[column].fillna("").astype(str).str.casefold().str.contains(
                    needle, regex=False
                )
            out = out[mask]

    out = _with_age(out)
    if critical_only and not out.empty:
        is_failure = out.get("classificacao_conforme", pd.Series("", index=out.index)).eq("falha")
        is_old = out["age_days"].fillna(0).ge(90)
        out = out[is_failure | is_old]
    return out


def _with_age(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "Data" in out.columns and "Data de Análise" in out.columns:
        age = (out["Data"] - out["Data de Análise"]).dt.days
        out["age_days"] = age.where(age.ge(0))
    else:
        out["age_days"] = pd.NA
    return out


def _pct(count: int, total: int) -> float:
    return round(100.0 * count / total, 1) if total else 0.0


def _summary(df: pd.DataFrame) -> dict[str, Any]:
    total = len(df)
    classification = df.get("classificacao_conforme", pd.Series(dtype=str))
    failures = int(classification.eq("falha").sum())
    no_failure = int(classification.eq("nao_falha").sum())
    undecided = max(0, total - failures - no_failure)
    age = pd.to_numeric(df.get("age_days", pd.Series(dtype=float)), errors="coerce").dropna()
    over_90 = int(age.ge(90).sum())
    protocols = 0
    if "Protocolo" in df.columns:
        protocols = int(df["Protocolo"].dropna().astype(str).str.strip().replace("", pd.NA).nunique())
    return {
        "total": total,
        "unique_protocols": protocols,
        "confirmed_failures": failures,
        "confirmed_rate": _pct(failures, failures + no_failure),
        "without_failure": no_failure,
        "undecided": undecided,
        "aged_over_90": over_90,
        "aged_over_90_pct": _pct(over_90, total),
        "median_age_days": int(round(float(age.median()))) if len(age) else None,
    }


def _buckets(df: pd.DataFrame, column: str, *, limit: int = 8) -> list[dict[str, Any]]:
    if df.empty or column not in df.columns:
        return []
    values = df[column].fillna("").astype(str).str.strip()
    values = values[values.ne("")]
    counts = values.value_counts().head(limit)
    total = int(counts.sum())
    return [
        {"label": str(label), "count": int(count), "pct": _pct(int(count), total)}
        for label, count in counts.items()
    ]


def _top_causes(df: pd.DataFrame) -> list[dict[str, Any]]:
    failures = df[df.get("classificacao_conforme", pd.Series("", index=df.index)).eq("falha")]
    candidates = [column for column in ("Tipo de falha", "Cenário") if column in failures.columns]
    if not candidates:
        return []
    # Algumas masters possuem a coluna Tipo de falha, mas quase toda vazia.
    # Nesse caso, Cenário representa melhor a concentração real do recorte.
    column = max(
        candidates,
        key=lambda candidate: int(
            failures[candidate].fillna("").astype(str).str.strip().ne("").sum()
        ),
    )
    return _buckets(failures, column, limit=6)


def _monthly(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty or "Data" not in df.columns:
        return []
    work = df.dropna(subset=["Data"]).copy()
    if work.empty:
        return []
    work["_month"] = work["Data"].dt.to_period("M")
    rows: list[dict[str, Any]] = []
    for month, group in work.groupby("_month", sort=True):
        summary = _summary(group)
        rows.append(
            {
                "month": str(month),
                "label": month.to_timestamp().strftime("%m/%Y"),
                "total": summary["total"],
                "confirmed_failures": summary["confirmed_failures"],
                "confirmed_rate": summary["confirmed_rate"],
            }
        )
    return rows[-18:]


def _cross_summary(
    daily: dict[str, dict[str, float]],
    date_from: date | None,
    date_to: date | None,
) -> dict[str, Any]:
    totals = {
        "requests": 0,
        "protocols_informed": 0,
        "notified_failures": 0,
        "findings": 0,
        "audited_protocols": 0,
        "training_hours": 0.0,
        "training_sessions": 0,
    }
    monthly: dict[str, dict[str, float]] = {}
    for raw_date, values in daily.items():
        current_date = date.fromisoformat(raw_date)
        if date_from and current_date < date_from:
            continue
        if date_to and current_date > date_to:
            continue
        month = raw_date[:7]
        month_values = monthly.setdefault(month, {key: 0 for key in totals})
        for key in totals:
            value = values.get(key, 0) or 0
            totals[key] += value
            month_values[key] += value
    totals["training_hours"] = round(float(totals["training_hours"]), 1)
    for values in monthly.values():
        values["training_hours"] = round(float(values["training_hours"]), 1)
    return {
        "available": bool(daily),
        "totals": totals,
        "monthly": [
            {"month": month, "label": f"{month[5:7]}/{month[:4]}", **values}
            for month, values in sorted(monthly.items())
        ][-18:],
    }


def _comparison(
    full_df: pd.DataFrame,
    current: dict[str, Any],
    *,
    date_from: date | None,
    date_to: date | None,
    dimension_filters: dict[str, Any],
) -> dict[str, Any] | None:
    if not date_from or not date_to:
        return None
    days = (date_to - date_from).days + 1
    previous_to = date_from - timedelta(days=1)
    previous_from = previous_to - timedelta(days=days - 1)
    previous_df = _filter_period(full_df, previous_from, previous_to)
    previous_df = _apply_dimensions(previous_df, **dimension_filters)
    previous = _summary(previous_df)
    current_rate = float(current["confirmed_rate"])
    previous_rate = float(previous["confirmed_rate"])
    total_delta_pct = (
        round(100.0 * (current["total"] - previous["total"]) / previous["total"], 1)
        if previous["total"]
        else None
    )
    return {
        "date_from": previous_from.isoformat(),
        "date_to": previous_to.isoformat(),
        "total": previous["total"],
        "confirmed_rate": previous_rate,
        "confirmed_failures": previous["confirmed_failures"],
        "delta_rate_pp": round(current_rate - previous_rate, 1),
        "delta_total_pct": total_delta_pct,
    }


def _health(
    summary: dict[str, Any],
    comparison: dict[str, Any] | None,
    top_causes: list[dict[str, Any]],
    goals: dict[str, float],
) -> dict[str, Any]:
    if not summary["total"]:
        return {
            "level": "neutral",
            "label": "Sem base no recorte",
            "score": None,
            "reason": "Ajuste os filtros para visualizar o sinal CS.",
            "goals": goals,
        }
    rate = float(summary["confirmed_rate"])
    old_pct = float(summary["aged_over_90_pct"])
    delta = float(comparison["delta_rate_pp"]) if comparison else 0.0
    concentration = float(top_causes[0]["pct"]) if top_causes else 0.0
    rate_target = float(goals.get("confirmed_rate", 5.0))
    old_target = float(goals.get("aged_over_90_pct", 10.0))
    concentration_target = float(goals.get("cause_concentration_pct", 35.0))
    score = 100
    score -= min(35, round(max(0.0, rate - rate_target) * 2.5))
    score -= min(20, round(max(0.0, old_pct - old_target) * 0.7))
    score -= min(15, round(max(0.0, delta) * 3.0))
    score -= min(15, round(max(0.0, concentration - concentration_target) * 0.4))
    score = max(0, int(score))
    if score >= 80:
        level, label = "healthy", "Sinal favorável"
    elif score >= 60:
        level, label = "attention", "Ponto de atenção"
    else:
        level, label = "critical", "Atenção prioritária"
    reasons = [f"{rate:.1f}% das decisões confirmaram falha"]
    if old_pct:
        reasons.append(f"{old_pct:.1f}% chegaram após mais de 90 dias")
    if comparison and delta:
        direction = "alta" if delta > 0 else "queda"
        reasons.append(f"{direction} de {abs(delta):.1f} p.p. contra o período anterior")
    return {
        "level": level,
        "label": label,
        "score": score,
        "reason": "; ".join(reasons) + ".",
        "goals": goals,
    }


def _actions(summary: dict[str, Any], comparison: dict[str, Any] | None, causes: list[dict[str, Any]]) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    if comparison and comparison["delta_rate_pp"] >= 1:
        actions.append({"severity": "high", "title": "Validar a alta com Qualidade", "detail": f"A taxa aumentou {comparison['delta_rate_pp']:.1f} p.p. frente ao período anterior."})
    if causes and causes[0]["pct"] >= 35:
        actions.append({"severity": "high", "title": f"Atuar sobre {causes[0]['label']}", "detail": f"O motivo concentra {causes[0]['pct']:.1f}% das falhas confirmadas no recorte."})
    if summary["aged_over_90"]:
        actions.append({"severity": "medium", "title": "Revisar contestações antigas", "detail": f"{summary['aged_over_90']} caso(s) chegaram mais de 90 dias após a análise."})
    if summary["undecided"]:
        actions.append({"severity": "medium", "title": "Completar decisões pendentes", "detail": f"{summary['undecided']} contestação(ões) estão sem classificação conclusiva."})
    if not actions:
        actions.append({"severity": "low", "title": "Sustentar o acompanhamento", "detail": "O recorte não apresenta alertas automáticos; mantenha a revisão periódica com Qualidade."})
    return actions[:4]


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    work = df.sort_values("Data", ascending=False, na_position="last").head(75)
    rows = []
    for _, row in work.iterrows():
        classification = safe_str(row.get("classificacao_conforme")) or "indefinido"
        received = row.get("Data")
        analysis = row.get("Data de Análise")
        age = row.get("age_days")
        rows.append(
            {
                "protocol": safe_str(row.get("Protocolo")) or "—",
                "received_at": received.date().isoformat() if pd.notna(received) else None,
                "analysis_at": analysis.date().isoformat() if pd.notna(analysis) else None,
                "result": classification,
                "result_label": RESULT_LABELS.get(classification, "Sem decisão"),
                "failure_type": safe_str(row.get("Tipo de falha")) or "—",
                "stage": safe_str(row.get("Etapa")) or "—",
                "age_days": int(age) if pd.notna(age) else None,
            }
        )
    return rows


def build_cs_dashboard(
    *,
    client_slug: str,
    storage_key: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    result: str = "",
    failure_type: str = "",
    stage: str = "",
    search: str = "",
    critical_only: bool = False,
) -> dict[str, Any]:
    client_slug = (client_slug or "brb").strip().lower()
    if not is_client_enabled(client_slug) and not _use_eo_db():
        raise ValueError(f"Cliente não habilitado: {client_slug}")
    if _use_eo_db():
        try:
            resolve_client_config(client_slug)
        except KeyError as exc:
            raise ValueError(str(exc)) from exc
    full_df, cross_daily, findings_df, source_meta = _resolve_cs_data(client_slug, storage_key)
    options = {
        "results": [{"value": key, "label": label} for key, label in RESULT_LABELS.items()],
        "failure_types": _values(full_df, "Tipo de falha"),
        "stages": _values(full_df, "Etapa"),
    }
    dimension_filters = {
        "result": result,
        "failure_type": failure_type,
        "stage": stage,
        "search": search,
        "critical_only": critical_only,
    }
    current_df = _filter_period(full_df, date_from, date_to)
    current_df = _apply_dimensions(current_df, **dimension_filters)
    summary = _summary(current_df)
    causes = _top_causes(current_df)
    comparison = _comparison(
        full_df,
        summary,
        date_from=date_from,
        date_to=date_to,
        dimension_filters=dimension_filters,
    )
    client = _client_cfg(client_slug)
    goals = dict(client.get("cs_goals") or {})
    return {
        "available": True,
        "source": source_meta,
        "applied_filters": {
            "date_from": date_from.isoformat() if date_from else None,
            "date_to": date_to.isoformat() if date_to else None,
            **dimension_filters,
        },
        "options": options,
        "summary": summary,
        "cross_source": _cross_summary(cross_daily, date_from, date_to),
        "findings": _finding_insights(findings_df, date_from, date_to),
        "comparison": comparison,
        "health": _health(summary, comparison, causes, goals),
        "top_causes": causes,
        "by_stage": _buckets(current_df, "Etapa", limit=6),
        "monthly": _monthly(current_df),
        "actions": _actions(summary, comparison, causes),
        "records": _records(current_df),
    }
