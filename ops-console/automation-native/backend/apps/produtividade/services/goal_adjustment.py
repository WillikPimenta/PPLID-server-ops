# -*- coding: utf-8 -*-
"""Ajuste de stage_goal pelo Productivity Discount (headcount) e lookups de ociosidade."""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db.models import QuerySet
from django.db.models.functions import Lower

from apps.monitor_eventos.services.tabela_monitor import jornada_from_recorded_at

# Carga horária padrão (05:30) — denominador do desconto de produtividade.
META_CARGA_HORAS = Decimal("5.5")
META_JORNADA_SECONDS = 19800  # 05:30:00 — base padrão do abatimento por ociosidade


def jornada_base_seconds(logado_seconds: int | float | None = None) -> int:
    """Denominador do abatimento por ociosidade.

    Até 05:30 usa a carga padrão; com hora extra (logado > 05:30) usa o tempo logado.
    """
    try:
        logado = max(0, int(logado_seconds or 0))
    except (TypeError, ValueError):
        logado = 0
    if logado > META_JORNADA_SECONDS:
        return logado
    return META_JORNADA_SECONDS


def adjust_stage_goal(stage_goal: float, discount_hours: float | int | Decimal | None) -> float:
    """
    Etapa com ajuste = Etapa - ((DiscountProd / CargaHora) * Etapa)

    DiscountProd é decimal em horas (01:00 → 1.0). CargaHora = 5.5 (05:30).
    Ex.: 1414 - ((1.0 / 5.5) * 1414) = 1156.9091
    """
    if stage_goal is None:
        return 0.0
    goal = float(stage_goal)
    if goal <= 0:
        return goal
    try:
        discount = max(0.0, float(discount_hours or 0))
    except (TypeError, ValueError):
        discount = 0.0
    if discount <= 0:
        return goal
    carga = float(META_CARGA_HORAS)
    ratio = min(discount, carga) / carga
    return max(0.0, round(goal * (1.0 - ratio), 4))


def adjust_stage_goal_for_ociosidade(
    stage_goal: float,
    ociosidade_seconds: int | float | None,
    logado_seconds: int | float | None = None,
) -> float:
    """
    Meta final após PCD: meta_pcd − (meta_pcd × (ociosidade / base)).

    ociosidade_seconds = ociosidade líquida da jornada (logado − analisado).
    base = 05:30, ou tempo logado quando a jornada ultrapassa 05:30 (hora extra).
    """
    if stage_goal is None:
        return 0.0
    goal = float(stage_goal)
    if goal <= 0:
        return goal
    try:
        idle = max(0, int(ociosidade_seconds or 0))
    except (TypeError, ValueError):
        idle = 0
    if idle <= 0:
        return goal
    base = jornada_base_seconds(logado_seconds)
    ratio = min(idle, base) / float(base)
    return max(0.0, round(goal * (1.0 - ratio), 4))


def adjust_stage_goal_full(
    stage_goal: float,
    discount_hours: float | int | Decimal | None,
    ociosidade_seconds: int | float | None,
    *,
    apply_ociosidade: bool = True,
    logado_seconds: int | float | None = None,
) -> float:
    """Aplica PCD e, em seguida, ociosidade (base 5,5h ou logado se HE)."""
    after_pcd = adjust_stage_goal(stage_goal, discount_hours)
    if not apply_ociosidade:
        return after_pcd
    return adjust_stage_goal_for_ociosidade(
        after_pcd, ociosidade_seconds, logado_seconds=logado_seconds
    )


def ociosidade_for_goal_adjustment(
    lookup: dict[tuple[str, date], int],
    matricula: str,
    day: date,
) -> int:
    """Ociosidade líquida da jornada para ajuste de meta."""
    return ociosidade_for_agent_day(lookup, matricula, day)


def _monitor_daily_totals_for_qs(
    qs: QuerySet,
    field: str,
) -> dict[tuple[str, date], int]:
    """Mapa (matricula_lower, data_jornada) -> tempo_logado_dia ou tempo_ocioso_dia (monitor)."""
    from apps.produtividade.services.monitor_bridge import get_monitor_tabela_rows_for_qs

    tabela = get_monitor_tabela_rows_for_qs(qs)
    if not tabela:
        return {}

    lookup: dict[tuple[str, date], int] = {}
    null_rows = [row for row in tabela if row.get("hora") is None]
    source = null_rows if null_rows else tabela
    seen: set[tuple[str, str]] = set()

    for row in source:
        matricula = str(row.get("matricula_usuario") or "").strip().lower()
        data_jornada = row.get("data_jornada")
        if not matricula or not data_jornada:
            continue
        dedupe_key = (matricula, data_jornada)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        dia = date.fromisoformat(str(data_jornada)[:10])
        lookup[(matricula, dia)] = int(row.get(field) or 0)

    return lookup


def build_logado_lookup_for_qs(qs: QuerySet) -> dict[tuple[str, date], int]:
    """Mapa (matricula_lower, data_jornada) -> tempo_logado_dia em segundos."""
    return _monitor_daily_totals_for_qs(qs, "tempo_logado_dia")


