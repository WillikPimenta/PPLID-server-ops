# -*- coding: utf-8 -*-
"""Agrega o Excel consolidado Case → CaseConsolidadoSnapshot / DailyAgg."""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.produtividade_case.constants import (
    ALERTAS_FACT_MAX_LEN,
    CRUZAMENTO_KEY_SEP,
    DIM_ALERTA_DESTINO,
    DIM_CLIENTE_ORIGEM,
    DIM_CRUZAMENTO_WF_RESULTADO,
    DIM_MATRICULA_DESTINO,
    DIM_MATRICULA_ORIGEM,
    DIM_NH_ORIGEM,
    DIM_RESULTADO_DESTINO,
    DIM_STATUS_DESTINO,
    DIM_TIPO_CONCLUSAO_ORIGEM,
    DIM_VOLUME_CADASTRO_DIA,
    DIM_VOLUME_DIA,
    DIM_WORKFLOW_ORIGEM,
    STALE_CONSOLIDADO_HOURS,
    VOLUME_DIA_KEY,
)
from apps.produtividade_case.models import (
    CaseConsolidadoDailyAgg,
    CaseConsolidadoFact,
    CaseConsolidadoSnapshot,
)
from apps.produtividade_case.services.excel_reader import read_consolidado_rows
from apps.produtividade_case.services.fila_sync import _isoformat_local
from apps.produtividade_case.services.source_path import extract_periodo_mes


def _norm_key(value: object, *, empty: str = "(vazio)", max_len: int = 255) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        text = empty
    return text[:max_len]


def _norm_mat(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text or text == "nan":
        return "(sem matricula)"
    return text[:64]


def _day_from_row(row: dict) -> date | None:
    dt = row.get("conclusao_destino_at")
    if dt is None:
        return None
    if hasattr(dt, "date"):
        return dt.date()
    return None


def _cadastro_day_from_row(row: dict) -> date | None:
    dt = row.get("cadastro_destino_at")
    if dt is None:
        return None
    if hasattr(dt, "date"):
        return dt.date()
    return None


def _split_alertas(text: object) -> list[str]:
    raw = str(text or "").strip()
    if not raw or raw.lower() == "nan" or raw == "-":
        return []
    # alertas joined with " | " in excel
    return [p.strip() for p in raw.split("|") if p.strip()]


def build_daily_agg_buckets(
    rows: list[dict],
) -> tuple[int, dict[tuple[date, str, str], dict[str, int]]]:
    """Retorna (total_protocolos, buckets[(day, dim, key)] -> {count, seconds})."""
    buckets: dict[tuple[date, str, str], dict[str, int]] = defaultdict(
        lambda: {"count": 0, "seconds": 0}
    )
    # Preserve cardinalidade completa; Top-N é aplicado somente na leitura global.
    cliente_day: dict[date, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"count": 0, "seconds": 0})
    )
    mat_orig_day: dict[date, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"count": 0, "seconds": 0})
    )
    alerta_day: dict[date, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: {"count": 0, "seconds": 0})
    )

    total = 0
    for row in rows:
        day = _day_from_row(row)
        if day is None:
            continue
        total += 1
        secs = int(row.get("tempo_analise_segundos") or 0)
        if secs < 0:
            secs = 0

        pairs = (
            (DIM_WORKFLOW_ORIGEM, _norm_key(row.get("workflow_origem"))),
            (DIM_MATRICULA_DESTINO, _norm_mat(row.get("matricula_destino"))),
            (DIM_RESULTADO_DESTINO, _norm_key(row.get("resultado_destino"))),
            (DIM_STATUS_DESTINO, _norm_key(row.get("status_destino"))),
            (
                DIM_TIPO_CONCLUSAO_ORIGEM,
                _norm_key(row.get("tipo_conclusao_origem")),
            ),
            (DIM_NH_ORIGEM, _norm_key(row.get("nh_origem"))),
            (DIM_VOLUME_DIA, VOLUME_DIA_KEY),
            (
                DIM_CRUZAMENTO_WF_RESULTADO,
                _norm_key(
                    f"{_norm_key(row.get('workflow_origem'))}"
                    f"{CRUZAMENTO_KEY_SEP}"
                    f"{_norm_key(row.get('resultado_destino'))}",
                    max_len=255,
                ),
            ),
        )
        for dim, key in pairs:
            slot = buckets[(day, dim, key)]
            slot["count"] += 1
            slot["seconds"] += secs

        cad_day = _cadastro_day_from_row(row)
        if cad_day is not None:
            cad_slot = buckets[(cad_day, DIM_VOLUME_CADASTRO_DIA, VOLUME_DIA_KEY)]
            cad_slot["count"] += 1
            cad_slot["seconds"] += secs

        cli = _norm_key(row.get("cliente_origem"))
        cliente_day[day][cli]["count"] += 1
        cliente_day[day][cli]["seconds"] += secs

        mo = _norm_mat(row.get("matricula_origem"))
        mat_orig_day[day][mo]["count"] += 1
        mat_orig_day[day][mo]["seconds"] += secs

        alertas = _split_alertas(row.get("alertas_destino"))
        if not alertas:
            alerta_day[day]["(sem alerta)"]["count"] += 1
            alerta_day[day]["(sem alerta)"]["seconds"] += secs
        else:
            for alert in alertas:
                ak = _norm_key(alert, max_len=255)
                alerta_day[day][ak]["count"] += 1
                alerta_day[day][ak]["seconds"] += secs

    for day, counters in cliente_day.items():
        for key, vals in counters.items():
            buckets[(day, DIM_CLIENTE_ORIGEM, key)] = vals
    for day, counters in mat_orig_day.items():
        for key, vals in counters.items():
            buckets[(day, DIM_MATRICULA_ORIGEM, key)] = vals
    for day, counters in alerta_day.items():
        for key, vals in counters.items():
            buckets[(day, DIM_ALERTA_DESTINO, key)] = vals

    return total, buckets


