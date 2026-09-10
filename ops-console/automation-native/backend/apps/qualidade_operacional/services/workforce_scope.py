# -*- coding: utf-8 -*-
"""Escopo temporal de colaboradores para a visão operacional de qualidade."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any

from django.db.models import Q, QuerySet

from apps.qualidade_operacional.services.performance_cache import (
    get_workforce_cached,
    set_workforce_cached,
)
from apps.workforce.models import AgentHistory
from apps.workforce.services.journey_shift import resolve_journey_shift


TRUE_VALUES = {"1", "true", "yes", "sim", "on"}

_ONBOARDING_WINDOWS = {
    ("onboarding", "brflow"): 46,
    ("onboarding", "confer"): 38,
    ("reboarding", "brflow"): 38,
    ("reboarding", "confer"): 35,
}
_UPGRADE_TRAINING_DAYS = {"brflow": 5, "confer": 2}
_UPGRADE_FOLLOW_UP_DAYS = 30


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _param_list(params, key: str) -> list[str]:
    if hasattr(params, "getlist"):
        values = params.getlist(key)
    elif hasattr(params, "get"):
        raw = params.get(key)
        values = raw if isinstance(raw, (list, tuple, set)) else [raw]
    else:
        values = []
    out: list[str] = []
    for raw in values:
        for value in str(raw or "").split(","):
            value = value.strip()
            if value:
                out.append(value)
    return out


def workforce_scope_enabled(params) -> bool:
    raw = str(params.get("workforce_only") or "").strip().lower() if hasattr(params, "get") else ""
    return raw in TRUE_VALUES or bool(_param_list(params, "lider"))


def _normalized(value: Any) -> str:
    """Texto comparável sem depender de caixa ou acentuação."""
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in text if not unicodedata.combining(char)).casefold()


def _operation_for_history(history: AgentHistory) -> str | None:
    """Converte a operação registrada no HC para a regra de treinamento."""
    team = _normalized(history.team)
    text = _normalized(
        " ".join((history.team or "", history.team_sector or "", history.job_activity or ""))
    )
    if "operacional/fraud" in team or "brflow" in text:
        return "brflow"
    if "operacional/compliance" in team or "confer" in text:
        return "confer"
    return None


def _movement_kind(history: AgentHistory) -> str | None:
    activity = _normalized(history.job_activity)
    if "reintegr" in activity:
        return "reboarding"
    if "integr" in activity:
        return "onboarding"
    return None


def _history_changed(current: AgentHistory, previous: AgentHistory) -> bool:
    return (
        _normalized(current.team_sector) != _normalized(previous.team_sector)
        or _normalized(current.job_activity) != _normalized(previous.job_activity)
    )


@lru_cache(maxsize=1)
def build_responsibility_index() -> dict[str, list[dict[str, Any]]]:
    """Janelas temporais em que o facilitador responde pelo indicador."""
    histories = (
        AgentHistory.objects.select_related("agent", "facilitator")
        .exclude(agent__user_lan_id="")
        .order_by("agent_id", "start_date", "id")
    )
    by_agent: dict[str, list[AgentHistory]] = {}
    for history in histories:
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if matricula:
            by_agent.setdefault(matricula, []).append(history)

    index: dict[str, list[dict[str, Any]]] = {}
    for matricula, agent_histories in by_agent.items():
        windows: list[dict[str, Any]] = []
        for position, history in enumerate(agent_histories):
            if not history.facilitator:
                continue
            operation = _operation_for_history(history)
            movement = _movement_kind(history)
            if movement and operation:
                duration = _ONBOARDING_WINDOWS.get((movement, operation))
                if duration:
                    windows.append({
                        "start": history.start_date,
                        "end": history.start_date + timedelta(days=duration - 1),
                        "event_date": history.start_date,
                        "facilitator": history.facilitator,
                        "kind": movement,
                    })
                continue
            previous = agent_histories[position - 1] if position else None
            if (
                previous
                and operation
                and not _movement_kind(previous)
                and _history_changed(history, previous)
            ):
                windows.append({
                    "start": history.start_date - timedelta(days=_UPGRADE_TRAINING_DAYS[operation]),
                    "end": history.start_date + timedelta(days=_UPGRADE_FOLLOW_UP_DAYS - 1),
                    "event_date": history.start_date,
                    "facilitator": history.facilitator,
                    "kind": "upgrade",
                })
        if windows:
            index[matricula] = windows
    return index


def clear_responsibility_index_cache() -> None:
    """Invalida o índice quando AgentHistory ou a versão de Qualidade muda."""
    build_responsibility_index.cache_clear()


def responsibility_for_date(
    responsibility_index: dict[str, list[dict[str, Any]]],
    matricula: str,
    on_date: date | None,
    fallback: dict[str, str],
) -> dict[str, str]:
    """Facilitador prevalece dentro de sua janela; evento mais recente vence."""
    if not on_date:
        return fallback
    matches = [
        window for window in responsibility_index.get((matricula or "").strip().lower(), [])
        if window["start"] <= on_date <= window["end"]
    ]
    if not matches:
        return fallback
    window = max(matches, key=lambda item: item["event_date"])
    facilitator = window["facilitator"]
    return {
        **fallback,
        "lider": (facilitator.full_name or "Sem facilitador").strip(),
        "lider_matricula": (facilitator.user_lan_id or "").strip().lower(),
        "responsabilidade": "facilitador",
        "regra_responsabilidade": window["kind"],
    }

def responsibility_for_period(
    responsibility_index: dict[str, list[dict[str, Any]]],
    matricula: str,
    start: date | None,
    end: date | None,
    fallback: dict[str, str],
) -> dict[str, Any]:
    """Responsável quando a janela do facilitador se sobrepõe ao filtro."""
    period_start = start or end
    period_end = end or start
    if not period_start or not period_end:
        return fallback
    matches = overlapping_responsibility_windows(
        responsibility_index, matricula, period_start, period_end
    )
    if not matches:
        return fallback
    # Precedência: evento mais recente; evita dupla contagem no fallback single-row.
    window = max(matches, key=lambda item: item["event_date"])
    facilitator = window["facilitator"]
    fac_mat = (facilitator.user_lan_id or "").strip().lower()
    periods = serialize_responsibility_periods(
        [
            w
            for w in matches
            if (w["facilitator"].user_lan_id or "").strip().lower() == fac_mat
        ]
    )
    return {
        **fallback,
        "lider": (facilitator.full_name or "Sem facilitador").strip(),
        "lider_matricula": fac_mat,
        "facilitador_matricula": fac_mat,
        "responsabilidade": "facilitador",
        "regra_responsabilidade": window["kind"],
        "responsibility_periods": periods,
    }


def overlapping_responsibility_windows(
    responsibility_index: dict[str, list[dict[str, Any]]],
    matricula: str,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    return [
        window
        for window in responsibility_index.get((matricula or "").strip().lower(), [])
        if window["start"] <= period_end and window["end"] >= period_start
    ]


def serialize_responsibility_periods(windows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Janelas efetivas do contrato API, ordenadas e sem duplicidade exata."""
    seen: set[tuple[str, str, str]] = set()
    periods: list[dict[str, str]] = []
    for window in sorted(windows, key=lambda item: (item["start"], item["end"], item["kind"])):
        kind = str(window.get("kind") or "").strip().lower()
        start = window["start"].isoformat()
        end = window["end"].isoformat()
        key = (kind, start, end)
        if key in seen:
            continue
        seen.add(key)
        periods.append({"kind": kind, "start_date": start, "end_date": end})
    return periods