def build_monitor_ociosidade_lookup_for_qs(qs: QuerySet) -> dict[tuple[str, date], int]:
    """Mapa (matricula_lower, data_jornada) -> tempo_ocioso_dia do monitor (hora a hora)."""
    return _monitor_daily_totals_for_qs(qs, "tempo_ocioso_dia")


def build_analyzed_daily_lookup_for_qs(qs: QuerySet) -> dict[tuple[str, date], int]:
    """Soma analysis_seconds por (matricula, data_jornada)."""
    lookup: dict[tuple[str, date], int] = defaultdict(int)
    for row in qs.values("matricula_norm", "recorded_at", "analysis_seconds"):
        recorded_at = row["recorded_at"]
        matricula = row["matricula_norm"]
        if recorded_at is None or not matricula:
            continue
        jornada = jornada_from_recorded_at(recorded_at)
        if jornada is None:
            continue
        lookup[(matricula.strip().lower(), jornada)] += int(row["analysis_seconds"] or 0)
    return dict(lookup)


def build_net_ociosidade_lookup_for_qs(qs: QuerySet) -> dict[tuple[str, date], int]:
    """Ociosidade líquida por jornada: max(0, tempo_logado_dia − analysis_seconds_dia)."""
    logado = build_logado_lookup_for_qs(qs)
    analyzed = build_analyzed_daily_lookup_for_qs(qs)
    keys = set(logado) | set(analyzed)
    lookup: dict[tuple[str, date], int] = {}
    for key in keys:
        net = int(logado.get(key, 0) or 0) - int(analyzed.get(key, 0) or 0)
        lookup[key] = max(0, net)
    return lookup


def build_ociosidade_lookup_for_qs(qs: QuerySet) -> dict[tuple[str, date], int]:
    """Mapa de ociosidade líquida por jornada (display / tempo ocioso)."""
    return build_net_ociosidade_lookup_for_qs(qs)


def ociosidade_for_agent_day(
    lookup: dict[tuple[str, date], int],
    matricula: str,
    day: date,
) -> int:
    if not matricula or not day:
        return 0
    return lookup.get((matricula.strip().lower(), day), 0)


def _history_covers_date(history, on_date: date) -> bool:
    if history.start_date > on_date:
        return False
    if history.final_date and history.final_date < on_date:
        return False
    return True


def build_productivity_discount_lookup_for_qs(qs: QuerySet) -> dict[tuple[str, date], float]:
    """
    Mapa (matricula_lower, data_jornada) -> Productivity Discount em horas decimais.

    Fonte: AgentHistory.productivity_discount (headcount). 01:00 → 1.0.
    """
    from apps.workforce.models import Agent, AgentHistory

    matriculas = {
        str(m).strip().lower()
        for m in qs.values_list("matricula_norm", flat=True).distinct()
        if m
    }
    if not matriculas:
        return {}

    days: set[date] = set()
    for recorded_at in qs.values_list("recorded_at", flat=True).distinct():
        if recorded_at is None:
            continue
        jornada = jornada_from_recorded_at(recorded_at)
        if jornada is not None:
            days.add(jornada)
    if not days:
        return {}

    agents = {
        (a.user_lan_id or "").strip().lower(): a
        for a in Agent.objects.annotate(lan_lower=Lower("user_lan_id"))
        .filter(lan_lower__in=matriculas)
        .only("id", "user_lan_id")
    }
    if not agents:
        return {}

    agent_ids = [a.id for a in agents.values()]
    histories_by_agent: dict[str, list] = defaultdict(list)
    for history in (
        AgentHistory.objects.filter(agent_id__in=agent_ids)
        .order_by("agent_id", "-start_date")
        .only("agent_id", "start_date", "final_date", "productivity_discount", "pcd")
    ):
        histories_by_agent[str(history.agent_id)].append(history)

    lan_by_agent_id = {str(a.id): lan for lan, a in agents.items()}
    lookup: dict[tuple[str, date], float] = {}

    for agent_id, histories in histories_by_agent.items():
        lan = lan_by_agent_id.get(agent_id)
        if not lan:
            continue
        for day in days:
            # Somente histórico vigente na data da jornada — sem fallback para outro ciclo.
            history = next((h for h in histories if _history_covers_date(h, day)), None)
            if history is None:
                continue
            # Desconto PcD só vale com condição PcD ativa no histórico vigente.
            if not bool(getattr(history, "pcd", False)):
                continue
            raw = history.productivity_discount
            if raw is None:
                continue
            try:
                hours = float(raw)
            except (TypeError, ValueError):
                continue
            if hours <= 0:
                continue
            lookup[(lan, day)] = hours

    return lookup


def discount_for_agent_day(
    lookup: dict[tuple[str, date], float],
    matricula: str,
    day: date,
) -> float:
    if not matricula or not day:
        return 0.0
    return float(lookup.get((matricula.strip().lower(), day), 0.0) or 0.0)