def _fact_from_row(snap: CaseConsolidadoSnapshot, periodo: str, row: dict, source: str) -> CaseConsolidadoFact | None:
    if _day_from_row(row) is None:
        return None
    protocolo = _norm_key(row.get("protocolo_destino"), empty="")
    if not protocolo:
        return None
    secs = row.get("tempo_analise_segundos")
    try:
        secs_i = int(secs) if secs is not None else None
    except (TypeError, ValueError):
        secs_i = None
    if secs_i is not None and secs_i < 0:
        secs_i = 0
    return CaseConsolidadoFact(
        snapshot=snap,
        periodo_mes=periodo,
        protocolo_destino=protocolo[:128],
        protocolo_origem=_norm_key(row.get("protocolo_origem"), empty="")[:128],
        workflow_origem=_norm_key(row.get("workflow_origem"), empty="")[:255],
        nh_origem=_norm_key(row.get("nh_origem"), empty="")[:64],
        cliente_origem=_norm_key(row.get("cliente_origem"), empty="")[:255],
        matricula_origem=_norm_mat(row.get("matricula_origem"))[:64],
        matricula_destino=_norm_mat(row.get("matricula_destino"))[:64],
        resultado_origem=_norm_key(row.get("resultado_origem"), empty="")[:128],
        resultado_destino=_norm_key(row.get("resultado_destino"), empty="")[:128],
        status_destino=_norm_key(row.get("status_destino"), empty="")[:64],
        tipo_conclusao_origem=_norm_key(row.get("tipo_conclusao_origem"), empty="")[:32],
        alertas_destino=str(row.get("alertas_destino") or "")[:ALERTAS_FACT_MAX_LEN],
        cadastro_origem_at=row.get("cadastro_origem_at"),
        cadastro_destino_at=row.get("cadastro_destino_at"),
        conclusao_destino_at=row.get("conclusao_destino_at"),
        inspecao_at=row.get("inspecao_at"),
        tempo_analise_segundos=secs_i,
        source_file=source[:1024],
    )