def facilitator_window_assignment_rows(
    responsibility_index: dict[str, list[dict[str, Any]]],
    matricula: str,
    start: date | None,
    end: date | None,
    fallback: dict[str, str],
) -> list[dict[str, Any]]:
    """Uma linha por (agente, facilitador, janela temporal) — espelha leader_assignment_rows."""
    period_start = start or end
    period_end = end or start
    if not period_start or not period_end:
        return []
    matches = overlapping_responsibility_windows(
        responsibility_index, matricula, period_start, period_end
    )
    rows: list[dict[str, Any]] = []
    for window in matches:
        window_start = max(window["start"], period_start)
        window_end = min(window["end"], period_end)
        if window_start > window_end:
            continue
        fac = window["facilitator"]
        fac_mat = (fac.user_lan_id or "").strip().lower() or f"id:{fac.pk}"
        rows.append(
            {
                **fallback,
                "matricula": matricula,
                "key": f"{matricula}|fac|{fac_mat}|{window_start}|{window_end}",
                "lider": (fac.full_name or "Sem facilitador").strip(),
                "lider_matricula": fac_mat,
                "facilitador_matricula": fac_mat,
                "responsabilidade": "facilitador",
                "regra_responsabilidade": window["kind"],
                "vigencia_inicio": window_start.isoformat(),
                "vigencia_fim": window_end.isoformat(),
                "window_start": window_start,
                "window_end": window_end,
            }
        )
    return rows


