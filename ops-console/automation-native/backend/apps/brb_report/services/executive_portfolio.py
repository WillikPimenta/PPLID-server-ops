# -*- coding: utf-8 -*-
"""Agrega KPIs multi-cliente para visão executiva (portfólio)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from apps.brb_report.services.client_catalog import (
    list_report_clients,
    list_scheduled_report_client_slugs,
    resolve_client_config,
)
from report_brb.brb_analytics import _mes_label
from report_brb.brb_contestacao_temporal import contestacao_temporal
from report_brb.brb_format import format_int_br
from report_brb.brb_loaders import BRBDataBundle, load_bundle_hybrid
from report_brb.brb_metrics import compute_metrics

_MONTH_ABBR = ("", "JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")


def _fmt_n(value: int) -> str:
    return format_int_br(int(value))


def _period_label(inicio: date | None, fim: date | None) -> str:
    if inicio and fim:
        return f"{inicio.strftime('%d/%m/%Y')} a {fim.strftime('%d/%m/%Y')}"
    if inicio:
        return f"a partir de {inicio.strftime('%d/%m/%Y')}"
    if fim:
        return f"até {fim.strftime('%d/%m/%Y')}"
    return "Período completo"


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _month_abbr(ym: str) -> str:
    try:
        return _MONTH_ABBR[int(ym.split("-")[1])]
    except (IndexError, ValueError):
        return ym


def _month_range(inicio: date | None, fim: date | None) -> list[str]:
    if not inicio or not fim:
        return []
    months: list[str] = []
    year, month = inicio.year, inicio.month
    end_key = _month_key(fim.year, fim.month)
    while True:
        key = _month_key(year, month)
        months.append(key)
        if key == end_key:
            break
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def _monthly_from_contestacao(bundle: BRBDataBundle) -> tuple[dict[str, int], dict[str, int]]:
    cont = bundle.contestacao
    if cont.empty or "Data" not in cont.columns:
        return {}, {}
    work = cont.copy()
    work["mes_ref"] = _mes_label(work["Data"])
    work = work[work["mes_ref"].notna()]
    if work.empty:
        return {}, {}
    contest = work.groupby("mes_ref").size().astype(int).to_dict()
    falhas = work[work["classificacao_conforme"] == "falha"]
    confirmadas = (
        falhas.groupby("mes_ref").size().astype(int).to_dict() if not falhas.empty else {}
    )
    return confirmadas, contest


def _monthly_from_achados(bundle: BRBDataBundle) -> dict[str, int]:
    fg = bundle.falhas_gerais
    if fg.empty or "Data de Análise" not in fg.columns:
        return {}
    work = fg.copy()
    work["mes_ref"] = _mes_label(work["Data de Análise"])
    work = work[work["mes_ref"].notna()]
    if work.empty:
        return {}
    if "chave_caso" in work.columns:
        return work.groupby("mes_ref")["chave_caso"].nunique().astype(int).to_dict()
    return work.groupby("mes_ref").size().astype(int).to_dict()


def _monthly_from_training(bundle: BRBDataBundle) -> tuple[dict[str, float], dict[str, int]]:
    """Retorna horas e sessões por mês para a leitura do plano de ação."""
    training = getattr(bundle, "treinamentos_horas", None)
    if training is None or training.empty:
        training = getattr(bundle, "treinamentos", None)
        if training is None or training.empty:
            return {}, {}
    date_col = "_periodo_ref" if "_periodo_ref" in training.columns else next(
        (c for c in ("StartDate", "Session: StartDate", "FinalDate", "AssignmentDate", "RequestDate") if c in training.columns), None
    )
    if not date_col:
        return {}, {}
    work = training.copy()
    work["mes_ref"] = _mes_label(work[date_col])
    work = work[work["mes_ref"].notna()]
    if work.empty:
        return {}, {}
    hours = work.groupby("mes_ref")["horas"].sum().round(1).to_dict() if "horas" in work.columns else {}
    sessions = work.groupby("mes_ref").size().astype(int).to_dict()
    return {str(k): float(v) for k, v in hours.items()}, {str(k): int(v) for k, v in sessions.items()}


def _workflow_label(value: object) -> str:
    text = str(value or "").strip()
    return text or "Sem workflow"


def _workflow_key(value: object) -> str:
    from report_brb.brb_normalize import strip_accents

    text = strip_accents(_workflow_label(value)).casefold()
    return text or "__sem_workflow__"


def _monthly_contestacao_frame(frame: pd.DataFrame) -> tuple[dict[str, int], dict[str, int]]:
    if frame.empty or "Data" not in frame.columns:
        return {}, {}
    work = frame.copy()
    work["mes_ref"] = _mes_label(work["Data"])
    work = work[work["mes_ref"].notna()]
    if work.empty:
        return {}, {}
    contest = work.groupby("mes_ref").size().astype(int).to_dict()
    falhas = work[work["classificacao_conforme"] == "falha"]
    confirmadas = (
        falhas.groupby("mes_ref").size().astype(int).to_dict() if not falhas.empty else {}
    )
    return confirmadas, contest


def _monthly_achados_frame(frame: pd.DataFrame) -> dict[str, int]:
    if frame.empty or "Data de Análise" not in frame.columns:
        return {}
    work = frame.copy()
    work["mes_ref"] = _mes_label(work["Data de Análise"])
    work = work[work["mes_ref"].notna()]
    if work.empty:
        return {}
    if "chave_caso" in work.columns:
        return work.groupby("mes_ref")["chave_caso"].nunique().astype(int).to_dict()
    return work.groupby("mes_ref").size().astype(int).to_dict()


def _contestacao_with_workflow(
    contestacao: pd.DataFrame,
    auditados: pd.DataFrame,
    falhas_gerais: pd.DataFrame,
) -> pd.DataFrame:
    from report_brb.brb_filters import norm_protocolo

    if contestacao.empty:
        return contestacao.copy()
    proto_wf: dict[str, str] = {}
    for frame in (auditados, falhas_gerais):
        if frame is None or frame.empty or "Workflow" not in frame.columns:
            continue
        proto_col = "Protocolo" if "Protocolo" in frame.columns else None
        if not proto_col:
            continue
        for _, row in frame.iterrows():
            proto = norm_protocolo(row.get(proto_col))
            wf = _workflow_label(row.get("Workflow"))
            if proto and wf != "Sem workflow":
                proto_wf[proto] = wf
    work = contestacao.copy()
    if "_protocolo_norm" not in work.columns and "Protocolo" in work.columns:
        work["_protocolo_norm"] = work["Protocolo"].map(norm_protocolo)
    work["_workflow"] = work.get("_protocolo_norm", pd.Series(dtype=str)).map(
        lambda proto: proto_wf.get(str(proto or "").strip(), "")
    )
    return work


def _workflow_id_map(names: set[str]) -> dict[str, int | None]:
    from apps.dimensoes_processos.models import DimWorkflow

    clean = {name for name in names if name and name != "Sem workflow"}
    if not clean:
        return {}
    rows = DimWorkflow.objects.filter(nome__in=clean).values_list("nome", "id_workflow")
    return {str(nome): int(wf_id) for nome, wf_id in rows}


def _client_workflow_catalog(id_cliente: int | None) -> dict[int, str]:
    """Workflows do cliente no catálogo EO/dimensões (inclui sem carga no recorte)."""
    if not id_cliente:
        return {}
    from apps.dimensoes_processos.models import DimWorkflow, ProjecaoSla
    from apps.qualidade_operacional.models import QualidadeAuditado, QualidadeFalha

    wf_ids: set[int] = set()
    for model in (QualidadeAuditado, QualidadeFalha):
        wf_ids.update(
            int(wid)
            for wid in model.objects.filter(id_cliente=id_cliente)
            .exclude(id_workflow__isnull=True)
            .values_list("id_workflow", flat=True)
            .distinct()
        )
    wf_ids.update(
        int(wid)
        for wid in ProjecaoSla.objects.filter(cliente_id=id_cliente)
        .values_list("workflow_id", flat=True)
        .distinct()
    )
    if not wf_ids:
        return {}
    rows = DimWorkflow.objects.filter(id_workflow__in=wf_ids).values_list("id_workflow", "nome")
    return {int(wid): str(nome) for wid, nome in rows if nome}


def _empty_workflow_row(*, nome: str, id_workflow: int | None = None) -> dict[str, Any]:
    return {
        "key": _workflow_key(nome),
        "nome": nome,
        "id_workflow": id_workflow,
        "auditados": 0,
        "achados_fg": 0,
        "contestacoes": 0,
        "falhas_confirmadas": 0,
        "sem_falha": 0,
        "contestacoes_decididas": 0,
        "taxa_confirmada_pct": 0.0,
        "monthly_confirmadas": {},
        "monthly_contestacoes": {},
        "monthly_achados": {},
        "sem_carga_periodo": True,
        "diagnostics": {
            "contestacao": _empty_source_diagnostics(),
            "auditoria": _empty_source_diagnostics(),
        },
        "contestacao_timing": {},
    }


def build_client_workflows(
    bundle: BRBDataBundle,
    *,
    fim: date | None = None,
    id_cliente: int | None = None,
) -> list[dict[str, Any]]:
    """Agrega KPIs e séries mensais por workflow para filtro no HTML."""
    auditados = bundle.auditados if bundle.auditados is not None else pd.DataFrame()
    falhas = bundle.falhas_gerais if bundle.falhas_gerais is not None else pd.DataFrame()
    contestacao = _contestacao_with_workflow(bundle.contestacao, auditados, falhas)

    buckets: dict[str, dict[str, Any]] = {}

    def bucket_for(name: object) -> dict[str, Any]:
        label = _workflow_label(name)
        key = _workflow_key(name)
        if key not in buckets:
            buckets[key] = {
                "key": key,
                "nome": label,
                "id_workflow": None,
                "auditados": 0,
                "achados_fg": 0,
                "contestacoes": 0,
                "falhas_confirmadas": 0,
                "sem_falha": 0,
                "contestacoes_decididas": 0,
                "taxa_confirmada_pct": 0.0,
                "monthly_confirmadas": {},
                "monthly_contestacoes": {},
                "monthly_achados": {},
            }
        return buckets[key]

    if not auditados.empty and "Workflow" in auditados.columns:
        from report_brb.brb_filters import norm_protocolo

        for wf_name, grp in auditados.groupby("Workflow", dropna=False):
            row = bucket_for(wf_name)
            protos = grp.get("Protocolo", pd.Series(dtype=str)).map(norm_protocolo)
            row["auditados"] = int(protos[protos.astype(str).str.strip().ne("")].nunique())

    if not falhas.empty and "Workflow" in falhas.columns:
        for wf_name, grp in falhas.groupby("Workflow", dropna=False):
            row = bucket_for(wf_name)
            row["achados_fg"] = int(len(grp))
            row["monthly_achados"] = _monthly_achados_frame(grp)

    if not contestacao.empty and "_workflow" in contestacao.columns:
        for wf_name, grp in contestacao.groupby("_workflow", dropna=False):
            label = _workflow_label(wf_name)
            if label == "Sem workflow":
                continue
            row = bucket_for(wf_name)
            confirmadas = int((grp["classificacao_conforme"] == "falha").sum())
            sem_falha = int((grp["classificacao_conforme"] == "ok").sum())
            decididas = confirmadas + sem_falha
            row["contestacoes"] = int(len(grp))
            row["falhas_confirmadas"] = confirmadas
            row["sem_falha"] = sem_falha
            row["contestacoes_decididas"] = decididas
            row["taxa_confirmada_pct"] = (
                round(100.0 * confirmadas / decididas, 1) if decididas else 0.0
            )
            monthly_confirmadas, monthly_contest = _monthly_contestacao_frame(grp)
            if monthly_contest:
                row["monthly_contestacoes"] = monthly_contest
            if monthly_confirmadas:
                row["monthly_confirmadas"] = monthly_confirmadas

    id_map = _workflow_id_map({row["nome"] for row in buckets.values()})
    catalog = _client_workflow_catalog(id_cliente)
    catalog_ids = set(catalog.keys())
    existing_ids = {
        int(row["id_workflow"])
        for row in buckets.values()
        if row.get("id_workflow") is not None
    }
    for wf_id, nome in catalog.items():
        if wf_id in existing_ids:
            continue
        key = _workflow_key(nome)
        if key in buckets:
            if buckets[key].get("id_workflow") is None:
                buckets[key]["id_workflow"] = wf_id
            continue
        buckets[key] = _empty_workflow_row(nome=nome, id_workflow=wf_id)

    rows = []
    for row in buckets.values():
        if row.get("id_workflow") is None:
            row["id_workflow"] = id_map.get(row["nome"])
        has_volume = any(
            (
                row["auditados"],
                row["achados_fg"],
                row["contestacoes"],
                row["falhas_confirmadas"],
            )
        )
        wf_id = row.get("id_workflow")
        in_catalog = wf_id is not None and int(wf_id) in catalog_ids
        if not has_volume and not in_catalog:
            continue
        if not has_volume:
            row["sem_carga_periodo"] = True
        rows.append(row)
    rows.sort(
        key=lambda item: (
            int(item.get("falhas_confirmadas") or 0),
            int(item.get("achados_fg") or 0),
            int(item.get("contestacoes") or 0),
            str(item.get("nome") or "").casefold(),
        ),
        reverse=True,
    )
    for row in rows:
        wf_label = str(row.get("nome") or "")
        cont_sub = (
            contestacao[contestacao["_workflow"].eq(wf_label)]
            if not contestacao.empty and "_workflow" in contestacao.columns
            else pd.DataFrame()
        )
        fg_sub = (
            falhas[falhas["Workflow"].map(_workflow_label).eq(wf_label)]
            if not falhas.empty and "Workflow" in falhas.columns
            else pd.DataFrame()
        )
        row["diagnostics"] = {
            "contestacao": _build_contestacao_diagnostics([cont_sub] if not cont_sub.empty else []),
            "auditoria": _build_auditoria_diagnostics([fg_sub] if not fg_sub.empty else []),
        }
        row["contestacao_timing"] = build_portfolio_contestacao_timing(
            [cont_sub] if not cont_sub.empty else [],
            fim=fim,
        )
    return rows


def build_client_diagnostics(bundle: BRBDataBundle) -> dict[str, Any]:
    """Diagnóstico consolidado do cliente (todos os workflows)."""
    auditados = bundle.auditados if bundle.auditados is not None else pd.DataFrame()
    falhas = bundle.falhas_gerais if bundle.falhas_gerais is not None else pd.DataFrame()
    contestacao = _contestacao_with_workflow(bundle.contestacao, auditados, falhas)
    return {
        "contestacao": _build_contestacao_diagnostics([contestacao] if not contestacao.empty else []),
        "auditoria": _build_auditoria_diagnostics([falhas] if not falhas.empty else []),
    }


def _merge_monthly(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    merged: dict[str, int] = {}
    for row in rows:
        if not row.get("ok"):
            continue
        for ym, value in (row.get(field) or {}).items():
            merged[ym] = merged.get(ym, 0) + int(value)
    return dict(sorted(merged.items()))


def build_portfolio_monthly(
    rows: list[dict[str, Any]],
    *,
    inicio: date | None = None,
    fim: date | None = None,
) -> dict[str, Any]:
    ok_rows = [r for r in rows if r.get("ok")]
    confirmadas = _merge_monthly(ok_rows, "monthly_confirmadas")
    contestacoes = _merge_monthly(ok_rows, "monthly_contestacoes")
    achados = _merge_monthly(ok_rows, "monthly_achados")
    months = _month_range(inicio, fim) or sorted(set(confirmadas) | set(contestacoes) | set(achados))
    partial_month = None
    if fim and months and months[-1] == _month_key(fim.year, fim.month):
        next_month = date(
            fim.year + (1 if fim.month == 12 else 0),
            1 if fim.month == 12 else fim.month + 1,
            1,
        )
        if fim < next_month - timedelta(days=1):
            partial_month = months[-1]
    return {
        "months": months,
        "confirmadas": confirmadas,
        "contestacoes": contestacoes,
        "achados": achados,
        "has_data": bool(confirmadas or contestacoes or achados),
        "partial_month": partial_month,
    }


def build_timeline_events(
    monthly: dict[str, Any],
    top10: list[dict[str, Any]],
    totals: dict[str, Any],
    *,
    inicio: date | None = None,
    fim: date | None = None,
) -> list[dict[str, str]]:
    leader = top10[0] if top10 else {}
    top_share = round(sum(float(r.get("share_confirmadas_pct") or 0) for r in top10), 1)
    confirmadas = monthly.get("confirmadas") or {}
    achados = monthly.get("achados") or {}
    achados_total = int(totals.get("achados_fg") or 0)
    events: list[dict[str, str]] = []

    if inicio:
        events.append(
            {
                "kind": "open",
                "when": inicio.strftime("%d/%m/%Y"),
                "title": "Início do recorte",
                "value": _fmt_n(int(totals.get("clientes_ok") or 0)),
                "detail": f"clientes no portfólio · {_fmt_n(achados_total)} achados na auditoria",
            }
        )

    if achados:
        peak_audit_ym = max(achados, key=lambda k: achados[k])
        events.append(
            {
                "kind": "audit",
                "when": _month_abbr(peak_audit_ym),
                "title": "Pico de achados na auditoria",
                "value": _fmt_n(int(achados[peak_audit_ym])),
                "detail": f"maior mês de achados identificados ({peak_audit_ym})",
            }
        )

    if confirmadas:
        peak_ym = max(confirmadas, key=lambda k: confirmadas[k])
        events.append(
            {
                "kind": "peak",
                "when": _month_abbr(peak_ym),
                "title": "Pico de confirmações",
                "value": _fmt_n(int(confirmadas[peak_ym])),
                "detail": "maior mês de falhas confirmadas em contestação",
            }
        )

    if leader:
        events.append(
            {
                "kind": "attention",
                "when": leader.get("nome_curto") or "—",
                "title": "Maior volume individual",
                "value": _fmt_n(int(leader.get("falhas_confirmadas") or 0)),
                "detail": "falhas confirmadas no período completo",
            }
        )

    events.append(
        {
            "kind": "risk",
            "when": "Top 10",
            "title": "Concentração do portfólio",
            "value": f"{top_share:.1f}%",
            "detail": "das falhas confirmadas do universo",
        }
    )

    if fim:
        partial = monthly.get("partial_month")
        detail = (
            f"recorte parcial até {fim.strftime('%d/%m/%Y')}"
            if partial
            else "fechamento do recorte analisado"
        )
        events.append(
            {
                "kind": "close",
                "when": fim.strftime("%d/%m/%Y"),
                "title": "Taxa confirmada ponderada",
                "value": f"{float(totals.get('taxa_confirmada_pct') or 0):.1f}%",
                "detail": detail,
            }
        )
    return events


def build_risk_table_rows(rows: list[dict[str, Any]], *, limit: int = 6) -> list[dict[str, Any]]:
    ok_rows = [r for r in rows if r.get("ok")]
    ranked = sorted(
        ok_rows,
        key=lambda r: (
            int(r.get("falhas_confirmadas") or 0),
            float(r.get("taxa_confirmada_pct") or 0),
        ),
        reverse=True,
    )[:limit]
    peak_confirmadas = max((int(r.get("falhas_confirmadas") or 0) for r in ranked), default=0) or 1
    out: list[dict[str, Any]] = []
    for row in ranked:
        confirmadas = int(row.get("falhas_confirmadas") or 0)
        decididas = int(row.get("contestacoes_decididas") or 0)
        taxa = float(row.get("taxa_confirmada_pct") or 0)
        if confirmadas == 0 and taxa == 0:
            reading = "Sem confirmação no recorte"
            pill = "green"
        elif decididas < 10 and taxa >= 50:
            reading = "Alta taxa, amostra pequena"
            pill = "amber"
        elif taxa >= 10:
            reading = "Pressão elevada de qualidade"
            pill = "red"
        elif taxa >= 5:
            reading = "Pressão moderada"
            pill = "amber"
        else:
            reading = "Volume relevante, taxa controlada"
            pill = "green"
        out.append(
            {
                **row,
                "bar_pct": max(8, int(round(100 * confirmadas / peak_confirmadas))),
                "pill": pill,
                "reading": reading,
            }
        )
    return out


def _load_workbook_contestacao_sheet(workbook_path: Path | None) -> pd.DataFrame | None:
    if not workbook_path or not Path(workbook_path).is_file():
        return None
    xl = pd.ExcelFile(workbook_path)
    if "Contestacao" not in xl.sheet_names:
        return None
    return pd.read_excel(workbook_path, "Contestacao")


def build_client_portfolio_row(
    client_slug: str,
    *,
    inicio: date | None = None,
    fim: date | None = None,
    client_meta: dict[str, Any] | None = None,
    contestacao_sink: list[pd.DataFrame] | None = None,
    fg_sink: list[pd.DataFrame] | None = None,
    workbook_path: Path | None = None,
    excel_contestacao: pd.DataFrame | None = None,
) -> dict[str, Any]:
    meta = client_meta or {}
    nome = meta.get("nome_curto") or meta.get("nome") or client_slug
    try:
        resolve_client_config(client_slug)
        bundle = load_bundle_hybrid(
            workbook_path,
            inicio=inicio,
            fim=fim,
            client_slug=client_slug,
            use_eo_db=True,
            excel_contestacao=excel_contestacao,
        )
        m = compute_metrics(bundle)
        decididas = int(m.conforme_sim + m.conforme_nao)
        monthly_confirmadas, monthly_contest = _monthly_from_contestacao(bundle)
        monthly_achados = _monthly_from_achados(bundle)
        monthly_treinamentos_horas, monthly_treinamentos_sessoes = _monthly_from_training(bundle)
        tre = bundle.treinamentos
        tre_h = bundle.treinamentos_horas
        corp_tre = int(tre.get("scope_treinamento", pd.Series(dtype=str)).eq("corporativo").sum()) if not tre.empty else 0
        corp_h = float(tre_h.loc[tre_h.get("scope_treinamento", pd.Series(dtype=str)).eq("corporativo"), "horas"].sum()) if not tre_h.empty and "horas" in tre_h.columns and "scope_treinamento" in tre_h.columns else 0.0
        corp_sessions = int(tre_h.get("scope_treinamento", pd.Series(dtype=str)).eq("corporativo").sum()) if not tre_h.empty else 0
        if contestacao_sink is not None and bundle.contestacao is not None and not bundle.contestacao.empty:
            tagged = bundle.contestacao.copy()
            tagged["_client_slug"] = client_slug
            tagged["_client_nome"] = nome
            contestacao_sink.append(tagged)
        if fg_sink is not None and bundle.falhas_gerais is not None and not bundle.falhas_gerais.empty:
            tagged_fg = bundle.falhas_gerais.copy()
            tagged_fg["_client_slug"] = client_slug
            tagged_fg["_client_nome"] = nome
            fg_sink.append(tagged_fg)
        client_timing: dict[str, Any] = {}
        if contestacao_sink is None:
            client_timing = build_portfolio_contestacao_timing(
                [bundle.contestacao] if bundle.contestacao is not None and not bundle.contestacao.empty else [],
                fim=fim,
            )
        id_cliente = meta.get("id_cliente")
        if id_cliente is None:
            try:
                from report_brb.client_registry import resolve_id_cliente

                id_cliente = resolve_id_cliente(client_slug)
            except Exception:  # noqa: BLE001
                id_cliente = None
        return {
            "slug": client_slug,
            "nome": meta.get("nome") or nome,
            "nome_curto": nome,
            "ok": True,
            "auditados": int(m.auditados_casos),
            "auditados_registros": int(m.auditados_registros),
            "achados_fg": int(m.casos_unicos_fg),
            "contestacoes": int(m.contestacao_registros),
            "falhas_confirmadas": int(m.conforme_nao),
            "sem_falha": int(m.conforme_sim),
            "contestacoes_decididas": decididas,
            "taxa_confirmada_pct": float(m.pct_procedente or 0.0),
            "na_notificadas": int(m.na_falhas_registros),
            "treinamentos": int(getattr(m, "treinamentos_registros", 0) or 0),
            "treinamentos_sessoes": int(getattr(m, "treinamentos_sessoes", 0) or 0),
            "treinamentos_agentes": int(getattr(m, "treinamentos_agentes", 0) or 0),
            "treinamentos_horas": float(getattr(m, "treinamentos_horas", 0) or 0),
            "treinamentos_corporativos": corp_tre,
            "treinamentos_horas_corporativas": corp_h,
            "treinamentos_sessoes_corporativas": corp_sessions,
            "taxa_achado_pct": float(m.auditados_taxa_achado or 0.0),
            "monthly_confirmadas": monthly_confirmadas,
            "monthly_contestacoes": monthly_contest,
            "monthly_achados": monthly_achados,
            "monthly_treinamentos_horas": monthly_treinamentos_horas,
            "monthly_treinamentos_sessoes": monthly_treinamentos_sessoes,
            "contestacao_timing": client_timing,
            "workflows": build_client_workflows(
                bundle,
                fim=fim,
                id_cliente=int(id_cliente) if id_cliente is not None else None,
            ),
            "diagnostics": build_client_diagnostics(bundle),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "slug": client_slug,
            "nome": meta.get("nome") or nome,
            "nome_curto": nome,
            "ok": False,
            "error": str(exc),
            "auditados": 0,
            "auditados_registros": 0,
            "achados_fg": 0,
            "contestacoes": 0,
            "falhas_confirmadas": 0,
            "sem_falha": 0,
            "contestacoes_decididas": 0,
            "taxa_confirmada_pct": 0.0,
            "na_notificadas": 0,
            "taxa_achado_pct": 0.0,
            "treinamentos": 0,
            "treinamentos_sessoes": 0,
            "treinamentos_agentes": 0,
            "treinamentos_horas": 0.0,
            "monthly_confirmadas": {},
            "monthly_contestacoes": {},
            "monthly_achados": {},
            "monthly_treinamentos_horas": {},
            "monthly_treinamentos_sessoes": {},
            "contestacao_timing": {},
            "workflows": [],
            "diagnostics": {"contestacao": _empty_source_diagnostics(), "auditoria": _empty_source_diagnostics()},
        }


def _portfolio_totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok_rows = [r for r in rows if r.get("ok")]
    # O corporativo aparece em cada cliente, mas entra uma vez no consolidado.
    # Use o maior valor observado para não depender da ordenação das linhas.
    corp_tre = max((int(r.get("treinamentos_corporativos") or 0) for r in ok_rows), default=0)
    corp_sess = max((int(r.get("treinamentos_sessoes_corporativas") or 0) for r in ok_rows), default=0)
    corp_hours = max((float(r.get("treinamentos_horas_corporativas") or 0) for r in ok_rows), default=0.0)
    total_decididas = sum(int(r["contestacoes_decididas"]) for r in ok_rows)
    total_confirmadas = sum(int(r["falhas_confirmadas"]) for r in ok_rows)
    taxa_ponderada = (
        round(100.0 * total_confirmadas / total_decididas, 1) if total_decididas else 0.0
    )
    return {
        "clientes_total": len(rows),
        "clientes_ok": len(ok_rows),
        "clientes_erro": len(rows) - len(ok_rows),
        "auditados": sum(int(r["auditados"]) for r in ok_rows),
        "auditados_registros": sum(int(r.get("auditados_registros") or 0) for r in ok_rows),
        "achados_fg": sum(int(r["achados_fg"]) for r in ok_rows),
        "contestacoes": sum(int(r["contestacoes"]) for r in ok_rows),
        "falhas_confirmadas": total_confirmadas,
        "sem_falha": sum(int(r["sem_falha"]) for r in ok_rows),
        "contestacoes_decididas": total_decididas,
        "taxa_confirmada_pct": taxa_ponderada,
        "na_notificadas": sum(int(r["na_notificadas"]) for r in ok_rows),
        "treinamentos": sum(max(0, int(r.get("treinamentos") or 0) - int(r.get("treinamentos_corporativos") or 0)) for r in ok_rows) + corp_tre,
        "treinamentos_sessoes": sum(max(0, int(r.get("treinamentos_sessoes") or 0) - int(r.get("treinamentos_sessoes_corporativas") or 0)) for r in ok_rows) + corp_sess,
        "treinamentos_agentes": sum(int(r.get("treinamentos_agentes") or 0) for r in ok_rows),
        "treinamentos_horas": round(sum(max(0.0, float(r.get("treinamentos_horas") or 0) - float(r.get("treinamentos_horas_corporativas") or 0)) for r in ok_rows) + corp_hours, 1),
    }


def rank_portfolio_rows(rows: list[dict[str, Any]], *, top_n: int = 10) -> list[dict[str, Any]]:
    ok_rows = [r for r in rows if r.get("ok")]
    ranked = sorted(
        ok_rows,
        key=lambda r: (
            int(r.get("falhas_confirmadas") or 0),
            int(r.get("contestacoes") or 0),
            int(r.get("auditados") or 0),
        ),
        reverse=True,
    )
    total_confirmadas = sum(int(r["falhas_confirmadas"]) for r in ok_rows) or 0
    top: list[dict[str, Any]] = []
    for index, row in enumerate(ranked[:top_n], start=1):
        item = dict(row)
        item["rank"] = index
        confirmadas = int(item.get("falhas_confirmadas") or 0)
        item["share_confirmadas_pct"] = (
            round(100.0 * confirmadas / total_confirmadas, 1) if total_confirmadas and confirmadas else 0.0
        )
        top.append(item)
    return top


def build_portfolio_contestacao_timing(
    frames: list[pd.DataFrame],
    *,
    fim: date | None = None,
) -> dict[str, Any]:
    """Agrega tempo entre análise contestada e recebimento no portfólio."""
    if not frames:
        return {}
    combined = pd.concat(frames, ignore_index=True)
    ref = datetime.combine(fim, datetime.min.time()) if fim else datetime.now()
    timing = contestacao_temporal(combined, ref)
    return _contestacao_timing_payload(timing)


def _contestacao_timing_payload(timing: dict[str, Any]) -> dict[str, Any]:
    if not timing.get("total"):
        return {}
    return {
        "mediana_dias": int(timing.get("mediana_dias") or 0),
        "total": int(timing.get("total") or 0),
        "pct_mais_90": float(timing.get("pct_mais_90") or 0.0),
        "mais_90": int(timing.get("mais_90") or 0),
        "faixas": timing.get("faixas") or [],
        "anos": timing.get("anos") or [],
    }


def attach_client_contestacao_timings(
    rows: list[dict[str, Any]],
    frames: list[pd.DataFrame],
    *,
    fim: date | None = None,
) -> None:
    """Preenche contestacao_timing por cliente após consolidar frames do portfólio."""
    if not frames:
        for row in rows:
            if row.get("ok"):
                row["contestacao_timing"] = {}
        return
    combined = pd.concat(frames, ignore_index=True)
    if "_client_slug" not in combined.columns:
        return
    ref = datetime.combine(fim, datetime.min.time()) if fim else datetime.now()
    by_slug: dict[str, dict[str, Any]] = {}
    for slug, grp in combined.groupby("_client_slug", sort=False):
        by_slug[str(slug)] = _contestacao_timing_payload(contestacao_temporal(grp, ref))
    for row in rows:
        if row.get("ok"):
            row["contestacao_timing"] = by_slug.get(str(row.get("slug") or ""), {})


def build_executive_story(
    rows: list[dict[str, Any]],
    top10: list[dict[str, Any]],
    totals: dict[str, Any],
    *,
    periodo: str,
) -> dict[str, Any]:
    """Narrativa executiva em arco: contexto → concentração → risco → ação."""
    if not totals.get("clientes_ok"):
        return {
            "headline": "Portfólio sem dados no recorte",
            "subtitle": "Não foi possível consolidar clientes ativos.",
            "beats": [],
            "closing": "",
            "paragraphs": ["Não foi possível montar o portfólio executivo."],
        }

    ok_rows = [r for r in rows if r.get("ok")]
    confirmadas = int(totals.get("falhas_confirmadas") or 0)
    taxa = float(totals.get("taxa_confirmada_pct") or 0)
    clientes = int(totals.get("clientes_ok") or 0)
    leader = top10[0] if top10 else {}
    top_share = round(sum(float(r.get("share_confirmadas_pct") or 0) for r in top10), 1)
    top3_share = round(sum(float(r.get("share_confirmadas_pct") or 0) for r in top10[:3]), 1)

    achados_total = int(totals.get("achados_fg") or 0)
    with_contest = [r for r in ok_rows if int(r.get("contestacoes") or 0) > 0]
    with_confirmed = [r for r in ok_rows if int(r.get("falhas_confirmadas") or 0) > 0]
    silent = [r for r in ok_rows if int(r.get("contestacoes") or 0) == 0]
    with_achados = [r for r in ok_rows if int(r.get("achados_fg") or 0) > 0]

    headline = (
        "O risco está concentrado em poucos clientes"
        if confirmadas and top_share >= 50
        else (
            f"{_fmt_n(confirmadas)} falhas confirmadas em {clientes} clientes — "
            f"top 10 concentra {top_share:.0f}% do volume"
            if confirmadas
            else f"{clientes} clientes auditados com baixa contestação no recorte"
        )
    )

    decision_lead = (
        f"A auditoria identificou {_fmt_n(achados_total)} achados no portfólio; "
        f"em contestação, {_fmt_n(confirmadas)} falhas foram confirmadas em {clientes} clientes. "
        f"Os 10 maiores concentram {top_share:.1f}% das confirmações"
        + (
            f" — priorize as três maiores contas"
            f"{' e valide clientes sem movimento' if silent else ''}."
            if top10
            else "."
        )
    )

    subtitle = (
        f"Entre {periodo}, foram revisados {_fmt_n(totals['auditados'])} protocolos, "
        f"registrados {_fmt_n(achados_total)} achados na auditoria e "
        f"{_fmt_n(totals['contestacoes'])} contestações "
        f"(taxa confirmada {taxa:.1f}%)."
    )

    beats: list[dict[str, str]] = [
        {
            "kind": "open",
            "title": "1 · Duas lentes, um portfólio",
            "body": (
                f"O portfólio reúne {clientes} clientes ativos. "
                f"A auditoria encontrou {_fmt_n(achados_total)} achados em {len(with_achados)} contas; "
                f"a contestação registrou movimento em {len(with_contest)} clientes, "
                f"com {_fmt_n(confirmadas)} falhas confirmadas. "
                f"São leituras complementares — achados mostram o que a operação detectou; "
                f"confirmações mostram o que o cliente questionou e foi procedente."
            ),
        },
        {
            "kind": "focus",
            "title": "2 · Onde o volume se concentra",
            "body": (
                f"O ranking não é uniforme: os 10 maiores respondem por {top_share:.1f}% das falhas confirmadas, "
                f"e só os três primeiros — "
                f"{', '.join(r['nome_curto'] for r in top10[:3]) if len(top10) >= 3 else '—'} — "
                f"somam {top3_share:.1f}%. "
                f"{'O líder isolado é ' + leader.get('nome_curto', '—') + ', com ' + _fmt_n(int(leader.get('falhas_confirmadas') or 0)) + ' confirmações.' if leader else ''}"
            ),
        },
    ]

    with_rate = [r for r in ok_rows if int(r.get("contestacoes_decididas") or 0) >= 10]
    if with_rate:
        highest = max(with_rate, key=lambda r: float(r.get("taxa_confirmada_pct") or 0))
        lowest = min(with_rate, key=lambda r: float(r.get("taxa_confirmada_pct") or 0))
        beats.append(
            {
                "kind": "risk",
                "title": "3 · O sinal de atenção",
                "body": (
                    f"Entre clientes com volume relevante (10+ decisões), "
                    f"{highest['nome_curto']} apresenta a maior taxa de confirmação ({highest['taxa_confirmada_pct']:.1f}%), "
                    f"enquanto {lowest['nome_curto']} opera na faixa mais baixa ({lowest['taxa_confirmada_pct']:.1f}%). "
                    "A leitura executiva não compara ‘certo vs errado’ — indica onde a pressão de qualidade é mais intensa."
                ),
            }
        )
    elif confirmadas:
        beats.append(
            {
                "kind": "risk",
                "title": "3 · O sinal de atenção",
                "body": (
                    "O volume confirmado ainda é pulverizado: nenhum cliente isolado domina a taxa. "
                    "O risco está na soma de pequenos volumes, não em um outlier evidente."
                ),
            }
        )

    action_bits = []
    if leader:
        action_bits.append(f"priorizar acompanhamento com {leader.get('nome_curto')}")
    if silent:
        action_bits.append(f"validar carga ou ausência real de contestação em {len(silent)} clientes silenciosos")
    action_bits.append("revisar causas dominantes nos top 3 antes do próximo fechamento")
    beats.append(
        {
            "kind": "action",
            "title": "4 · Próximo passo sugerido",
            "body": (
                "Para o ciclo seguinte, o CS pode "
                + "; ".join(action_bits)
                + f". A referência de taxa do portfólio ({taxa:.1f}%) orienta o ritmo de acompanhamento."
            ),
        }
    )

    closing = (
        "Auditoria e contestação respondem perguntas diferentes — juntas, mostram escala, "
        "concentração e pressão; o detalhe por cliente aprofunda a leitura executiva."
    )

    paragraphs = [subtitle] + [b["body"] for b in beats] + [closing]
    return {
        "headline": headline,
        "decision_lead": decision_lead,
        "subtitle": subtitle,
        "beats": beats,
        "closing": closing,
        "paragraphs": paragraphs,
    }


_FG_TIPO_INTERPRETACAO: dict[str, str] = {
    "Automático": "Calibrar regras, limites e exceções.",
    "Mapeamento": "Revisar cobertura e classificação dos cenários.",
    "Manual": "Reforçar procedimento, orientação ou barreira operacional.",
    "Processual": "Revisar fluxo, etapa e governança.",
    "Necessidade de avaliação": "Completar triagem e classificação do achado.",
    "Não informado": "Completar a classificação do registro.",
}


def _empty_source_diagnostics() -> dict[str, Any]:
    return {
        "total": 0,
        "tipos": [],
        "tipo_inferred": False,
        "pareto_motivos": [],
        "motivos_por_mes": [],
        "top_motivo": None,
    }


def _prepare_fg_work(fg: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Exclui duplicados de contestação e normaliza cenário/tipo de falha."""
    from report_brb.brb_normalize import padronizar_descricao
    from report_brb.brb_render_cliente import _find_fg_col, _normalize_tipo_falha_fg

    if fg.empty:
        return fg.copy(), 0, 0
    total_bruto = len(fg)
    dup_n = int(fg.get("duplicado_contestacao", pd.Series(dtype=bool)).fillna(False).sum())
    work = fg.copy()
    if "duplicado_contestacao" in work.columns:
        work = work[~work["duplicado_contestacao"].fillna(False)]
    scenario_col = next(
        (col for col in ("Novo cenário", "Cenário", "descricao_padrao") if col in work.columns),
        None,
    )
    tipo_col = _find_fg_col(work, "tipo", "falha")
    if scenario_col:
        work["_cenario"] = work[scenario_col].fillna("").astype(str).map(
            lambda value: padronizar_descricao(value) or "Não informado"
        )
    else:
        work["_cenario"] = "Não informado"
    if tipo_col:
        work["_tipo_fg"] = work[tipo_col].map(_normalize_tipo_falha_fg)
    else:
        work["_tipo_fg"] = "Não informado"
    return work, total_bruto, dup_n