def sync_consolidado_daily_aggs(
    path: Path | str,
    *,
    periodo_mes: str | None = None,
    rows: list[dict] | None = None,
) -> CaseConsolidadoSnapshot:
    """Reload mensal: atomics curtas + bulk chunked (sem atomic monolítico).

    `rows` opcional evita releitura do Excel quando o caller já parseou.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Consolidado Case não encontrado: {path}")

    started = time.perf_counter()
    periodo = (periodo_mes or "").strip() or extract_periodo_mes(path)
    if rows is None:
        rows = read_consolidado_rows(path, periodo)
    total, buckets = build_daily_agg_buckets(rows)
    source = str(path.resolve())

    from apps.produtividade_case.services.mvp_metrics import build_workflow_tempo_stats_map

    tempo_stats = build_workflow_tempo_stats_map(rows)

    # Delete curto (cascade diários/facts do snapshot) + órfãos por período
    with transaction.atomic():
        CaseConsolidadoSnapshot.objects.filter(periodo_mes=periodo).delete()
        CaseConsolidadoFact.objects.filter(periodo_mes=periodo).delete()

    with transaction.atomic():
        snap = CaseConsolidadoSnapshot.objects.create(
            periodo_mes=periodo,
            captured_at=timezone.now(),
            total_protocolos=total,
            success=True,
            source_file=source,
            duration_seconds=round(time.perf_counter() - started, 3),
            message=f"Agregados+fatos consolidado Case ({total} protocolos).",
            tempo_stats_json=tempo_stats,
        )

    batch = 1000
    aggs = [
        CaseConsolidadoDailyAgg(
            snapshot=snap,
            periodo_mes=periodo,
            day=day,
            dimension=dim,
            key=key,
            count=vals["count"],
            analysis_seconds_sum=vals["seconds"],
        )
        for (day, dim, key), vals in buckets.items()
    ]
    for i in range(0, len(aggs), batch):
        with transaction.atomic():
            CaseConsolidadoDailyAgg.objects.bulk_create(
                aggs[i : i + batch], batch_size=batch
            )

    facts = []
    for row in rows:
        fact = _fact_from_row(snap, periodo, row, source)
        if fact is not None:
            facts.append(fact)
    for i in range(0, len(facts), batch):
        with transaction.atomic():
            CaseConsolidadoFact.objects.bulk_create(
                facts[i : i + batch], batch_size=batch
            )

    snap.duration_seconds = round(time.perf_counter() - started, 3)
    snap.save(update_fields=["duration_seconds"])
    return snap


def latest_consolidado_snapshot(
    periodo_mes: str | None = None,
) -> CaseConsolidadoSnapshot | None:
    qs = CaseConsolidadoSnapshot.objects.filter(success=True)
    if periodo_mes:
        qs = qs.filter(periodo_mes=periodo_mes.strip())
    return qs.order_by("-captured_at").first()


_MESES_PT = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def _period_token(day: date) -> str:
    return f"{_MESES_PT[day.month - 1]}-{day.year}"


def _requested_periods(
    *, periodo_mes: str | None, date_from: date | None, date_to: date | None
) -> list[str]:
    if date_from or date_to:
        start = date_from or date_to
        end = date_to or date_from
        assert start is not None and end is not None
        cursor = date(start.year, start.month, 1)
        last = date(end.year, end.month, 1)
        periods: list[str] = []
        while cursor <= last:
            periods.append(_period_token(cursor))
            cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
        return periods
    if periodo_mes:
        return [periodo_mes.strip().lower()]
    latest = latest_consolidado_snapshot()
    return [latest.periodo_mes] if latest else []


def selected_consolidado_snapshots(
    *, periodo_mes: str | None = None, date_from: date | None = None, date_to: date | None = None
) -> list[CaseConsolidadoSnapshot]:
    """Retorna o snapshot bem-sucedido mais recente de cada mês solicitado."""
    periods = _requested_periods(periodo_mes=periodo_mes, date_from=date_from, date_to=date_to)
    if not periods:
        return []
    rows = CaseConsolidadoSnapshot.objects.filter(
        success=True, periodo_mes__in=periods
    ).order_by("periodo_mes", "-captured_at", "-id")
    selected: dict[str, CaseConsolidadoSnapshot] = {}
    for snap in rows:
        selected.setdefault(snap.periodo_mes.lower(), snap)
    return [selected[p] for p in periods if p in selected]


def period_metadata(
    *, periodo_mes: str | None = None, date_from: date | None = None, date_to: date | None = None
) -> dict[str, Any]:
    periods = _requested_periods(periodo_mes=periodo_mes, date_from=date_from, date_to=date_to)
    snaps = selected_consolidado_snapshots(
        periodo_mes=periodo_mes, date_from=date_from, date_to=date_to
    )
    found = {snap.periodo_mes.lower() for snap in snaps}
    missing = [p for p in periods if p not in found]
    return {
        "periodo_meses": periods,
        "date_from": date_from.isoformat() if date_from else None,
        "date_to": date_to.isoformat() if date_to else None,
        "range_complete": bool(periods) and not missing,
        "missing_periodos": missing,
    }


def consolidado_is_stale(
    snap: CaseConsolidadoSnapshot | None,
    *,
    hours: float = STALE_CONSOLIDADO_HOURS,
) -> bool:
    if snap is None:
        return True
    return snap.captured_at < timezone.now() - timedelta(hours=hours)


def _period_filter_params(
    *,
    periodo_mes: str | None,
    date_from: date | None,
    date_to: date | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if periodo_mes:
        out["periodo_mes"] = periodo_mes.strip()
    if date_from:
        out["day__gte"] = date_from
    if date_to:
        out["day__lte"] = date_to
    return out


def _agg_qs(
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    dimension: str | None = None,
):
    snaps = selected_consolidado_snapshots(
        periodo_mes=periodo_mes, date_from=date_from, date_to=date_to
    )
    if not snaps:
        return None, CaseConsolidadoDailyAgg.objects.none()

    snap = snaps[-1]
    qs = CaseConsolidadoDailyAgg.objects.filter(snapshot__in=snaps)
    filters = _period_filter_params(
        periodo_mes=None,  # já filtrado pelo snapshot
        date_from=date_from,
        date_to=date_to,
    )
    if filters:
        qs = qs.filter(**filters)
    if dimension:
        qs = qs.filter(dimension=dimension)
    return snap, qs


def _rollup_dimension(qs) -> list[dict[str, Any]]:
    from django.db.models import Sum

    rows = (
        qs.values("key")
        .annotate(
            count=Sum("count"),
            analysis_seconds_sum=Sum("analysis_seconds_sum"),
        )
        .order_by("-count", "key")
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        count = int(row["count"] or 0)
        secs = int(row["analysis_seconds_sum"] or 0)
        tma = round(secs / count, 1) if count else 0.0
        out.append(
            {
                "key": row["key"],
                "count": count,
                "analysis_seconds_sum": secs,
                "tma_seconds": tma,
            }
        )
    return out


def serialize_analitica_status(
    *,
    periodo_mes: str | None = None,
) -> dict[str, Any]:
    # Um período explicitamente solicitado nunca deve retornar dados de outro mês.
    snap = latest_consolidado_snapshot(periodo_mes)
    if snap is None:
        return {
            "has_data": False,
            "periodo_mes": periodo_mes or "",
            "captured_at": None,
            "total_protocolos": 0,
            "stale": True,
            "source_file": "",
            "message": "Nenhum consolidado Case agregado disponível.",
        }
    return {
        "has_data": True,
        "periodo_mes": snap.periodo_mes,
        "captured_at": _isoformat_local(snap.captured_at),
        "total_protocolos": snap.total_protocolos,
        "stale": consolidado_is_stale(snap),
        "source_file": Path(snap.source_file).name if snap.source_file else "",
        "message": snap.message or "",
        "snapshot_id": snap.pk,
    }


def serialize_analitica_resumo(
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    top: int = 15,
) -> dict[str, Any]:
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    snap, qs = _agg_qs(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
    )
    if snap is None:
        base.update(
            {
                "by_workflow_origem": [],
                "by_resultado_destino": [],
                "by_status_destino": [],
                "by_tipo_conclusao_origem": [],
                "by_nh_origem": [],
                "by_cliente_origem": [],
                "by_matricula_origem": [],
                "by_alerta_destino": [],
                "total_periodo": 0,
            }
        )
        return base

    def top_n(dim: str) -> list[dict]:
        return _rollup_dimension(qs.filter(dimension=dim))[: max(1, top)]

    volume_rows = _rollup_dimension(qs.filter(dimension=DIM_VOLUME_DIA))
    total_periodo = sum(r["count"] for r in volume_rows)
    base.update(
        {
            "periodo_mes": snap.periodo_mes,
            "total_periodo": total_periodo,
            "by_workflow_origem": top_n(DIM_WORKFLOW_ORIGEM),
            "by_resultado_destino": top_n(DIM_RESULTADO_DESTINO),
            "by_status_destino": top_n(DIM_STATUS_DESTINO),
            "by_tipo_conclusao_origem": top_n(DIM_TIPO_CONCLUSAO_ORIGEM),
            "by_nh_origem": top_n(DIM_NH_ORIGEM),
            "by_cliente_origem": top_n(DIM_CLIENTE_ORIGEM),
            "by_matricula_origem": top_n(DIM_MATRICULA_ORIGEM),
            "by_alerta_destino": top_n(DIM_ALERTA_DESTINO),
        }
    )
    return base


def serialize_por_workflow(
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    snap, qs = _agg_qs(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
        dimension=DIM_WORKFLOW_ORIGEM,
    )
    rows = _rollup_dimension(qs) if snap else []
    total = sum(r["count"] for r in rows) or 0
    for r in rows:
        r["pct"] = round(100.0 * r["count"] / total, 2) if total else 0.0
    from apps.produtividade_case.services.mvp_metrics import enrich_workflow_tempo_stats

    rows = enrich_workflow_tempo_stats(
        rows,
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
    )
    base.update({"items": rows, "total": total})
    return base


def serialize_por_agente(
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    snap, qs = _agg_qs(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
        dimension=DIM_MATRICULA_DESTINO,
    )
    rows = _rollup_dimension(qs) if snap else []
    from apps.produtividade_case.services.mvp_metrics import enrich_agent_tempo_stats

    rows = enrich_agent_tempo_stats(
        rows,
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
    )
    enrich = _enrich_agentes([r["key"] for r in rows], reference_date=date_to)
    items = []
    for r in rows:
        meta = enrich.get(r["key"], {})
        items.append(
            {
                **r,
                "matricula": r["key"],
                "agent_name": meta.get("agent_name") or r["key"],
                "team": meta.get("team") or "",
                "leader_name": meta.get("leader_name") or "",
            }
        )
    total = sum(r["count"] for r in items) or 0
    base.update({"items": items, "total": total})
    return base


def _enrich_agentes(
    matriculas: list[str], *, reference_date: date | None = None
) -> dict[str, dict[str, str]]:
    from datetime import date as date_cls

    from apps.produtividade.services.enrichment import (
        _history_for_date,
        build_agent_maps,
        build_history_index,
    )

    if not matriculas:
        return {}
    agents_by_mat, names_by_mat = build_agent_maps()
    history_index = build_history_index()
    reference = reference_date or date_cls.today()
    out: dict[str, dict[str, str]] = {}
    for mat in matriculas:
        name = names_by_mat.get(mat) or ""
        agent = agents_by_mat.get(mat)
        team = ""
        leader = ""
        if agent is not None:
            histories = history_index.get(str(agent.id), [])
            history = _history_for_date(histories, reference)
            if history:
                team = history.team or ""
                if history.leader_id and history.leader:
                    leader = history.leader.full_name or ""
        out[mat] = {
            "agent_name": name or mat,
            "team": team,
            "leader_name": leader,
        }
    return out


def serialize_serie_diaria(
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    workflow_origem: str | None = None,
) -> dict[str, Any]:
    """Série diária dual: cadastrados (entrada Case) × concluídos (conclusão destino).

    Sem ``workflow_origem``: agregados ``volume_dia`` + ``volume_cadastro_dia``.
    Com filtro: agrega facts por workflow.
    """
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    periodo = periodo_mes or base.get("periodo_mes") or None
    wf = (workflow_origem or "").strip() or None

    if wf:
        points = _serie_diaria_from_facts(
            periodo_mes=periodo,
            date_from=date_from,
            date_to=date_to,
            workflow_origem=wf,
        )
    else:
        points = _serie_diaria_from_aggs(
            periodo_mes=periodo,
            date_from=date_from,
            date_to=date_to,
        )

    total_concluidos = sum(int(p.get("concluidos") or 0) for p in points)
    total_cadastrados = sum(int(p.get("cadastrados") or 0) for p in points)
    base.update(
        {
            "points": points,
            "workflow_origem": wf,
            "total_concluidos": total_concluidos,
            "total_cadastrados": total_cadastrados,
            "saldo": total_cadastrados - total_concluidos,
        }
    )
    return base


def _serie_diaria_from_aggs(
    *,
    periodo_mes: str | None,
    date_from: date | None,
    date_to: date | None,
) -> list[dict[str, Any]]:
    from django.db.models import Sum

    snap, qs_conc = _agg_qs(
        periodo_mes=periodo_mes,
        date_from=date_from,
        date_to=date_to,
        dimension=DIM_VOLUME_DIA,
    )
    if snap is None:
        return []

    _, qs_cad = _agg_qs(
        periodo_mes=periodo_mes,
        date_from=date_from,
        date_to=date_to,
        dimension=DIM_VOLUME_CADASTRO_DIA,
    )

    concluidos: dict[date, dict[str, int]] = {}
    for row in (
        qs_conc.values("day")
        .annotate(count=Sum("count"), analysis_seconds_sum=Sum("analysis_seconds_sum"))
        .order_by("day")
    ):
        day = row["day"]
        concluidos[day] = {
            "count": int(row["count"] or 0),
            "seconds": int(row["analysis_seconds_sum"] or 0),
        }

    cadastrados: dict[date, int] = {}
    for row in qs_cad.values("day").annotate(count=Sum("count")).order_by("day"):
        cadastrados[row["day"]] = int(row["count"] or 0)

    days = sorted(set(concluidos) | set(cadastrados))
    points: list[dict[str, Any]] = []
    for day in days:
        conc = concluidos.get(day) or {"count": 0, "seconds": 0}
        cad = cadastrados.get(day, 0)
        count = conc["count"]
        points.append(
            {
                "day": day.isoformat(),
                "count": count,
                "concluidos": count,
                "cadastrados": cad,
                "analysis_seconds_sum": conc["seconds"],
            }
        )
    return points


def _serie_diaria_from_facts(
    *,
    periodo_mes: str | None,
    date_from: date | None,
    date_to: date | None,
    workflow_origem: str,
) -> list[dict[str, Any]]:
    from django.db.models import Count, Sum

    snaps = selected_consolidado_snapshots(
        periodo_mes=periodo_mes, date_from=date_from, date_to=date_to
    )
    if not snaps:
        return []

    facts = CaseConsolidadoFact.objects.filter(snapshot__in=snaps)
    # Facts guardam workflow vazio como ""; aggs usam "(vazio)".
    wf_raw = (workflow_origem or "").strip()
    if wf_raw in ("", "(vazio)"):
        facts = facts.filter(workflow_origem="")
    else:
        facts = facts.filter(workflow_origem=wf_raw[:255])

    concluidos: dict[date, dict[str, int]] = {}
    conc_qs = facts.exclude(conclusao_destino_at__isnull=True)
    if date_from:
        conc_qs = conc_qs.filter(conclusao_destino_at__date__gte=date_from)
    if date_to:
        conc_qs = conc_qs.filter(conclusao_destino_at__date__lte=date_to)
    for row in (
        conc_qs.values("conclusao_destino_at__date")
        .annotate(
            count=Count("id"),
            analysis_seconds_sum=Sum("tempo_analise_segundos"),
        )
        .order_by("conclusao_destino_at__date")
    ):
        day = row["conclusao_destino_at__date"]
        if day is None:
            continue
        concluidos[day] = {
            "count": int(row["count"] or 0),
            "seconds": int(row["analysis_seconds_sum"] or 0),
        }

    cadastrados: dict[date, int] = {}
    cad_qs = facts.exclude(cadastro_destino_at__isnull=True)
    if date_from:
        cad_qs = cad_qs.filter(cadastro_destino_at__date__gte=date_from)
    if date_to:
        cad_qs = cad_qs.filter(cadastro_destino_at__date__lte=date_to)
    for row in (
        cad_qs.values("cadastro_destino_at__date")
        .annotate(count=Count("id"))
        .order_by("cadastro_destino_at__date")
    ):
        day = row["cadastro_destino_at__date"]
        if day is None:
            continue
        cadastrados[day] = int(row["count"] or 0)

    days = sorted(set(concluidos) | set(cadastrados))
    points: list[dict[str, Any]] = []
    for day in days:
        conc = concluidos.get(day) or {"count": 0, "seconds": 0}
        cad = cadastrados.get(day, 0)
        count = conc["count"]
        points.append(
            {
                "day": day.isoformat(),
                "count": count,
                "concluidos": count,
                "cadastrados": cad,
                "analysis_seconds_sum": conc["seconds"],
            }
        )
    return points


def serialize_cruzamento(
    *,
    periodo_mes: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    top_workflows: int = 10,
    top_resultados: int = 8,
) -> dict[str, Any]:
    base = serialize_analitica_status(periodo_mes=periodo_mes)
    snap, qs = _agg_qs(
        periodo_mes=periodo_mes or base.get("periodo_mes") or None,
        date_from=date_from,
        date_to=date_to,
        dimension=DIM_CRUZAMENTO_WF_RESULTADO,
    )
    cells_raw = _rollup_dimension(qs) if snap else []
    # Expand composite keys
    cells = []
    wf_totals: dict[str, int] = defaultdict(int)
    res_totals: dict[str, int] = defaultdict(int)
    for row in cells_raw:
        parts = str(row["key"]).split(CRUZAMENTO_KEY_SEP, 1)
        if len(parts) != 2:
            continue
        wf, res = parts
        cells.append(
            {
                "workflow_origem": wf,
                "resultado_destino": res,
                "count": row["count"],
                "analysis_seconds_sum": row["analysis_seconds_sum"],
                "tma_seconds": row["tma_seconds"],
            }
        )
        wf_totals[wf] += row["count"]
        res_totals[res] += row["count"]

    top_wf = {
        k
        for k, _ in sorted(wf_totals.items(), key=lambda x: -x[1])[: max(1, top_workflows)]
    }
    top_res = {
        k
        for k, _ in sorted(res_totals.items(), key=lambda x: -x[1])[: max(1, top_resultados)]
    }
    filtered = [
        c
        for c in cells
        if c["workflow_origem"] in top_wf and c["resultado_destino"] in top_res
    ]
    base.update(
        {
            "workflows": sorted(top_wf, key=lambda w: -wf_totals[w]),
            "resultados": sorted(top_res, key=lambda r: -res_totals[r]),
            "cells": filtered,
        }
    )
    return base