def facilitator_assignment_rows(
    responsibility_index: dict[str, list[dict[str, Any]]],
    matricula: str,
    start: date | None,
    end: date | None,
    fallback: dict[str, str],
) -> list[dict[str, Any]]:
    """Uma linha por facilitador com janelas sobrepostas ao período.

    Quando há facilitadores distintos, as métricas devem ser atribuídas por
    janela — não integralmente ao facilitador mais recente.
    """
    period_start = start or end
    period_end = end or start
    if not period_start or not period_end:
        return []
    matches = overlapping_responsibility_windows(
        responsibility_index, matricula, period_start, period_end
    )
    if not matches:
        return []

    by_facilitator: dict[str, list[dict[str, Any]]] = {}
    for window in matches:
        fac = window["facilitator"]
        fac_mat = (fac.user_lan_id or "").strip().lower() or f"id:{fac.pk}"
        by_facilitator.setdefault(fac_mat, []).append(window)

    rows: list[dict[str, Any]] = []
    for fac_mat, windows in by_facilitator.items():
        primary = max(windows, key=lambda item: item["event_date"])
        facilitator = primary["facilitator"]
        rows.append(
            {
                **fallback,
                "key": f"{matricula}|fac|{fac_mat}",
                "lider": (facilitator.full_name or "Sem facilitador").strip(),
                "lider_matricula": fac_mat,
                "facilitador_matricula": fac_mat,
                "responsabilidade": "facilitador",
                "regra_responsabilidade": primary["kind"],
                "responsibility_periods": serialize_responsibility_periods(windows),
                "windows": windows,
            }
        )
    return rows


def date_in_windows(on_date: date | None, windows: list[dict[str, Any]]) -> bool:
    if not on_date:
        return False
    return any(window["start"] <= on_date <= window["end"] for window in windows)


def equipe_occurrence_query(params, *, date_field: str) -> Q | None:
    """Q temporal: ocorrência na equipe vigente do agente na data do evento.

    Usa AgentHistory.team com vigência start_date..final_date (null = aberta).
    Várias equipes = OR. Ocorrências sem histórico compatível são excluídas.
    """
    equipes = _param_list(params, "equipe")
    if not equipes:
        return None
    team_q = Q()
    for value in equipes:
        team_q |= Q(team__iexact=value)
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    qs = (
        AgentHistory.objects.select_related("agent")
        .exclude(agent__user_lan_id="")
        .exclude(team="")
        .filter(team_q)
    )
    if start:
        qs = qs.filter(Q(final_date__isnull=True) | Q(final_date__gte=start))
    if end:
        qs = qs.filter(start_date__lte=end)

    query = Q(pk__in=[])
    grouped: dict[tuple[date, date], set[str]] = {}
    for history in qs.iterator(chunk_size=2000):
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if not matricula:
            continue
        hist_start = history.start_date
        hist_end = history.final_date or end or hist_start
        window_start = max(hist_start, start) if start else hist_start
        window_end = min(hist_end, end) if end else hist_end
        if window_start > window_end:
            continue
        grouped.setdefault((window_start, window_end), set()).add(matricula)

    for (window_start, window_end), matriculas in sorted(grouped.items()):
        query |= Q(
            matricula__in=sorted(matriculas),
            **{
                f"{date_field}__gte": window_start,
                f"{date_field}__lte": window_end,
            },
        )
    return query


def _occurrence_query_from_grouped(
    grouped: dict[tuple[date, date], set[str]],
    *,
    date_field: str,
) -> Q:
    query = Q(pk__in=[])
    for (window_start, window_end), matriculas in sorted(grouped.items()):
        query |= Q(
            matricula__in=sorted(matriculas),
            **{
                f"{date_field}__gte": window_start,
                f"{date_field}__lte": window_end,
            },
        )
    return query


