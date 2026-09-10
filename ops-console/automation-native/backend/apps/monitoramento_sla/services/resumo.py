# -*- coding: utf-8 -*-
"""Agregações do painel Resumo (layout analítico / drilldown temporal)."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Any, Literal

from django.db.models import Case, F, IntegerField, Sum, Value, When
from django.db.models.functions import Coalesce, Extract, ExtractHour, TruncDay, TruncMonth, TruncWeek, TruncYear
from django.utils import timezone

from apps.controle_sla.services.sla_eval import expand_dias_token
from apps.dimensoes_processos.models import DimWorkflow, ProjecaoSla
from apps.monitoramento_sla.models import SlaUtilConsolidado
from apps.monitoramento_sla.services.projecao_lookup import iter_dates, models_q_open_or_after

META_SLA_DEFAULT = 98.5
Granularity = Literal["auto", "year", "quarter", "month", "week", "day", "hour"]


def _pct(part: float, total: float, digits: int = 1) -> float | None:
    if not total:
        return None
    return round(100.0 * part / total, digits)


def _pp_diff(a: float | None, b: float | None, digits: int = 1) -> float | None:
    if a is None or b is None:
        return None
    return round(a - b, digits)


def _parse_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_optional_float(value: Any, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _filter_consolidado(
    qs,
    *,
    start: date,
    end: date,
    id_cliente: int | None = None,
    id_workflow: int | None = None,
    id_nh: int | None = None,
    tipo_conclusao: str | None = None,
    sla_natural: str | None = None,
    hour: int | None = None,
    dow: int | None = None,
):
    qs = qs.filter(data_cadastro__gte=start, data_cadastro__lte=end)
    if id_cliente is not None:
        qs = qs.filter(id_cliente=id_cliente)
    if id_workflow is not None:
        qs = qs.filter(id_workflow=id_workflow)
    if id_nh is not None:
        qs = qs.filter(id_nh=id_nh)
    if tipo_conclusao is not None:
        qs = qs.filter(tipo_conclusao=tipo_conclusao)
    if sla_natural in {"Dentro", "Fora"}:
        qs = qs.filter(sla_descricao_natural=sla_natural)
    if hour is not None:
        qs = qs.annotate(_h=ExtractHour("hora_cadastro")).filter(_h=hour)
    if dow is not None:
        qs = qs.annotate(_dow=Extract("data_cadastro", "dow")).filter(_dow=dow)
    return qs


def volume_esperado(
    *,
    start: date,
    end: date,
    id_cliente: int | None = None,
    id_workflow: int | None = None,
    id_nh: int | None = None,
) -> float:
    from apps.monitoramento_sla.services.projecao_lookup import _weekday_megazord

    qs = ProjecaoSla.objects.filter(data_inicio__lte=end).filter(models_q_open_or_after(start))
    if id_cliente is not None:
        qs = qs.filter(cliente_id=id_cliente)
    if id_workflow is not None:
        qs = qs.filter(workflow_id=id_workflow)
    if id_nh is not None:
        qs = qs.filter(nivel_hierarquico_id=id_nh)

    dates_by_wd: dict[int, list[date]] = {i: [] for i in range(7)}
    for d in iter_dates(start, end):
        dates_by_wd[_weekday_megazord(d)].append(d)

    total = 0.0
    for row in qs.iterator(chunk_size=500):
        vol = float(row.volume) if row.volume is not None else 0.0
        if vol <= 0:
            continue
        days = expand_dias_token(row.dias_semana)
        if not days:
            continue
        for wd in days:
            for d in dates_by_wd.get(wd, ()):
                if row.data_inicio > d:
                    continue
                if row.data_fim is not None and row.data_fim < d:
                    continue
                total += vol
    return total


def _agg_kpis(qs) -> dict[str, Any]:
    agg = qs.aggregate(
        total=Coalesce(Sum("quantidade"), Value(0)),
        dentro=Coalesce(
            Sum(
                Case(
                    When(sla_descricao_natural="Dentro", then=F("quantidade")),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ),
            Value(0),
        ),
        fora=Coalesce(
            Sum(
                Case(
                    When(sla_descricao_natural="Fora", then=F("quantidade")),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ),
            Value(0),
        ),
        aj_dentro=Coalesce(
            Sum(
                Case(
                    When(sla_descricao_ajustado="Dentro", then=F("quantidade")),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ),
            Value(0),
        ),
        aj_fora=Coalesce(
            Sum(
                Case(
                    When(sla_descricao_ajustado="Fora", then=F("quantidade")),
                    default=Value(0),
                    output_field=IntegerField(),
                )
            ),
            Value(0),
        ),
    )
    total = int(agg["total"] or 0)
    dentro = int(agg["dentro"] or 0)
    fora = int(agg["fora"] or 0)
    aj_dentro = int(agg["aj_dentro"] or 0)
    aj_fora = int(agg["aj_fora"] or 0)
    return {
        "dentro": dentro,
        "fora": fora,
        "total": total,
        "sla_pct": _pct(dentro, total, digits=1),
        "sla_ajustado_pct": _pct(aj_dentro, total, digits=1),
        "fora_pct": _pct(fora, total, digits=1),
        "fora_ajustado_pct": _pct(aj_fora, total, digits=1),
    }


def resolve_granularity(start: date, end: date, requested: str | None) -> str:
    raw = (requested or "auto").lower()
    if raw in {"year", "quarter", "month", "week", "day", "hour"}:
        return raw
    days = (end - start).days + 1
    if days > 700:
        return "year"
    if days > 120:
        return "month"
    if days > 45:
        return "week"
    if days > 7:
        return "day"
    return "hour"


def _next_granularity(g: str) -> str | None:
    order = ["year", "quarter", "month", "week", "day", "hour"]
    try:
        i = order.index(g)
    except ValueError:
        return None
    return order[i + 1] if i + 1 < len(order) else None


def _fiscal_quarter_bounds_from_date(d: date) -> tuple[date, date, str, str]:
    """Trimestre fiscal com início em abril (Q1=Abr–Jun … Q4=Jan–Mar).

    O ano do rótulo é o ano civil do abril que inicia o FY
    (ex.: jan/2027 → Q4 2026; jul/2026 → Q2 2026).
    """
    y, m = d.year, d.month
    fy_start_year = y if m >= 4 else y - 1
    offset = (m - 4) if m >= 4 else (m + 8)
    q = offset // 3 + 1
    if q == 4:
        start = date(fy_start_year + 1, 1, 1)
        end = date(fy_start_year + 1, 3, 31)
    else:
        start_month = (q - 1) * 3 + 4  # 4, 7, 10
        start = date(fy_start_year, start_month, 1)
        end_m = start_month + 2
        end = date(fy_start_year, end_m, monthrange(fy_start_year, end_m)[1])
    key = f"{fy_start_year}-FQ{q}"
    label = f"Q{q} {fy_start_year}"
    return start, end, key, label


def _series_bucket_bounds(g: str, bucket) -> tuple[date, date, str, str]:
    """Retorna (start, end, key, label)."""
    if g == "year":
        y = bucket.year if hasattr(bucket, "year") else int(bucket)
        return date(y, 1, 1), date(y, 12, 31), str(y), str(y)
    if g == "quarter":
        d = bucket.date() if isinstance(bucket, datetime) else bucket
        if not isinstance(d, date):
            d = date(d.year, d.month, 1)
        return _fiscal_quarter_bounds_from_date(d)
    if g == "month":
        y, m = bucket.year, bucket.month
        start = date(y, m, 1)
        end = date(y, m, monthrange(y, m)[1])
        label = start.strftime("%b/%Y")
        return start, end, start.strftime("%Y-%m"), label
    if g == "week":
        # TruncWeek Monday-start in Postgres with TruncWeek(week_start=1) — Django uses Monday by default on PG
        start = bucket.date() if isinstance(bucket, datetime) else bucket
        end = start + timedelta(days=6)
        iso = start.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        label = f"Sem {iso.week} | {start.day}–{end.day} {start.strftime('%b').lower()} {start.year}"
        return start, end, key, label
    if g == "day":
        d = bucket.date() if isinstance(bucket, datetime) else bucket
        return d, d, d.isoformat(), d.strftime("%d/%m/%Y")
    # hour — bucket is (date, hour) handled separately
    raise ValueError(g)


def _build_serie(qs, granularity: str) -> list[dict[str, Any]]:
    trunc_map = {
        "year": TruncYear("data_cadastro"),
        # Trimestre fiscal (abr→mar): agrega por mês e consolida em Python.
        "month": TruncMonth("data_cadastro"),
        "week": TruncWeek("data_cadastro"),
        "day": TruncDay("data_cadastro"),
    }
    if granularity == "hour":
        rows = (
            qs.annotate(hour=ExtractHour("hora_cadastro"))
            .values("data_cadastro", "hour")
            .annotate(
                recebidos=Coalesce(Sum("quantidade"), Value(0)),
                excedidos=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_natural="Fora", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
                dentro_m=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_natural="Dentro", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
                aj_dentro=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_ajustado="Dentro", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
            )
            .order_by("data_cadastro", "hour")
        )
        out = []
        for r in rows:
            d = r["data_cadastro"]
            h = int(r["hour"] or 0)
            rec = int(r["recebidos"] or 0)
            din = int(r["dentro_m"] or 0)
            aj = int(r["aj_dentro"] or 0)
            key = f"{d.isoformat()}T{h:02d}"
            out.append(
                {
                    "key": key,
                    "label": f"{d.strftime('%d/%m')} {h:02d}h",
                    "start_date": d.isoformat(),
                    "end_date": d.isoformat(),
                    "hour": h,
                    "recebidos": rec,
                    "excedidos": int(r["excedidos"] or 0),
                    "sla_pct": _pct(din, rec, digits=1),
                    "sla_ajustado_pct": _pct(aj, rec, digits=1),
                    "drill_to": None,
                }
            )
        return out

    # Trimestre fiscal: agrega por mês e consolida no FY (abr→mar).
    trunc_key = "month" if granularity == "quarter" else granularity
    trunc = trunc_map[trunc_key]
    rows = (
        qs.annotate(bucket=trunc)
        .values("bucket")
        .annotate(
            recebidos=Coalesce(Sum("quantidade"), Value(0)),
            excedidos=Coalesce(
                Sum(
                    Case(
                        When(sla_descricao_natural="Fora", then=F("quantidade")),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                Value(0),
            ),
            dentro_m=Coalesce(
                Sum(
                    Case(
                        When(sla_descricao_natural="Dentro", then=F("quantidade")),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                Value(0),
            ),
            aj_dentro=Coalesce(
                Sum(
                    Case(
                        When(sla_descricao_ajustado="Dentro", then=F("quantidade")),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                Value(0),
            ),
        )
        .order_by("bucket")
    )
    nxt = _next_granularity(granularity)
    out: list[dict[str, Any]] = []
    if granularity == "quarter":
        merged: dict[str, dict[str, Any]] = {}
        for r in rows:
            b = r["bucket"]
            if b is None:
                continue
            d = b.date() if isinstance(b, datetime) else b
            b_start, b_end, key, label = _fiscal_quarter_bounds_from_date(d)
            slot = merged.get(key)
            if slot is None:
                slot = {
                    "key": key,
                    "label": label,
                    "start_date": b_start.isoformat(),
                    "end_date": b_end.isoformat(),
                    "recebidos": 0,
                    "excedidos": 0,
                    "dentro_m": 0,
                    "aj_dentro": 0,
                    "drill_to": nxt,
                }
                merged[key] = slot
            slot["recebidos"] += int(r["recebidos"] or 0)
            slot["excedidos"] += int(r["excedidos"] or 0)
            slot["dentro_m"] += int(r["dentro_m"] or 0)
            slot["aj_dentro"] += int(r["aj_dentro"] or 0)
        for key in sorted(merged.keys()):
            slot = merged[key]
            rec = slot["recebidos"]
            din = slot.pop("dentro_m")
            aj = slot.pop("aj_dentro")
            slot["sla_pct"] = _pct(din, rec, digits=1)
            slot["sla_ajustado_pct"] = _pct(aj, rec, digits=1)
            out.append(slot)
        return out

    for r in rows:
        b = r["bucket"]
        if b is None:
            continue
        b_start, b_end, key, label = _series_bucket_bounds(granularity, b)
        rec = int(r["recebidos"] or 0)
        din = int(r["dentro_m"] or 0)
        aj = int(r["aj_dentro"] or 0)
        out.append(
            {
                "key": key,
                "label": label,
                "start_date": b_start.isoformat(),
                "end_date": b_end.isoformat(),
                "recebidos": rec,
                "excedidos": int(r["excedidos"] or 0),
                "sla_pct": _pct(din, rec, digits=1),
                "sla_ajustado_pct": _pct(aj, rec, digits=1),
                "drill_to": nxt,
            }
        )
    return out


def build_resumo(
    *,
    start: date,
    end: date,
    id_cliente: int | None = None,
    id_workflow: int | None = None,
    id_nh: int | None = None,
    calendar_month: int | None = None,
    calendar_year: int | None = None,
    tipo_conclusao: str | None = None,
    sla_natural: str | None = None,
    hour: int | None = None,
    dow: int | None = None,
    granularity: str | None = None,
    meta_sla: float = META_SLA_DEFAULT,
    include_workflows_detail: bool = True,
) -> dict[str, Any]:
    filter_kw = dict(
        start=start,
        end=end,
        id_cliente=id_cliente,
        id_workflow=id_workflow,
        id_nh=id_nh,
        tipo_conclusao=tipo_conclusao,
        sla_natural=sla_natural,
        hour=hour,
        dow=dow,
    )
    base = _filter_consolidado(SlaUtilConsolidado.objects.all(), **filter_kw)
    k = _agg_kpis(base)
    total = k["total"]
    dentro = k["dentro"]
    fora = k["fora"]

    # tipo conclusão (sem filtro de tipo, para manter opções de filtro cruzado)
    tipo_base = _filter_consolidado(
        SlaUtilConsolidado.objects.all(),
        start=start,
        end=end,
        id_cliente=id_cliente,
        id_workflow=id_workflow,
        id_nh=id_nh,
        sla_natural=sla_natural,
        hour=hour,
        dow=dow,
    )
    tipo_total = int(tipo_base.aggregate(v=Coalesce(Sum("quantidade"), Value(0)))["v"] or 0)
    tipo_conclusao_list = []
    for r in (
        tipo_base.values("tipo_conclusao")
        .annotate(quantidade=Coalesce(Sum("quantidade"), Value(0)))
        .order_by("-quantidade")
    ):
        label = (r["tipo_conclusao"] or "").strip() or "(vazio)"
        q = int(r["quantidade"] or 0)
        tipo_conclusao_list.append({"label": label, "quantidade": q, "pct": _pct(q, tipo_total, digits=1)})

    esperado = volume_esperado(
        start=start, end=end, id_cliente=id_cliente, id_workflow=id_workflow, id_nh=id_nh
    )
    esperado_i = int(round(esperado))
    volume_diff_abs = total - esperado_i
    volume_delta_pct = round(100.0 * volume_diff_abs / esperado, 1) if esperado > 0 else None
    volume_atingimento_pct = round(100.0 * total / esperado, 1) if esperado > 0 else None

    impact_ids = list(
        base.filter(sla_descricao_natural="Fora")
        .exclude(id_workflow__isnull=True)
        .values_list("id_workflow", flat=True)
        .distinct()
    )
    atendimento_ids = set(
        base.exclude(id_workflow__isnull=True).values_list("id_workflow", flat=True).distinct()
    )
    # Impactados ⊆ atendimento (evita % > 100 por divergência de agregação)
    impact_ids = [i for i in impact_ids if i in atendimento_ids]
    workflows_atendimento = len(atendimento_ids)
    workflows_impactados = len(impact_ids)
    workflows_impactados_pct = _pct(workflows_impactados, workflows_atendimento, digits=1)

    workflows_detail: list[dict[str, Any]] = []
    if include_workflows_detail and impact_ids:
        nomes = dict(DimWorkflow.objects.filter(pk__in=impact_ids).values_list("pk", "nome"))
        wf_rows = (
            base.filter(id_workflow__in=impact_ids)
            .values("id_workflow", "id_cliente")
            .annotate(
                volume=Coalesce(Sum("quantidade"), Value(0)),
                fora_q=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_natural="Fora", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
                dentro_q=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_natural="Dentro", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
                aj_d=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_ajustado="Dentro", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
            )
            .order_by("-fora_q")[:50]
        )
        for r in wf_rows:
            vol = int(r["volume"] or 0)
            fq = int(r["fora_q"] or 0)
            workflows_detail.append(
                {
                    "id_workflow": r["id_workflow"],
                    "workflow_nome": nomes.get(r["id_workflow"] or -1, f"#{r['id_workflow']}"),
                    "id_cliente": r["id_cliente"],
                    "volume": vol,
                    "fora": fq,
                    "sla_pct": _pct(int(r["dentro_q"] or 0), vol, digits=1),
                    "sla_ajustado_pct": _pct(int(r["aj_d"] or 0), vol, digits=1),
                    "participacao_desvio_pct": _pct(fq, fora, digits=1) if fora else None,
                }
            )

    # período anterior equivalente
    span = (end - start).days + 1
    prev_end = start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span - 1)
    prev_base = _filter_consolidado(
        SlaUtilConsolidado.objects.all(),
        start=prev_start,
        end=prev_end,
        id_cliente=id_cliente,
        id_workflow=id_workflow,
        id_nh=id_nh,
        tipo_conclusao=tipo_conclusao,
        sla_natural=sla_natural,
        hour=hour,
        dow=dow,
    )
    prev_k = _agg_kpis(prev_base)

    sla_pct = k["sla_pct"]
    sla_aj = k["sla_ajustado_pct"]
    meta = float(meta_sla)
    kpis = {
        **k,
        "meta_sla": meta,
        "sla_vs_meta_pp": _pp_diff(sla_pct, meta),
        "sla_ajustado_vs_meta_pp": _pp_diff(sla_aj, meta),
        "sla_vs_prev_pp": _pp_diff(sla_pct, prev_k["sla_pct"]),
        "sla_ajustado_vs_natural_pp": _pp_diff(sla_aj, sla_pct),
        "tipo_conclusao": tipo_conclusao_list,
        "volume": total,
        "volume_esperado": esperado_i,
        "volume_diff_abs": volume_diff_abs,
        "volume_delta_pct": volume_delta_pct,
        "volume_atingimento_pct": volume_atingimento_pct,
        "workflows_atendimento": workflows_atendimento,
        "workflows_impactados": workflows_impactados,
        "workflows_impactados_pct": workflows_impactados_pct,
        "as_of": timezone.now().isoformat(),
        "periodo_anterior": {
            "start_date": prev_start.isoformat(),
            "end_date": prev_end.isoformat(),
            "sla_pct": prev_k["sla_pct"],
            "sla_ajustado_pct": prev_k["sla_ajustado_pct"],
            "total": prev_k["total"],
        },
    }

    # --- calendário ---
    cal_year = calendar_year or start.year
    cal_month = calendar_month or start.month
    if cal_month < 1 or cal_month > 12:
        cal_month = start.month
    month_start = date(cal_year, cal_month, 1)
    month_end = date(cal_year, cal_month, monthrange(cal_year, cal_month)[1])
    # lookback 7 days for WoW trend
    wow_start = month_start - timedelta(days=7)
    daily_qs = _filter_consolidado(
        SlaUtilConsolidado.objects.all(),
        start=wow_start,
        end=month_end,
        id_cliente=id_cliente,
        id_workflow=id_workflow,
        id_nh=id_nh,
        tipo_conclusao=tipo_conclusao,
        sla_natural=sla_natural,
        hour=hour,
        dow=dow,
    )
    daily = (
        daily_qs.values("data_cadastro")
        .annotate(
            volume=Coalesce(Sum("quantidade"), Value(0)),
            dentro=Coalesce(
                Sum(
                    Case(
                        When(sla_descricao_natural="Dentro", then=F("quantidade")),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                Value(0),
            ),
            fora_d=Coalesce(
                Sum(
                    Case(
                        When(sla_descricao_natural="Fora", then=F("quantidade")),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                Value(0),
            ),
            aj_d=Coalesce(
                Sum(
                    Case(
                        When(sla_descricao_ajustado="Dentro", then=F("quantidade")),
                        default=Value(0),
                        output_field=IntegerField(),
                    )
                ),
                Value(0),
            ),
        )
        .order_by("data_cadastro")
    )
    by_day: dict[date, dict[str, Any]] = {}
    for r in daily:
        d = r["data_cadastro"]
        vol = int(r["volume"] or 0)
        din = int(r["dentro"] or 0)
        by_day[d] = {
            "volume": vol,
            "dentro": din,
            "fora": int(r["fora_d"] or 0),
            "sla_pct": _pct(din, vol, digits=1),
            "sla_ajustado_pct": _pct(int(r["aj_d"] or 0), vol, digits=1),
        }

    today = timezone.localdate()
    calendario: list[dict[str, Any]] = []
    cur = month_start
    while cur <= month_end:
        info = by_day.get(cur)
        sla_pct_d = info["sla_pct"] if info else None
        prev_week = by_day.get(cur - timedelta(days=7))
        prev_pct = prev_week["sla_pct"] if prev_week else None
        tendencia = "flat"
        if sla_pct_d is not None and prev_pct is not None:
            if sla_pct_d > prev_pct:
                tendencia = "up"
            elif sla_pct_d < prev_pct:
                tendencia = "down"
        if cur > today:
            estado = "futuro"
        elif info is None or info["volume"] == 0:
            estado = "sem_movimento"
        else:
            estado = "com_dados"
        in_range = start <= cur <= end
        calendario.append(
            {
                "date": cur.isoformat(),
                "sla_pct": sla_pct_d if in_range else None,
                "sla_ajustado_pct": info["sla_ajustado_pct"] if info and in_range else None,
                "volume": info["volume"] if info and in_range else 0,
                "dentro": info["dentro"] if info and in_range else 0,
                "fora": info["fora"] if info and in_range else 0,
                "tendencia": tendencia if in_range else "flat",
                "tendencia_base": "mesmo_dia_semana_anterior",
                "estado": estado,
                "in_range": in_range,
                "vs_meta": _pp_diff(sla_pct_d, meta) if in_range else None,
            }
        )
        cur += timedelta(days=1)

    gran = resolve_granularity(start, end, granularity)
    serie = _build_serie(base, gran)

    def _heat_map(qs) -> dict[tuple[int, int], dict[str, Any]]:
        rows = (
            qs.exclude(hora_cadastro__isnull=True)
            .annotate(dow_v=Extract("data_cadastro", "dow"), hour_v=ExtractHour("hora_cadastro"))
            .values("dow_v", "hour_v")
            .annotate(
                volume=Coalesce(Sum("quantidade"), Value(0)),
                dentro_h=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_natural="Dentro", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
                fora_h=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_natural="Fora", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
                aj_h=Coalesce(
                    Sum(
                        Case(
                            When(sla_descricao_ajustado="Dentro", then=F("quantidade")),
                            default=Value(0),
                            output_field=IntegerField(),
                        )
                    ),
                    Value(0),
                ),
            )
        )
        out: dict[tuple[int, int], dict[str, Any]] = {}
        for r in rows:
            if r["dow_v"] is None or r["hour_v"] is None:
                continue
            vol = int(r["volume"] or 0)
            din = int(r["dentro_h"] or 0)
            key = (int(r["dow_v"]), int(r["hour_v"]))
            out[key] = {
                "volume": vol,
                "dentro": din,
                "fora": int(r["fora_h"] or 0),
                "sla_pct": _pct(din, vol, digits=1) if vol else None,
                "sla_ajustado_pct": _pct(int(r["aj_h"] or 0), vol, digits=1) if vol else None,
            }
        return out

    heat_cur = _heat_map(base)
    prev_heat_start = start - timedelta(days=7)
    prev_heat_end = end - timedelta(days=7)
    prev_heat_base = _filter_consolidado(
        SlaUtilConsolidado.objects.all(),
        start=prev_heat_start,
        end=prev_heat_end,
        id_cliente=id_cliente,
        id_workflow=id_workflow,
        id_nh=id_nh,
        tipo_conclusao=tipo_conclusao,
        sla_natural=sla_natural,
        hour=hour,
        dow=dow,
    )
    heat_prev = _heat_map(prev_heat_base)
    heat_total_vol = sum(v["volume"] for v in heat_cur.values()) or 0

    heatmap = []
    for (d_i, h_i), info in sorted(heat_cur.items()):
        vol = info["volume"]
        sla = info["sla_pct"]
        prev = heat_prev.get((d_i, h_i))
        prev_sla = prev["sla_pct"] if prev else None
        heatmap.append(
            {
                "dow": d_i,
                "hour": h_i,
                "volume": vol,
                "dentro": info["dentro"],
                "fora": info["fora"],
                "sla_pct": sla,
                "sla_ajustado_pct": info["sla_ajustado_pct"],
                "vs_meta_pp": _pp_diff(sla, meta) if sla is not None else None,
                "participacao_volume_pct": _pct(vol, heat_total_vol, digits=1) if vol else None,
                "sla_pct_semana_anterior": prev_sla,
                "vs_semana_pp": _pp_diff(sla, prev_sla),
            }
        )

    return {
        "ok": True,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "granularity": gran,
        "granularity_requested": granularity or "auto",
        "meta_sla": meta,
        "kpis": kpis,
        "workflows_detail": workflows_detail,
        "calendario": calendario,
        "calendario_mes": cal_month,
        "calendario_ano": cal_year,
        "serie_temporal": serie,
        "serie_mensal": serie,  # compat
        "heatmap": heatmap,
    }


def build_resumo_from_params(params: dict) -> dict[str, Any]:
    from apps.monitoramento_sla.services.resumo_serve import resolve_date_range

    start, end, err = resolve_date_range(params)
    if err or start is None or end is None:
        raise ValueError(err or "Período inválido.")
    tipo_raw = params.get("tipo_conclusao")
    if tipo_raw == "(vazio)":
        tipo_filtro: str | None = ""
    elif tipo_raw:
        tipo_filtro = str(tipo_raw).strip()
    else:
        tipo_filtro = None
    return build_resumo(
        start=start,
        end=end,
        id_cliente=_parse_optional_int(params.get("id_cliente")),
        id_workflow=_parse_optional_int(params.get("id_workflow")),
        id_nh=_parse_optional_int(params.get("id_nh")),
        calendar_month=_parse_optional_int(params.get("month")),
        calendar_year=_parse_optional_int(params.get("calendar_year"))
        or _parse_optional_int(params.get("year")),
        tipo_conclusao=tipo_filtro,
        sla_natural=(params.get("sla_natural") or None),
        hour=_parse_optional_int(params.get("hour")),
        dow=_parse_optional_int(params.get("dow")),
        granularity=(params.get("granularity") or "auto"),
        meta_sla=_parse_optional_float(params.get("meta_sla"), META_SLA_DEFAULT) or META_SLA_DEFAULT,
    )