def _pareto_from_grouped_rows(
    rows: list[dict[str, Any]],
    *,
    total: int,
    motivo_key: str = "motivo",
    tipo_key: str = "tipo",
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not rows or total <= 0:
        return []
    ordered = sorted(rows, key=lambda row: int(row.get("quantidade") or 0), reverse=True)
    if len(ordered) <= limit:
        return [
            {
                "motivo": str(row[motivo_key]),
                "tipo": str(row.get(tipo_key) or "—"),
                "quantidade": int(row["quantidade"]),
                "pct": round(100.0 * int(row["quantidade"]) / total, 1),
            }
            for row in ordered
        ]
    top = ordered[: limit - 1]
    outros_qtd = sum(int(row["quantidade"]) for row in ordered[limit - 1 :])
    pareto = [
        {
            "motivo": str(row[motivo_key]),
            "tipo": str(row.get(tipo_key) or "—"),
            "quantidade": int(row["quantidade"]),
            "pct": round(100.0 * int(row["quantidade"]) / total, 1),
        }
        for row in top
    ]
    pareto.append(
        {
            "motivo": "Outros",
            "tipo": "—",
            "quantidade": outros_qtd,
            "pct": round(100.0 * outros_qtd / total, 1),
        }
    )
    return pareto


def _monthly_top_motivo(
    work: pd.DataFrame,
    *,
    date_col: str,
    motivo_col: str,
) -> list[dict[str, Any]]:
    if work.empty or date_col not in work.columns or motivo_col not in work.columns:
        return []
    monthly: list[dict[str, Any]] = []
    frame = work.copy()
    frame["mes_ref"] = _mes_label(frame[date_col])
    frame = frame[frame["mes_ref"].notna()]
    for ym, grp in frame.groupby("mes_ref", sort=True):
        counts = grp[motivo_col].value_counts()
        if counts.empty:
            continue
        top_motivo = str(counts.index[0])
        top_n = int(counts.iloc[0])
        grp_total = len(grp)
        monthly.append(
            {
                "ym": str(ym),
                "label": _month_abbr(str(ym)),
                "total": grp_total,
                "top_motivo": top_motivo,
                "top_quantidade": top_n,
                "top_pct": round(100.0 * top_n / grp_total, 1) if grp_total else 0.0,
            }
        )
    return monthly


def _build_contestacao_diagnostics(frames: list[pd.DataFrame]) -> dict[str, Any]:
    from report_brb.brb_render_cliente import (
        _group_procedentes,
        _group_procedentes_by_tipo,
        _procedentes_contestacao,
    )

    empty = _empty_source_diagnostics()
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return empty

    combined = pd.concat(usable, ignore_index=True)
    proc = _procedentes_contestacao(combined)
    total = len(proc)
    if total == 0:
        return empty

    tipo_df, tipo_inferred = _group_procedentes_by_tipo(proc)
    motivo_df = _group_procedentes(combined)
    tipos: list[dict[str, Any]] = []
    for _, row in tipo_df.iterrows():
        casos = int(row["Casos"])
        tipos.append(
            {
                "label": str(row["Tipo de procedência"]),
                "casos": casos,
                "pct": round(100.0 * casos / total, 1),
                "interpretacao": str(row.get("Interpretação") or ""),
            }
        )
    pareto_rows = [
        {
            "motivo": str(row["Motivo"]),
            "tipo": str(row.get("Tipo de procedência") or "—"),
            "quantidade": int(row["Quantidade"]),
        }
        for _, row in motivo_df.iterrows()
    ]
    pareto = _pareto_from_grouped_rows(pareto_rows, total=total)
    motivos_por_mes = _monthly_top_motivo(proc, date_col="Data", motivo_col="_motivo")
    por_tipo = {}
    for tipo, grupo in proc.groupby("_tipo_procedencia", dropna=False):
        tipo_key = str(tipo or "Não informado")
        rows = [
            {"motivo": str(row["Motivo"]), "tipo": tipo_key, "quantidade": int(row["Quantidade"])}
            for _, row in _group_procedentes(grupo).iterrows()
        ]
        por_tipo[tipo_key] = {
            "pareto_motivos": _pareto_from_grouped_rows(rows, total=len(grupo)),
            "motivos_por_mes": _monthly_top_motivo(grupo, date_col="Data", motivo_col="_motivo"),
            "records": [
                {"cliente": str(item.get("_client_slug") or item.get("Cliente") or ""), "protocolo": str(item.get("Protocolo") or ""), "cenario": str(item.get("_motivo") or ""), "tipo": tipo_key, "etapa": str(item.get("Etapa") or ""), "workflow": str(item.get("_workflow") or item.get("Workflow") or "")}
                for _, item in grupo.iterrows()
            ],
        }
    return {
        "total": total,
        "tipos": tipos,
        "tipo_inferred": bool(tipo_inferred),
        "pareto_motivos": pareto,
        "motivos_por_mes": motivos_por_mes,
        "por_tipo": por_tipo,
        "top_motivo": pareto[0] if pareto else None,
    }


def _build_auditoria_diagnostics(frames: list[pd.DataFrame]) -> dict[str, Any]:
    from report_brb.brb_render_cliente import _fg_perfil_por_tipo

    empty = _empty_source_diagnostics()
    empty["achados_bruto"] = 0
    empty["duplicados_contestacao"] = 0
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return empty

    combined = pd.concat(usable, ignore_index=True)
    work, total_bruto, dup_n = _prepare_fg_work(combined)
    total = len(work)
    if total == 0:
        return {
            **empty,
            "achados_bruto": total_bruto,
            "duplicados_contestacao": dup_n,
        }

    tipo_df, _, _ = _fg_perfil_por_tipo(combined)
    tipos: list[dict[str, Any]] = []
    for _, row in tipo_df.iterrows():
        casos = int(row["Casos"])
        label = str(row["Tipo de falha"])
        tipos.append(
            {
                "label": label,
                "casos": casos,
                "pct": round(100.0 * casos / total, 1),
                "interpretacao": _FG_TIPO_INTERPRETACAO.get(label, "Validar classificação."),
            }
        )

    pareto_rows: list[dict[str, Any]] = []
    for cenario, grp in work.groupby("_cenario", dropna=False):
        tipo_counts = grp["_tipo_fg"].value_counts()
        tipo_dom = str(tipo_counts.index[0]) if len(tipo_counts) else "Não informado"
        if len(tipo_counts) > 1:
            tipo_dom = f"{tipo_dom}*"
        pareto_rows.append(
            {
                "motivo": str(cenario),
                "tipo": tipo_dom,
                "quantidade": len(grp),
            }
        )
    pareto = _pareto_from_grouped_rows(pareto_rows, total=total)
    motivos_por_mes = _monthly_top_motivo(
        work,
        date_col="Data de Análise",
        motivo_col="_cenario",
    )
    por_tipo = {}
    for tipo, grupo in work.groupby("_tipo_fg", dropna=False):
        tipo_key = str(tipo or "Não informado")
        rows = []
        for cenario, cenario_group in grupo.groupby("_cenario", dropna=False):
            rows.append({"motivo": str(cenario), "tipo": tipo_key, "quantidade": len(cenario_group)})
        por_tipo[tipo_key] = {
            "pareto_motivos": _pareto_from_grouped_rows(rows, total=len(grupo)),
            "motivos_por_mes": _monthly_top_motivo(
                grupo, date_col="Data de Análise", motivo_col="_cenario"
            ),
            "records": [
                {"cliente": str(item.get("_client_slug") or item.get("Cliente") or ""), "protocolo": str(item.get("Protocolo") or ""), "cenario": str(item.get("_cenario") or ""), "tipo": tipo_key, "etapa": str(item.get("Etapa") or ""), "workflow": str(item.get("Workflow") or "")}
                for _, item in grupo.iterrows()
            ],
        }
    return {
        "total": total,
        "achados_bruto": total_bruto,
        "duplicados_contestacao": dup_n,
        "tipos": tipos,
        "tipo_inferred": False,
        "pareto_motivos": pareto,
        "motivos_por_mes": motivos_por_mes,
        "por_tipo": por_tipo,
        "top_motivo": pareto[0] if pareto else None,
    }


def build_portfolio_diagnostics(
    contestacao_frames: list[pd.DataFrame],
    fg_frames: list[pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Agrega causas e padrões de contestação confirmada e achados da auditoria."""
    contestacao = _build_contestacao_diagnostics(contestacao_frames)
    auditoria = _build_auditoria_diagnostics(fg_frames or [])
    return {
        "contestacao": contestacao,
        "auditoria": auditoria,
    }


def build_monthly_scopes(
    rows: list[dict[str, Any]],
    top10: list[dict[str, Any]],
    *,
    inicio: date | None = None,
    fim: date | None = None,
) -> dict[str, Any]:
    months = _month_range(inicio, fim)
    top10_slugs = {r.get("slug") for r in top10 if r.get("slug")}
    top3_slugs = {r.get("slug") for r in top10[:3] if r.get("slug")}

    def merge(field: str, allowed: set[str] | None) -> dict[str, int]:
        merged: dict[str, int] = {}
        for row in rows:
            if not row.get("ok"):
                continue
            if allowed is not None and row.get("slug") not in allowed:
                continue
            for ym, value in (row.get(field) or {}).items():
                merged[ym] = merged.get(ym, 0) + int(value)
        return dict(sorted(merged.items()))

    def scoped_metric(field: str) -> dict[str, dict[str, int]]:
        portfolio = merge(field, None)
        top10_data = merge(field, top10_slugs)
        top3_data = merge(field, top3_slugs)
        all_keys = set(portfolio) | set(top10_data) | set(top3_data)
        month_keys = months or sorted(all_keys)
        return {
            "portfolio": {ym: int(portfolio.get(ym, 0)) for ym in month_keys},
            "top10": {ym: int(top10_data.get(ym, 0)) for ym in month_keys},
            "top3": {ym: int(top3_data.get(ym, 0)) for ym in month_keys},
        }

    def scoped_training(field: str) -> dict[str, dict[str, float]]:
        """Agrega capacitação sem repetir o bloco corporativo por cliente."""
        eligible = [r for r in rows if r.get("ok")]
        all_keys = {
            ym for row in eligible for ym in (row.get(field) or {}).keys()
        }
        corporate: dict[str, float] = {}
        for ym in all_keys:
            values = [float((row.get(field) or {}).get(ym, 0) or 0) for row in eligible]
            corporate[ym] = min(values) if values else 0.0

        def merge_training(allowed: set[str] | None) -> dict[str, float]:
            selected = [r for r in eligible if allowed is None or r.get("slug") in allowed]
            merged: dict[str, float] = {}
            for row in selected:
                for ym, value in (row.get(field) or {}).items():
                    base = corporate.get(ym, 0.0)
                    merged[ym] = merged.get(ym, 0.0) + max(0.0, float(value or 0) - base)
            for ym, base in corporate.items():
                if selected:
                    merged[ym] = merged.get(ym, 0.0) + base
            return dict(sorted(merged.items()))

        portfolio = merge_training(None)
        top10_data = merge_training(top10_slugs)
        top3_data = merge_training(top3_slugs)
        month_keys = months or sorted(set(portfolio) | set(top10_data) | set(top3_data))
        return {
            "portfolio": {ym: round(float(portfolio.get(ym, 0)), 1) for ym in month_keys},
            "top10": {ym: round(float(top10_data.get(ym, 0)), 1) for ym in month_keys},
            "top3": {ym: round(float(top3_data.get(ym, 0)), 1) for ym in month_keys},
        }

    confirmadas = scoped_metric("monthly_confirmadas")
    achados = scoped_metric("monthly_achados")
    contestacoes = scoped_metric("monthly_contestacoes")
    treinamentos_horas = scoped_training("monthly_treinamentos_horas")
    treinamentos_sessoes = scoped_training("monthly_treinamentos_sessoes")
    month_keys = months or sorted(
        set(confirmadas["portfolio"]) | set(achados["portfolio"]) | set(contestacoes["portfolio"])
        | set(treinamentos_horas["portfolio"])
    )
    return {
        "months": month_keys,
        "portfolio": confirmadas["portfolio"],
        "top10": confirmadas["top10"],
        "top3": confirmadas["top3"],
        "metrics": {
            "confirmadas": confirmadas,
            "achados": achados,
            "contestacoes": contestacoes,
            "treinamentos_horas": treinamentos_horas,
            "treinamentos_sessoes": treinamentos_sessoes,
        },
        "has_data": any(confirmadas["portfolio"].values())
        or any(achados["portfolio"].values())
        or any(contestacoes["portfolio"].values()),
        "partial_month": build_portfolio_monthly(rows, inicio=inicio, fim=fim).get("partial_month"),
    }


def build_portfolio_narrative(
    rows: list[dict[str, Any]],
    top10: list[dict[str, Any]],
    totals: dict[str, Any],
    *,
    periodo: str,
) -> list[str]:
    return build_executive_story(rows, top10, totals, periodo=periodo)["paragraphs"]


def build_executive_portfolio(
    *,
    inicio: date | None = None,
    fim: date | None = None,
    client_slugs: list[str] | None = None,
    top_n: int = 10,
    workbook_path: Path | None = None,
) -> dict[str, Any]:
    """Monta payload executivo: todos os clientes + top N + narrativa."""
    catalog = {row["slug"]: row for row in list_report_clients(enabled_only=True)}
    slugs = client_slugs or list_scheduled_report_client_slugs(enabled_only=True)
    excel_contestacao = _load_workbook_contestacao_sheet(workbook_path)
    rows: list[dict[str, Any]] = []
    contestacao_frames: list[pd.DataFrame] = []
    fg_frames: list[pd.DataFrame] = []
    for slug in slugs:
        rows.append(
            build_client_portfolio_row(
                slug,
                inicio=inicio,
                fim=fim,
                client_meta=catalog.get(slug),
                contestacao_sink=contestacao_frames,
                fg_sink=fg_frames,
                workbook_path=workbook_path,
                excel_contestacao=excel_contestacao,
            )
        )

    attach_client_contestacao_timings(rows, contestacao_frames, fim=fim)

    rows.sort(
        key=lambda r: (
            int(r.get("falhas_confirmadas") or 0),
            int(r.get("contestacoes") or 0),
        ),
        reverse=True,
    )
    totals = _portfolio_totals(rows)
    total_confirmadas = int(totals.get("falhas_confirmadas") or 0)
    for row in rows:
        if row.get("ok"):
            confirmadas = int(row.get("falhas_confirmadas") or 0)
            row["share_confirmadas_pct"] = (
                round(100.0 * confirmadas / total_confirmadas, 1)
                if total_confirmadas and confirmadas
                else 0.0
            )
    top10 = rank_portfolio_rows(rows, top_n=top_n)
    periodo = _period_label(inicio, fim)
    story = build_executive_story(rows, top10, totals, periodo=periodo)
    monthly = build_portfolio_monthly(rows, inicio=inicio, fim=fim)
    silent_count = len(
        [r for r in rows if r.get("ok") and int(r.get("contestacoes") or 0) == 0]
    )
    top3_share = round(sum(float(r.get("share_confirmadas_pct") or 0) for r in top10[:3]), 1)
    monthly_scopes = build_monthly_scopes(rows, top10, inicio=inicio, fim=fim)
    contestacao_timing = build_portfolio_contestacao_timing(contestacao_frames, fim=fim)

    return {
        "periodo": periodo,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "inicio": inicio.isoformat() if inicio else None,
        "fim": fim.isoformat() if fim else None,
        "totals": totals,
        "top10": top10,
        "rows": rows,
        "narrative": story["paragraphs"],
        "story": story,
        "monthly": monthly,
        "monthly_scopes": monthly_scopes,
        "contestacao_timing": contestacao_timing,
        "diagnostics": build_portfolio_diagnostics(contestacao_frames, fg_frames),
        "timeline_events": build_timeline_events(
            monthly, top10, totals, inicio=inicio, fim=fim
        ),
        "risk_rows": build_risk_table_rows(rows),
        "silent_clients_count": silent_count,
        "top3_share_pct": top3_share,
    }