def localidade_hc_occurrence_query(params, *, date_field: str) -> Q | None:
    """Q temporal: ocorrência na localidade HC vigente do agente na data do evento."""
    localidades = _param_list(params, "localidade_hc")
    if not localidades:
        return None
    loc_q = Q()
    for value in localidades:
        loc_q |= Q(location__iexact=value)
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    qs = (
        AgentHistory.objects.select_related("agent")
        .exclude(agent__user_lan_id="")
        .exclude(location="")
        .filter(loc_q)
    )
    if start:
        qs = qs.filter(Q(final_date__isnull=True) | Q(final_date__gte=start))
    if end:
        qs = qs.filter(start_date__lte=end)

    grouped: dict[tuple[date, date], set[str]] = {}
    for history in qs.iterator(chunk_size=2000):
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if not matricula:
            continue
        clipped = _clip_history_window(history, start, end)
        if not clipped:
            continue
        grouped.setdefault(clipped, set()).add(matricula)
    return _occurrence_query_from_grouped(grouped, date_field=date_field)


def turno_occurrence_query(params, *, date_field: str) -> Q | None:
    """Q temporal: ocorrência no turno (journey_shift) vigente na data do evento."""
    turnos = _param_list(params, "turno")
    if not turnos:
        return None
    turno_keys = {_normalized(value) for value in turnos}
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    qs = AgentHistory.objects.select_related("agent").exclude(agent__user_lan_id="")
    if start:
        qs = qs.filter(Q(final_date__isnull=True) | Q(final_date__gte=start))
    if end:
        qs = qs.filter(start_date__lte=end)

    grouped: dict[tuple[date, date], set[str]] = {}
    for history in qs.iterator(chunk_size=2000):
        shift = (history.journey_shift or "").strip()
        if not shift:
            shift = resolve_journey_shift(history.journey or "")
        if not shift or _normalized(shift) not in turno_keys:
            continue
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if not matricula:
            continue
        clipped = _clip_history_window(history, start, end)
        if not clipped:
            continue
        grouped.setdefault(clipped, set()).add(matricula)
    return _occurrence_query_from_grouped(grouped, date_field=date_field)


_OPEN_VIGENCIA_END = date(9999, 12, 31)


def _clip_history_window(
    history: AgentHistory,
    period_start: date | None,
    period_end: date | None,
) -> tuple[date, date] | None:
    """Recorte inclusivo da vigência do histórico dentro do período filtrado."""
    hist_start = history.start_date
    if history.final_date is not None:
        hist_end = history.final_date
    elif period_end is not None:
        hist_end = period_end
    else:
        hist_end = _OPEN_VIGENCIA_END
    window_start = max(hist_start, period_start) if period_start else hist_start
    window_end = min(hist_end, period_end) if period_end is not None else hist_end
    if window_start > window_end:
        return None
    return window_start, window_end


def _leader_histories_for_period(params) -> QuerySet:
    """Históricos sobrepostos ao período, opcionalmente filtrados por líder."""
    qs = (
        AgentHistory.objects.select_related("agent", "leader")
        .exclude(agent__user_lan_id="")
    )
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    if start:
        qs = qs.filter(Q(final_date__isnull=True) | Q(final_date__gte=start))
    if end:
        qs = qs.filter(start_date__lte=end)

    leaders = _param_list(params, "lider")
    if leaders:
        leader_q = Q()
        for value in leaders:
            leader_q |= Q(leader__full_name__iexact=value)
            leader_q |= Q(leader__user_lan_id__iexact=value)
        qs = qs.filter(leader_q)
    return qs


def build_leader_history_index(
    period_start: date | None,
    period_end: date | None,
) -> dict[str, list[AgentHistory]]:
    """Índice matricula → históricos sobrepostos ao período (ordenados por start_date)."""
    qs = AgentHistory.objects.select_related("agent", "leader").exclude(
        agent__user_lan_id=""
    )
    if period_start:
        qs = qs.filter(Q(final_date__isnull=True) | Q(final_date__gte=period_start))
    if period_end:
        qs = qs.filter(start_date__lte=period_end)

    index: dict[str, list[AgentHistory]] = {}
    for history in qs.order_by("agent_id", "start_date", "id"):
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if not matricula:
            continue
        index.setdefault(matricula, []).append(history)
    return index


