"""Validação de regras de troca de escala."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.utils import timezone

from apps.workforce.models import Agent

from ..models import Escala, Schedule
from .permissions import get_active_history
from .overtime import STANDARD_WORK_HOURS, hours_from_work_schedule
from .swap_request_workflow import validate_swap_date
from .schedule_utils import (
    is_time_range_schedule,
    normalize_time_schedule,
    resolve_escala_work_schedule,
    schedule_end_datetime,
    schedule_start_datetime,
)

SWAP_KIND_SHIFT_SCHEDULE = "shift_schedule"
SWAP_KIND_SHIFT_BH = "shift_bh"
SWAP_KIND_PEER = "peer"

SWAP_KIND_CHOICES = (
    SWAP_KIND_SHIFT_SCHEDULE,
    SWAP_KIND_SHIFT_BH,
    SWAP_KIND_PEER,
)

MAX_CONSECUTIVE_WORK_DAYS = 6
MIN_REST_HOURS = 11
VALIDATION_OK = "ok"
VALIDATION_NA = "n/a"
VALIDATION_NO_DATA = "sem escala"
SCHEDULE_WINDOW_DAYS = 10


@dataclass
class DaySchedule:
    work_schedule: str = ""
    is_work_day: bool = False
    job_activity: str = ""


@dataclass
class SwapRuleCheck:
    key: str
    label: str
    passed: bool
    message: str


@dataclass
class SwapValidationResult:
    is_valid: bool
    errors: list[str]
    validation_days: str
    validation_hour: str
    validation_activity: str
    checks: list[SwapRuleCheck]


def _agent_by_lan(lan_id: str) -> Agent | None:
    return Agent.objects.filter(user_lan_id__iexact=lan_id.strip()).first()


def _job_activity_for_agent(agent: Agent, target: date) -> str:
    escala = (
        Escala.objects.filter(agent=agent, data=target)
        .select_related("job_activity")
        .first()
    )
    if escala and escala.job_activity:
        return escala.job_activity.name.strip()

    history = get_active_history(agent)
    if history and history.job_activity:
        return str(history.job_activity).strip()
    return ""


def _headcount_activity_for_agent(agent: Agent) -> str:
    history = get_active_history(agent)
    if history and history.job_activity:
        return str(history.job_activity).strip()
    return ""


def _day_schedule_from_escala(escala: Escala) -> DaySchedule:
    ws = resolve_escala_work_schedule(escala.dia_escala, escala.horario)
    activity = escala.job_activity.name.strip() if escala.job_activity else ""
    if is_time_range_schedule(ws):
        return DaySchedule(work_schedule=ws, is_work_day=True, job_activity=activity)
    return DaySchedule(job_activity=activity)


def get_published_day_schedule(agent: Agent, target: date) -> DaySchedule | None:
    escala = (
        Escala.objects.filter(agent=agent, data=target)
        .select_related("job_activity")
        .first()
    )
    if not escala:
        return None
    return _day_schedule_from_escala(escala)


def validate_published_work_schedule(agent: Agent, target: date) -> str | None:
    published = get_published_day_schedule(agent, target)
    if published is None:
        return "Não há escala publicada na data informada."
    if not published.is_work_day:
        return "O agente não possui turno publicado na data informada."
    return None


def get_agent_day_schedule(agent: Agent, target: date) -> DaySchedule:
    schedule = Schedule.objects.filter(agent=agent, date=target).first()
    if schedule:
        ws = normalize_time_schedule(schedule.work_schedule or "")
        if schedule.work_day and is_time_range_schedule(ws):
            return DaySchedule(
                work_schedule=ws,
                is_work_day=True,
                job_activity=_job_activity_for_agent(agent, target),
            )
        return DaySchedule(job_activity=_job_activity_for_agent(agent, target))

    escala = (
        Escala.objects.filter(agent=agent, data=target)
        .select_related("job_activity")
        .first()
    )
    if escala:
        ws = resolve_escala_work_schedule(escala.dia_escala, escala.horario)
        activity = escala.job_activity.name.strip() if escala.job_activity else ""
        if is_time_range_schedule(ws):
            return DaySchedule(work_schedule=ws, is_work_day=True, job_activity=activity)
        return DaySchedule(job_activity=activity)

    return DaySchedule(job_activity=_job_activity_for_agent(agent, target))


def build_schedule_map(
    agent: Agent,
    date_from: date,
    date_to: date,
    overrides: dict[date, DaySchedule] | None = None,
) -> dict[date, DaySchedule]:
    overrides = overrides or {}
    result: dict[date, DaySchedule] = {}
    current = date_from
    while current <= date_to:
        result[current] = overrides.get(current) or get_agent_day_schedule(agent, current)
        current += timedelta(days=1)
    return result


def build_published_schedule_map(
    agent: Agent,
    date_from: date,
    date_to: date,
    overrides: dict[date, DaySchedule] | None = None,
) -> dict[date, DaySchedule]:
    """Monta a janela usando exclusivamente a escala publicada."""
    overrides = overrides or {}
    published_by_date = {
        escala.data: _day_schedule_from_escala(escala)
        for escala in Escala.objects.filter(
            agent=agent,
            data__range=(date_from, date_to),
        ).select_related("job_activity")
    }
    result: dict[date, DaySchedule] = {}
    current = date_from
    while current <= date_to:
        result[current] = overrides.get(current) or published_by_date.get(current) or DaySchedule()
        current += timedelta(days=1)
    return result


def _max_consecutive_work_days(schedule_map: dict[date, DaySchedule], date_from: date, date_to: date) -> int:
    max_streak = 0
    streak = 0
    for current in sorted(day for day in schedule_map if date_from <= day <= date_to):
        if schedule_map[current].is_work_day:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def validate_consecutive_days(
    schedule_map: dict[date, DaySchedule],
    date_from: date,
    date_to: date,
) -> str:
    if not any(schedule_map[d].is_work_day for d in schedule_map if date_from <= d <= date_to):
        return VALIDATION_NO_DATA
    streak = _max_consecutive_work_days(schedule_map, date_from, date_to)
    if streak > MAX_CONSECUTIVE_WORK_DAYS:
        return f"{streak} dias consecutivos"
    return VALIDATION_OK


def validate_rest_around_swap(
    schedule_map: dict[date, DaySchedule],
    swap_date: date,
) -> str:
    """Valida os descansos imediatamente antes e depois do turno alterado."""
    proposed = schedule_map.get(swap_date)
    if not proposed or not proposed.is_work_day:
        return VALIDATION_NO_DATA

    previous_dates = sorted(
        (day for day, item in schedule_map.items() if day < swap_date and item.is_work_day),
        reverse=True,
    )
    next_dates = sorted(
        day for day, item in schedule_map.items() if day > swap_date and item.is_work_day
    )
    boundaries: list[tuple[date, date, str]] = []
    if previous_dates:
        boundaries.append((previous_dates[0], swap_date, "turno anterior e turno solicitado"))
    if next_dates:
        boundaries.append((swap_date, next_dates[0], "turno solicitado e turno posterior"))
    if not boundaries:
        return VALIDATION_NO_DATA

    for prev_day, curr_day, boundary_label in boundaries:
        prev_ws = schedule_map[prev_day].work_schedule
        curr_ws = schedule_map[curr_day].work_schedule
        end_dt = schedule_end_datetime(prev_day, prev_ws)
        start_dt = schedule_start_datetime(curr_day, curr_ws)
        if not end_dt or not start_dt:
            continue

        end_aware = timezone.make_aware(end_dt) if timezone.is_naive(end_dt) else end_dt
        start_aware = timezone.make_aware(start_dt) if timezone.is_naive(start_dt) else start_dt
        rest_hours = (start_aware - end_aware).total_seconds() / 3600
        if rest_hours < MIN_REST_HOURS:
            return (
                f"{rest_hours:.1f}h entre {boundary_label} "
                f"({prev_day:%d/%m} e {curr_day:%d/%m})"
            )
    return VALIDATION_OK


def _agent_display_name(agent: Agent) -> str:
    full = (agent.full_name or "").strip()
    if full:
        return full.split()[0]
    return (agent.user_lan_id or "Agente").strip()


def _format_activity_comparison(
    agent_a: Agent,
    activity_a: str,
    agent_b: Agent,
    activity_b: str,
) -> str:
    label_a = activity_a or "não identificada"
    label_b = activity_b or "não identificada"
    return (
        f"{_agent_display_name(agent_a)}: {label_a}\n"
        f"{_agent_display_name(agent_b)}: {label_b}"
    )


def validate_same_activity(agent_a: Agent, agent_b: Agent) -> str:
    activity_a = _headcount_activity_for_agent(agent_a)
    activity_b = _headcount_activity_for_agent(agent_b)
    if not activity_a or not activity_b:
        return _format_activity_comparison(agent_a, activity_a, agent_b, activity_b)
    if activity_a.casefold() != activity_b.casefold():
        return _format_activity_comparison(agent_a, activity_a, agent_b, activity_b)
    return VALIDATION_OK


def _rule_passed(value: str) -> bool:
    return value == VALIDATION_OK


def _rule_message(value: str) -> str:
    if value == VALIDATION_OK:
        return ""
    if value == VALIDATION_NO_DATA:
        return "Sem escala para validar"
    return value


def build_validation_checks(
    *,
    swap_kind: str,
    validation_days: str,
    validation_hour: str,
    validation_activity: str,
) -> list[SwapRuleCheck]:
    if swap_kind != SWAP_KIND_PEER:
        return []

    return [
        SwapRuleCheck(
            key="days",
            label="7 dias consecutivos trabalhados",
            passed=_rule_passed(validation_days),
            message=_rule_message(validation_days),
        ),
        SwapRuleCheck(
            key="rest",
            label="11 horas de descanso entre turnos",
            passed=_rule_passed(validation_hour),
            message=_rule_message(validation_hour),
        ),
        SwapRuleCheck(
            key="activity",
            label="Mesma atividade",
            passed=validation_activity == VALIDATION_OK,
            message=_rule_message(validation_activity)
            if validation_activity == VALIDATION_OK
            else (validation_activity or "atividade não identificada"),
        ),
    ]


def _finalize_validation_result(
    *,
    swap_kind: str,
    is_valid: bool,
    errors: list[str],
    validation_days: str,
    validation_hour: str,
    validation_activity: str,
) -> SwapValidationResult:
    checks = build_validation_checks(
        swap_kind=swap_kind,
        validation_days=validation_days,
        validation_hour=validation_hour,
        validation_activity=validation_activity,
    )
    return SwapValidationResult(
        is_valid=is_valid,
        errors=errors,
        validation_days=validation_days,
        validation_hour=validation_hour,
        validation_activity=validation_activity,
        checks=checks,
    )


def _combine_validation(results: list[str]) -> str:
    for item in results:
        if item not in (VALIDATION_OK, VALIDATION_NA, VALIDATION_NO_DATA):
            return item
    if all(item == VALIDATION_NO_DATA for item in results):
        return VALIDATION_NO_DATA
    return VALIDATION_OK


def validate_swap_request_data(
    *,
    swap_kind: str,
    agent_lan_id: str,
    date_swap: date,
    agent_lan_id_2: str = "",
    new_journey: str = "",
) -> SwapValidationResult:
    errors: list[str] = []
    days_results: list[str] = []
    hour_results: list[str] = []
    activity_result = VALIDATION_NA

    if swap_kind not in SWAP_KIND_CHOICES:
        return _finalize_validation_result(
            swap_kind=swap_kind,
            is_valid=False,
            errors=["Tipo de troca inválido."],
            validation_days="",
            validation_hour="",
            validation_activity="",
        )

    date_error = validate_swap_date(date_swap)
    if date_error:
        return _finalize_validation_result(
            swap_kind=swap_kind,
            is_valid=False,
            errors=[date_error],
            validation_days="",
            validation_hour="",
            validation_activity="",
        )

    agent = _agent_by_lan(agent_lan_id)
    if not agent:
        return _finalize_validation_result(
            swap_kind=swap_kind,
            is_valid=False,
            errors=["Agente não encontrado."],
            validation_days="",
            validation_hour="",
            validation_activity="",
        )

    date_from = date_swap - timedelta(days=SCHEDULE_WINDOW_DAYS)
    date_to = date_swap + timedelta(days=SCHEDULE_WINDOW_DAYS)

    agent_checks: list[tuple[Agent, dict[date, DaySchedule]]] = []

    if swap_kind == SWAP_KIND_PEER:
        partner = _agent_by_lan(agent_lan_id_2)
        if not partner:
            return _finalize_validation_result(
                swap_kind=swap_kind,
                is_valid=False,
                errors=["Agente parceiro não encontrado."],
                validation_days="",
                validation_hour="",
                validation_activity="",
            )
        if partner.user_lan_id.lower() == agent.user_lan_id.lower():
            return _finalize_validation_result(
                swap_kind=swap_kind,
                is_valid=False,
                errors=["Selecione um parceiro diferente do agente."],
                validation_days="",
                validation_hour="",
                validation_activity="",
            )

        day_a = get_published_day_schedule(agent, date_swap) or DaySchedule()
        day_b = get_published_day_schedule(partner, date_swap) or DaySchedule()
        if not day_a.is_work_day and not day_b.is_work_day:
            errors.append("Nenhum dos agentes possui escala de trabalho na data informada.")

        agent_checks.append((agent, {date_swap: day_b}))
        agent_checks.append((partner, {date_swap: day_a}))
        activity_result = validate_same_activity(agent, partner)

    elif swap_kind == SWAP_KIND_SHIFT_BH:
        schedule_error = validate_published_work_schedule(agent, date_swap)
        if schedule_error:
            return _finalize_validation_result(
                swap_kind=swap_kind,
                is_valid=False,
                errors=[schedule_error],
                validation_days=VALIDATION_NA,
                validation_hour=VALIDATION_NA,
                validation_activity=VALIDATION_NA,
            )
        return _finalize_validation_result(
            swap_kind=swap_kind,
            is_valid=True,
            errors=[],
            validation_days=VALIDATION_NA,
            validation_hour=VALIDATION_NA,
            validation_activity=VALIDATION_NA,
        )

    else:
        normalized = normalize_time_schedule(new_journey or "")
        if not is_time_range_schedule(normalized):
            return _finalize_validation_result(
                swap_kind=swap_kind,
                is_valid=False,
                errors=["Informe o novo horário no formato HH:MM - HH:MM."],
                validation_days="",
                validation_hour="",
                validation_activity=VALIDATION_NA,
            )
        computed = hours_from_work_schedule(normalized)
        if computed != STANDARD_WORK_HOURS:
            return _finalize_validation_result(
                swap_kind=swap_kind,
                is_valid=False,
                errors=["A carga horária deve ser igual a 6 horas."],
                validation_days="",
                validation_hour="",
                validation_activity=VALIDATION_NA,
            )
        agent_checks.append(
            (
                agent,
                {date_swap: DaySchedule(work_schedule=normalized, is_work_day=True)},
            )
        )

    for checked_agent, overrides in agent_checks:
        schedule_map = build_schedule_map(checked_agent, date_from, date_to, overrides)
        published_schedule_map = build_published_schedule_map(
            checked_agent,
            date_from,
            date_to,
            overrides,
        )
        label = checked_agent.full_name or checked_agent.user_lan_id
        days_check = validate_consecutive_days(schedule_map, date_from, date_to)
        hours_check = validate_rest_around_swap(published_schedule_map, date_swap)
        days_results.append(days_check)
        hour_results.append(hours_check)
        if days_check not in (VALIDATION_OK, VALIDATION_NO_DATA):
            errors.append(f"{label}: máximo de 7 dias consecutivos ({days_check}).")
        if hours_check not in (VALIDATION_OK, VALIDATION_NO_DATA):
            errors.append(f"{label}: descanso mínimo de 11h não atendido ({hours_check}).")

    validation_days = _combine_validation(days_results)
    validation_hour = _combine_validation(hour_results)

    if swap_kind == SWAP_KIND_PEER:
        if validation_days == VALIDATION_NO_DATA:
            errors.append("Não foi possível validar os 7 dias consecutivos dos dois agentes.")
        if validation_hour == VALIDATION_NO_DATA:
            errors.append("Não foi possível validar as 11 horas de descanso dos dois agentes.")

    return _finalize_validation_result(
        swap_kind=swap_kind,
        is_valid=not errors,
        errors=errors,
        validation_days=validation_days,
        validation_hour=validation_hour,
        validation_activity=activity_result,
    )


def serialize_validation_result(result: SwapValidationResult) -> dict:
    return {
        "is_valid": result.is_valid,
        "errors": result.errors,
        "validation_days": result.validation_days,
        "validation_hour": result.validation_hour,
        "validation_activity": result.validation_activity,
        "checks": [
            {
                "key": check.key,
                "label": check.label,
                "passed": check.passed,
                "message": check.message,
            }
            for check in result.checks
        ],
    }