def resolve_history_for_date(
    matricula: str,
    on_date: date | None,
    *,
    histories_by_mat: dict[str, list[AgentHistory]] | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> AgentHistory | None:
    """Histórico AgentHistory vigente na data efetiva; None se lacuna."""
    if not on_date:
        return None
    matricula = (matricula or "").strip().lower()
    if not matricula:
        return None
    if histories_by_mat is None:
        histories_by_mat = build_leader_history_index(period_start, period_end)
    histories = histories_by_mat.get(matricula) or []
    matches = [
        history
        for history in histories
        if history.start_date <= on_date
        and (history.final_date is None or history.final_date >= on_date)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: (item.start_date, item.id))


def resolve_leader_for_date(
    matricula: str,
    on_date: date | None,
    *,
    histories_by_mat: dict[str, list[AgentHistory]] | None = None,
    period_start: date | None = None,
    period_end: date | None = None,
) -> dict[str, str] | None:
    """Líder responsável na data efetiva; None se lacuna de histórico."""
    history = resolve_history_for_date(
        matricula,
        on_date,
        histories_by_mat=histories_by_mat,
        period_start=period_start,
        period_end=period_end,
    )
    if not history:
        return None
    matricula = (matricula or "").strip().lower()
    leader = history.leader
    return {
        "matricula": matricula,
        "agente": (history.agent.full_name or matricula).strip(),
        "lider": ((leader.full_name if leader else "") or "Sem líder").strip(),
        "lider_matricula": ((leader.user_lan_id if leader else "") or "").strip().lower(),
        "time": (history.team or "").strip(),
        "responsabilidade": "lider",
        "vigencia_inicio": history.start_date.isoformat(),
        "vigencia_fim": history.final_date.isoformat() if history.final_date else None,
    }


def leader_assignment_rows(
    matricula: str,
    start: date | None,
    end: date | None,
    fallback: dict[str, str],
    *,
    histories_by_mat: dict[str, list[AgentHistory]] | None = None,
) -> list[dict[str, Any]]:
    """Segmentos de líder por vigência sobreposta ao período."""
    matricula = (matricula or "").strip().lower()
    if not matricula:
        return []
    if histories_by_mat is None:
        histories_by_mat = build_leader_history_index(start, end)
    histories = histories_by_mat.get(matricula) or []
    rows: list[dict[str, Any]] = []
    for history in histories:
        clipped = _clip_history_window(history, start, end)
        if not clipped:
            continue
        window_start, window_end = clipped
        leader = history.leader
        lider_mat = ((leader.user_lan_id if leader else "") or "").strip().lower()
        lider_name = ((leader.full_name if leader else "") or "Sem líder").strip()
        rows.append(
            {
                **fallback,
                "matricula": matricula,
                "key": f"{matricula}|leader|{lider_mat}|{window_start}|{window_end}",
                "lider": lider_name,
                "lider_matricula": lider_mat,
                "time": (history.team or "").strip() or fallback.get("time", ""),
                "responsabilidade": "lider",
                "vigencia_inicio": window_start.isoformat(),
                "vigencia_fim": window_end.isoformat(),
                "window_start": window_start,
                "window_end": window_end,
            }
        )
    return rows


def leader_occurrence_query(params, *, date_field: str) -> Q | None:
    """Q temporal: ocorrência na vigência do líder selecionado na data do evento."""
    leaders = _param_list(params, "lider")
    if not leaders:
        return None
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    query = Q(pk__in=[])
    grouped: dict[tuple[date, date], set[str]] = {}
    for history in _leader_histories_for_period(params).iterator(chunk_size=2000):
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if not matricula:
            continue
        clipped = _clip_history_window(history, start, end)
        if not clipped:
            continue
        grouped.setdefault(clipped, set()).add(matricula)

    for (window_start, window_end), matriculas in sorted(grouped.items()):
        query |= Q(
            matricula__in=sorted(matriculas),
            **{
                f"{date_field}__gte": window_start,
                f"{date_field}__lte": window_end,
            },
        )
    return query


def facilitator_window_query(
    params, *, date_field: str, exact_matricula: bool = False
) -> Q:
    """Q das ocorrências que pertencem a janelas de facilitador no período.

    Quando o queryset já foi restringido às chaves normalizadas do workforce,
    janelas iguais são agrupadas em `matricula__in`. Fora desse escopo, o
    comportamento legado case-insensitive é preservado.
    """
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    fac_filter = {
        value.strip().lower()
        for value in _param_list(params, "facilitador_matricula")
        if value and str(value).strip()
    }
    query = Q(pk__in=[])
    grouped: dict[tuple[date, date], set[str]] = {}
    for matricula, windows in build_responsibility_index().items():
        for window in windows:
            if fac_filter:
                fac_mat = (window["facilitator"].user_lan_id or "").strip().lower()
                if fac_mat not in fac_filter:
                    continue
            window_start = max(window["start"], start) if start else window["start"]
            window_end = min(window["end"], end) if end else window["end"]
            if window_start > window_end:
                continue
            if exact_matricula:
                grouped.setdefault((window_start, window_end), set()).add(matricula)
            else:
                query |= Q(
                    matricula__iexact=matricula,
                    **{
                        f"{date_field}__gte": window_start,
                        f"{date_field}__lte": window_end,
                    },
                )
    if exact_matricula:
        for (window_start, window_end), matriculas in sorted(grouped.items()):
            query |= Q(
                matricula__in=sorted(matriculas),
                **{
                    f"{date_field}__gte": window_start,
                    f"{date_field}__lte": window_end,
                },
            )
    return query

def workforce_assignments(params) -> dict[str, dict[str, str]]:
    """Retorna a alocação mais recente de cada matrícula que sobrepõe o período."""
    cached = get_workforce_cached(params)
    if cached is not None:
        return cached
    qs = AgentHistory.objects.select_related("agent", "leader").exclude(
        agent__user_lan_id=""
    )
    start = _parse_date(params.get("start_date") if hasattr(params, "get") else None)
    end = _parse_date(params.get("end_date") if hasattr(params, "get") else None)
    if start:
        qs = qs.filter(Q(final_date__isnull=True) | Q(final_date__gte=start))
    if end:
        qs = qs.filter(start_date__lte=end)

    leaders = _param_list(params, "lider")
    if leaders:
        leader_q = Q()
        for value in leaders:
            leader_q |= Q(leader__full_name__iexact=value)
            leader_q |= Q(leader__user_lan_id__iexact=value)
        qs = qs.filter(leader_q)

    assignments: dict[str, dict[str, str]] = {}
    for history in qs.order_by("agent_id", "-start_date"):
        matricula = (history.agent.user_lan_id or "").strip().lower()
        if not matricula or matricula in assignments:
            continue
        leader = history.leader
        assignments[matricula] = {
            "matricula": matricula,
            "agente": (history.agent.full_name or matricula).strip(),
            "lider": ((leader.full_name if leader else "") or "Sem líder").strip(),
            "lider_matricula": ((leader.user_lan_id if leader else "") or "").strip().lower(),
            "time": (history.team or "").strip(),
            "responsabilidade": "lider",
        }
    # Escopo "lider": mantém o líder real (tabela de líderes separada).
    # Escopo "facilitador"/"all": destaca o facilitador quando a janela
    # se sobrepõe a qualquer dia do período filtrado.
    responsibility_scope = ""
    if hasattr(params, "get"):
        responsibility_scope = (params.get("responsibility_scope") or "").strip().lower()
    if responsibility_scope != "lider" and (start or end):
        responsibility_index = build_responsibility_index()
        for matricula, assignment in assignments.items():
            assignments[matricula] = responsibility_for_period(
                responsibility_index, matricula, start, end, assignment
            )

    set_workforce_cached(params, assignments)
    return assignments


def scope_quality_queryset(
    qs: QuerySet,
    params,
    *,
    date_field: str = "data",
    force: bool = False,
) -> QuerySet:
    """Mantém fatos no escopo workforce; filtro de líder usa vigência temporal."""
    if not force and not workforce_scope_enabled(params):
        return qs
    if _param_list(params, "lider"):
        leader_q = leader_occurrence_query(params, date_field=date_field)
        if leader_q is None:
            return qs.none()
        return qs.filter(leader_q)
    matriculas = list(workforce_assignments(params))
    if not matriculas:
        return qs.none()
    return qs.filter(matricula__in=matriculas)
