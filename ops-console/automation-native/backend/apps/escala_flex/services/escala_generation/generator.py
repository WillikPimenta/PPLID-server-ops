"""Gerador determinístico da prévia mensal."""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from django.db import transaction
from django.utils import timezone

from apps.escala_flex.models import (
    Escala,
    EscalaGenerationConflict,
    EscalaGenerationEntry,
    EscalaGenerationRun,
    JobActivity,
    Location,
)
from apps.escala_flex.services.schedule_utils import (
    is_night_shift_crossing,
    is_time_range_schedule,
    normalize_time_schedule,
    parse_work_schedule,
)

from .analysis import build_preview_analysis
from .conflicts import collect_conflicts
from .constants import DEFAULT_COVERAGE_SCHEDULES
from .eligibility import load_eligible_agent_days
from .rules import (
    holiday_override_for,
    hours_covered_by_schedule,
    is_5x2_schedule,
    is_weekend_excluded,
    is_work_day_value,
    load_holidays_for_month,
    merge_configuration,
    normalize_schedule_value,
    parse_activity_coverage,
    proposed_work_breaks_consecutive_days,
    proposed_work_breaks_rest,
    rest_hours_between,
    weekday_allowed,
)

# Diferença máxima aceitável de FOLGAs entre operacionais (diurnos fora 5x2).
FOLGA_SOFT_SPREAD = 1


def _resolve_job_activity(name: str) -> JobActivity | None:
    text = (name or "").strip()
    if not text:
        return None
    obj, _ = JobActivity.objects.get_or_create(name=text, defaults={"active": True})
    return obj


def _resolve_location(name: str) -> Location | None:
    text = (name or "").strip()
    if not text:
        return None
    existing = Location.objects.filter(city_name__iexact=text).first()
    if existing:
        return existing
    existing = Location.objects.filter(display_name__iexact=text).first()
    if existing:
        return existing
    return Location.objects.create(city_name=text, display_name=text)


def _balance_key(stats: dict, lan_id: str) -> tuple:
    return (
        stats.get("worked", 0),
        stats.get("weekend", 0),
        stats.get("holiday", 0),
        -stats.get("days_since_off", 0),
        lan_id or "",
    )


def _locked_day_value(entry: dict) -> bool:
    """Ausências conhecidas / feriado / 5x2 no fim de semana não viram trabalho automático."""
    if entry.get("fixed_weekend_off"):
        return True
    return entry.get("source") in {
        EscalaGenerationEntry.SOURCE_ABSENCE,
        EscalaGenerationEntry.SOURCE_HOLIDAY,
    }


def _is_5x2_entry(entry: dict) -> bool:
    if entry.get("is_5x2") is not None:
        return bool(entry.get("is_5x2"))
    return is_5x2_schedule(
        entry.get("schedule") or "",
        entry.get("journey_shift") or "",
    )


def _skip_as_weekend_cover(entry: dict, day: date) -> bool:
    """5x2 não cobre fim de semana (folga fixa sáb/dom)."""
    return day.weekday() >= 5 and _is_5x2_entry(entry)


def _activity_matches(entry: dict, activity: str) -> bool:
    return (entry.get("activity_name") or "").casefold() == (activity or "").casefold()


def _working_on_day(entries: list[dict], day: date) -> list[dict]:
    return [
        e
        for e in entries
        if e["date"] == day and is_work_day_value(e.get("day_value") or "")
    ]


def _matching_workers(
    entries: list[dict],
    day: date,
    activity: str,
    schedules: list[str],
) -> list[dict]:
    matched = []
    for e in _working_on_day(entries, day):
        if not _activity_matches(e, activity):
            continue
        effective = normalize_schedule_value(e["day_value"])
        if schedules and effective not in schedules:
            continue
        matched.append(e)
    return matched


def _schedule_slot_count(
    entries: list[dict],
    day: date,
    activity: str,
    schedule: str,
) -> int:
    """Conta quem está trabalhando no slot (day_value). Atividade é preferência, não bloqueio."""
    target = normalize_schedule_value(schedule)
    total = 0
    for e in entries:
        if e["date"] != day:
            continue
        if not is_work_day_value(e.get("day_value") or ""):
            continue
        if normalize_schedule_value(e.get("day_value") or "") != target:
            continue
        total += 1
    return total


def _schedules_in_pool(
    entries: list[dict],
    activity: str,
    schedules: list[str],
) -> list[str]:
    """Horários de cobertura aplicáveis para a atividade.

    Um slot entra se existir ao menos um agente diurno da atividade que possa
    assumi-lo (jornada exata ou substituição por proximidade no fim de semana).
    """
    allowed = [normalize_schedule_value(s) for s in schedules if s]
    if not allowed:
        return []
    allowed_set = set(allowed)
    pool: list[str] = []
    for slot in allowed:
        for e in entries:
            if not _activity_matches(e, activity):
                continue
            own = normalize_schedule_value(e.get("schedule") or "")
            if not is_work_day_value(own):
                continue
            if own in allowed_set:
                pool.append(slot)
                break
            nearest = min(allowed_set, key=lambda s: _schedule_proximity_key(s, own))
            if nearest == slot:
                pool.append(slot)
                break
    return pool


def _schedules_below_min(
    entries: list[dict],
    day: date,
    activity: str,
    schedules: list[str],
    min_coverage: int,
) -> list[str]:
    """Slots abaixo do mínimo entre os horários de cobertura presentes no time."""
    applicable = _schedules_in_pool(entries, activity, schedules)
    if not applicable:
        return []
    return [
        s
        for s in applicable
        if _schedule_slot_count(entries, day, activity, s) < min_coverage
    ]


def _works_on(entry: dict | None) -> bool:
    return bool(entry) and is_work_day_value(entry.get("day_value") or "")


def _other_weekend_day(day: date, pairs: list[tuple[date, date | None]]) -> date | None:
    for sat, sun in pairs:
        if day == sat:
            return sun
        if sun is not None and day == sun:
            return sat
    if day.weekday() == 5:
        return date.fromordinal(day.toordinal() + 1)
    if day.weekday() == 6:
        return date.fromordinal(day.toordinal() - 1)
    return None


def _would_break_coverage(entries: list[dict], candidate: dict, configuration: dict) -> bool:
    """True se transformar candidate em FOLGA derrubar cobertura mínima de um horário."""
    day = candidate["date"]
    effective = normalize_schedule_value(candidate.get("day_value") or "")
    if not is_work_day_value(effective):
        return False
    for cov in parse_activity_coverage(configuration):
        if not cov.min_coverage or not weekday_allowed(day, cov.weekdays):
            continue
        if cov.schedules and effective not in {
            normalize_schedule_value(s) for s in cov.schedules
        }:
            continue
        others = _schedule_slot_count(entries, day, cov.activity, effective)
        if others - 1 < cov.min_coverage:
            return True
    return False


def _count_folgas(entries: list[dict]) -> dict:
    """Conta FOLGAs geradas (ignora ausência/feriado/5x2 fixa)."""
    counts: dict = defaultdict(int)
    for e in entries:
        if e.get("source") in {
            EscalaGenerationEntry.SOURCE_ABSENCE,
            EscalaGenerationEntry.SOURCE_HOLIDAY,
        }:
            continue
        if e.get("fixed_weekend_off"):
            continue
        if str(e.get("day_value") or "").upper() == "FOLGA":
            counts[e["agent"].id] += 1
    return counts


def _weekend_folga_counts(entries: list[dict], agent_ids: list) -> dict:
    counts = {aid: 0 for aid in agent_ids}
    for e in entries:
        aid = e["agent"].id
        if aid not in counts or e["date"].weekday() < 5:
            continue
        if str(e.get("day_value") or "").upper() == "FOLGA":
            counts[aid] += 1
    return counts


def _weekend_work_counts(entries: list[dict], agent_ids: list) -> dict:
    counts = {aid: 0 for aid in agent_ids}
    for e in entries:
        aid = e["agent"].id
        if aid not in counts or e["date"].weekday() < 5:
            continue
        if is_work_day_value(e.get("day_value") or ""):
            counts[aid] += 1
    return counts


def _set_generated_folga(entry: dict) -> None:
    entry["day_value"] = "FOLGA"
    entry["source"] = EscalaGenerationEntry.SOURCE_GENERATED
    entry["holiday_work"] = False


def _restore_work(entry: dict, configuration: dict | None = None) -> bool:
    """Restaura jornada; no FDS só nos 3 slots de cobertura. False se não deu."""
    day = entry.get("date")
    schedule = entry.get("schedule") or ""
    if isinstance(day, date) and day.weekday() >= 5:
        mapped = _weekend_slot_for_schedule(schedule, configuration or {})
        if not mapped:
            return False
        entry["day_value"] = mapped
        entry["source"] = EscalaGenerationEntry.SOURCE_GENERATED
        entry["holiday_work"] = False
        return True
    if is_work_day_value(schedule):
        entry["day_value"] = schedule
        entry["source"] = EscalaGenerationEntry.SOURCE_GENERATED
        entry["holiday_work"] = False
        return True
    return False


def _folgas_on_day(entries: list[dict], day: date) -> int:
    return sum(
        1
        for e in entries
        if e["date"] == day and str(e.get("day_value") or "").upper() == "FOLGA"
    )


def _current_max_streak(agent_entries: list[dict]) -> int:
    best = 0
    cur = 0
    for e in agent_entries:
        if is_work_day_value(e.get("day_value") or ""):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _schedule_proximity_key(target: str, other: str) -> tuple:
    """Menor = mais próximo (mais sobreposição de horas, depois início mais perto)."""
    th = hours_covered_by_schedule(target)
    oh = hours_covered_by_schedule(other)
    if not th or not oh:
        return (10_000, 10_000)
    overlap = len(th & oh)
    try:
        ta, _ = parse_work_schedule(normalize_time_schedule(target))
        oa, _ = parse_work_schedule(normalize_time_schedule(other))
        raw = abs(ta - oa)
        start_dist = min(raw, 24 * 60 - raw)
    except (TypeError, ValueError):
        start_dist = 10_000
    return (-overlap, start_dist)


def _would_break_rest(
    entries: list[dict],
    entry: dict,
    new_schedule: str,
    configuration: dict,
) -> bool:
    """True se o horário proposto violar descanso mínimo com o dia anterior/seguinte."""
    return proposed_work_breaks_rest(
        entries,
        entry,
        new_schedule,
        float(configuration.get("min_rest_hours") or 0),
    )


def _would_break_consecutive(
    entries: list[dict],
    entry: dict,
    configuration: dict,
) -> bool:
    return proposed_work_breaks_consecutive_days(
        entries,
        entry,
        int(configuration.get("max_consecutive_work_days") or 0),
    )


def _activate_substitute(
    entry: dict,
    target_schedule: str | None = None,
    configuration: dict | None = None,
) -> None:
    """Reativa o agente. No FDS, day_value fica sempre em slot de cobertura."""
    own = normalize_schedule_value(entry.get("schedule") or "")
    day = entry.get("date")
    cfg = configuration or {}
    weekend = isinstance(day, date) and day.weekday() >= 5

    if target_schedule is None:
        if weekend:
            mapped = _weekend_slot_for_schedule(own, cfg)
            if not mapped:
                return
            entry["day_value"] = mapped
        else:
            if not is_work_day_value(own):
                return
            entry["day_value"] = own
    else:
        target = normalize_schedule_value(target_schedule)
        if weekend:
            cov = _coverage_schedule_set(cfg) or {
                normalize_schedule_value(s) for s in DEFAULT_COVERAGE_SCHEDULES
            }
            if target not in cov:
                target = min(cov, key=lambda s: _schedule_proximity_key(s, target))
            entry["day_value"] = target
        else:
            if not is_work_day_value(own):
                return
            entry["day_value"] = own if own == target else target
    entry["source"] = EscalaGenerationEntry.SOURCE_GENERATED
    entry["holiday_work"] = False


def _weekend_work_penalty(
    entries: list[dict],
    entry: dict,
    day: date,
) -> int:
    """1 se ativar esta pessoa faria (ou mantém) trabalho nos dois dias do fim de semana."""
    pairs = _weekend_pairs(sorted({e["date"] for e in entries}))
    other = _other_weekend_day(day, pairs)
    if other is None:
        return 0
    by_agent = [e for e in entries if e["agent"].id == entry["agent"].id and e["date"] == other]
    if by_agent and _works_on(by_agent[0]):
        return 1
    return 0


def _coverage_schedule_set(configuration: dict) -> set[str]:
    """Horários com cobertura mínima efetiva (activity_coverage); fallback defaults."""
    out: set[str] = set()
    for cov in parse_activity_coverage(configuration):
        if not cov.min_coverage:
            continue
        for s in cov.schedules or []:
            text = normalize_schedule_value(s)
            if text:
                out.add(text)
    if out:
        return out
    raw = configuration.get("coverage_schedules") or DEFAULT_COVERAGE_SCHEDULES
    return {normalize_schedule_value(s) for s in raw if s}


def _nearest_coverage_slot(schedule: str, configuration: dict) -> str | None:
    """Slot de cobertura mais próximo da jornada cadastral (diurno ou noturno)."""
    cov = sorted(_coverage_schedule_set(configuration))
    if not cov:
        cov = [normalize_schedule_value(s) for s in DEFAULT_COVERAGE_SCHEDULES]
    sched = normalize_schedule_value(schedule or "")
    if not is_work_day_value(sched):
        return None
    if sched in cov:
        return sched
    return min(cov, key=lambda slot: _schedule_proximity_key(slot, sched))


def _weekend_slot_for_schedule(schedule: str, configuration: dict) -> str | None:
    """Mapeia jornada cadastral para um dos slots de cobertura no FDS.

    Noturno (ex. 19:30–01:30) sobe para o slot diurno mais próximo para poder
    participar da cobertura no fim de semana; dias úteis mantêm a jornada real.
    """
    return _nearest_coverage_slot(schedule, configuration)


def _schedule_types_compatible_for_cover(
    schedule: str,
    target: str,
    day: date,
) -> bool:
    """Noturno pode cobrir slot diurno apenas no fim de semana."""
    night_sched = is_night_shift_crossing(schedule)
    night_target = is_night_shift_crossing(target)
    if night_sched == night_target:
        return True
    return day.weekday() >= 5 and night_sched and is_work_day_value(target)


def _is_coverage_agent(entry: dict, configuration: dict) -> bool:
    sched = normalize_schedule_value(entry.get("schedule") or "")
    if not sched or not is_work_day_value(sched):
        return False
    cov = _coverage_schedule_set(configuration)
    if is_night_shift_crossing(sched):
        return bool(cov)
    if not cov:
        return True
    if sched in cov:
        return True
    # Diurno próximo a slot de cobertura participa da rotação (não vira helper fixo).
    return True


def _is_night_agent(entry: dict) -> bool:
    return is_night_shift_crossing(entry.get("schedule") or "")


def _try_activate_cover(
    entries: list[dict],
    day: date,
    candidate: dict,
    configuration: dict,
    *,
    protect_balance: bool = True,
    allow_foreign: bool = True,
) -> dict | None:
    """Ativa 1 substituto. Proximidade/mesmo horário são só ordem — não trava.

    Ordem: equilíbrio de FOLGAs → evitar sáb+dom → próprio horário → mais próximo.
    Com protect_balance=True, só tira FOLGA de quem já tem ≥ FOLGAs que o candidato.
    """
    day_entries = [e for e in entries if e["date"] == day]
    target = normalize_schedule_value(candidate.get("day_value") or "")
    if not is_work_day_value(target):
        return None
    offs = _count_folgas(entries)
    cand_offs = offs[candidate["agent"].id]
    cov_set = _coverage_schedule_set(configuration)
    replacements = []
    for other in day_entries:
        if other is candidate or other["agent"].id == candidate["agent"].id:
            continue
        if _locked_day_value(other) or is_work_day_value(other.get("day_value") or ""):
            continue
        schedule = normalize_schedule_value(other.get("schedule") or "")
        if not is_work_day_value(schedule):
            continue
        if not _schedule_types_compatible_for_cover(schedule, target, day):
            continue
        if _skip_as_weekend_cover(other, day):
            continue
        other_offs = offs[other["agent"].id]
        if protect_balance and other_offs < cand_offs:
            continue
        exact = schedule == target
        # Empate de FOLGAs: mesmo slot ou horário próximo (foreign) — proximidade só ordena.
        if protect_balance and other_offs == cand_offs and not (exact or allow_foreign):
            continue
        if not allow_foreign and not exact:
            continue
        # Horário efetivo após ativação
        effective = schedule if exact else target
        if _would_break_rest(entries, other, effective, configuration):
            continue
        if _would_break_consecutive(entries, other, configuration):
            continue
        same_activity = (other.get("activity_name") or "").casefold() == (
            candidate.get("activity_name") or ""
        ).casefold()
        prox = _schedule_proximity_key(target, schedule)
        weekend_pen = _weekend_work_penalty(entries, other, day)
        # Preferência leve: não puxar alguém de outro slot de cobertura.
        other_cov_pen = 1 if (schedule in cov_set and schedule != target) else 0
        replacements.append(
            (
                # 1) Quem tem mais FOLGA cobre (libera quem tem poucas).
                -other_offs,
                # 2) Evita plantão sáb+dom.
                weekend_pen,
                # 3–4) Prioridade suave: próprio horário, depois proximidade.
                0 if exact else 1,
                prox,
                other_cov_pen,
                0 if same_activity else 1,
                other["agent"].user_lan_id or "",
                other,
            )
        )
    if not replacements:
        return None
    replacements.sort(key=lambda item: item[:7])
    for _off, _wp, _exact, _prox, _oc, _act, _lan, other in replacements:
        _activate_substitute(other, target, configuration)
        if not _would_break_coverage(entries, candidate, configuration):
            return other
        _set_generated_folga(other)
    return None


def _try_give_folga(
    entries: list[dict],
    entry: dict,
    configuration: dict,
    *,
    allow_foreign_cover: bool = True,
) -> bool:
    if _locked_day_value(entry) or not is_work_day_value(entry.get("day_value") or ""):
        return False
    if not _would_break_coverage(entries, entry, configuration):
        _set_generated_folga(entry)
        return True
    substitute = _try_activate_cover(
        entries,
        entry["date"],
        entry,
        configuration,
        protect_balance=True,
        allow_foreign=allow_foreign_cover,
    )
    if substitute is None:
        return False
    if not _would_break_coverage(entries, entry, configuration):
        _set_generated_folga(entry)
        return True
    _set_generated_folga(substitute)
    return False


def _folga_baseline_target(configuration: dict, days: list[date]) -> int:
    """Meta de FOLGAs antes da folga adicional configurada."""
    max_consecutive = int(configuration.get("max_consecutive_work_days") or 0)
    approx_target = (
        max(4, len(days) // max(max_consecutive + 1, 2)) if max_consecutive else 4
    )
    pairs = _weekend_pairs(days)
    if pairs:
        approx_target = max(approx_target, len(pairs))
    return approx_target


def _folga_target_with_extra(configuration: dict, days: list[date]) -> int:
    extra_offs = int(configuration.get("extra_offs_per_agent") or 0)
    return _folga_baseline_target(configuration, days) + max(0, extra_offs)


def _folga_day_preference_key(day: date) -> tuple:
    """Menor = melhor para conceder FOLGA: FDS → sex/seg → qui/ter → qua."""
    wd = day.weekday()
    if wd >= 5:
        return (0, wd - 5, day.toordinal())
    if wd in (4, 0):
        return (1, 0 if wd == 4 else 1, day.toordinal())
    if wd in (3, 1):
        return (2, 0 if wd == 3 else 1, day.toordinal())
    return (3, 0, day.toordinal())


def _weekend_pairs(days: list[date]) -> list[tuple[date, date | None]]:
    """Pares (sabado, domingo) presentes no mes."""
    by_ord = {d.toordinal(): d for d in days}
    pairs: list[tuple[date, date | None]] = []
    seen: set[date] = set()
    for d in days:
        if d.weekday() != 5:
            continue
        sun = by_ord.get(d.toordinal() + 1)
        sun_ok = sun if sun and sun.weekday() == 6 else None
        pairs.append((d, sun_ok))
        seen.add(d)
        if sun_ok:
            seen.add(sun_ok)
    for d in days:
        if d.weekday() == 6 and d not in seen:
            pairs.append((d, None))
    return pairs


def _entry_for(agent_entries: list[dict], day: date) -> dict | None:
    for e in agent_entries:
        if e["date"] == day:
            return e
    return None


def _try_give_double_folga(
    entries: list[dict],
    agent_entries: list[dict],
    day_a: date,
    day_b: date | None,
    configuration: dict,
    *,
    allow_foreign_cover: bool = True,
) -> int:
    """Tenta folga dupla (ex.: sab+dom). Retorna quantas FOLGAs concedidas (0-2)."""
    given = 0
    ea = _entry_for(agent_entries, day_a)
    if ea and _try_give_folga(
        entries, ea, configuration, allow_foreign_cover=allow_foreign_cover
    ):
        given += 1
    if day_b is not None:
        eb = _entry_for(agent_entries, day_b)
        if eb and _try_give_folga(
            entries, eb, configuration, allow_foreign_cover=allow_foreign_cover
        ):
            given += 1
    return given


def _apply_balanced_offs(entries: list[dict], configuration: dict) -> None:
    """Distribui FOLGAs priorizando fim de semana, folga dupla e rotacao entre agentes.

    Escala 5x2 (Integral) fica de fora: só folga sáb/dom/feriado, sem folga adicional.
    """
    max_consecutive = int(configuration.get("max_consecutive_work_days") or 0)
    by_day: dict[date, list[dict]] = defaultdict(list)
    by_agent: dict = defaultdict(list)
    for e in entries:
        by_day[e["date"]].append(e)
        by_agent[e["agent"].id].append(e)
    for agent_entries in by_agent.values():
        agent_entries.sort(key=lambda e: e["date"])
    days = sorted(by_day.keys())
    # 5x2 não participa da distribuição de folgas operacionais / adicionais.
    # Noturno participa (pode folgar sem derrubar slots diurnos), mas não cobre dia.
    agent_ids = sorted(
        (
            aid
            for aid, aes in by_agent.items()
            if aes and not _is_5x2_entry(aes[0])
        ),
        key=lambda aid: by_agent[aid][0]["agent"].user_lan_id or "",
    )
    if not days or not agent_ids:
        return

    pairs = _weekend_pairs(days)
    approx_target = _folga_target_with_extra(configuration, days)
    extra_offs = int(configuration.get("extra_offs_per_agent") or 0)

    soft_spread = FOLGA_SOFT_SPREAD
    cov_ids = {
        aid
        for aid in agent_ids
        if _is_coverage_agent(by_agent[aid][0], configuration)
    }

    # Fase 1: fim de semana em rodízio — várias passagens para o substituto
    # (quem já folgou) poder cobrir o slot de cobertura na passagem seguinte.
    for sat, sun in pairs:
        for _round in range(min(8, len(agent_ids) + 2)):
            offs = _count_folgas(entries)
            weekend_folgas = _weekend_folga_counts(entries, agent_ids)
            min_offs = min(offs[a] for a in agent_ids)
            cov_min = min((offs[a] for a in cov_ids), default=min_offs)
            # Alterna: rodada par semeia FOLGA em helpers; ímpar levanta cobertura.
            prefer_cov = _round % 2 == 1
            ordered = sorted(
                agent_ids,
                key=lambda aid: (
                    (0 if aid in cov_ids else 1)
                    if prefer_cov
                    else (0 if aid not in cov_ids else 1),
                    weekend_folgas[aid],
                    offs[aid],
                    by_agent[aid][0]["agent"].user_lan_id or "",
                ),
            )
            round_progress = False
            for aid in ordered:
                if offs[aid] >= approx_target:
                    continue
                if aid not in cov_ids and offs[aid] > cov_min + soft_spread:
                    continue
                if offs[aid] > min_offs + soft_spread and offs[aid] >= approx_target - 1:
                    continue
                agent_entries = by_agent[aid]
                before = offs[aid]
                gained = _try_give_double_folga(
                    entries,
                    agent_entries,
                    sat,
                    sun,
                    configuration,
                    allow_foreign_cover=True,
                )
                if gained == 0 and sun is not None:
                    eb = _entry_for(agent_entries, sun)
                    if eb and _try_give_folga(
                        entries, eb, configuration, allow_foreign_cover=True
                    ):
                        ea = _entry_for(agent_entries, sat)
                        if ea:
                            _try_give_folga(
                                entries, ea, configuration, allow_foreign_cover=True
                            )
                offs = _count_folgas(entries)
                if offs[aid] > before:
                    round_progress = True
                    min_offs = min(offs[a] for a in agent_ids)
                    cov_min = min((offs[a] for a in cov_ids), default=min_offs)
                    break
            if not round_progress:
                break

    # Fase 2: completar meta (inclui folga adicional) priorizando quem tem menos.
    prev_min = -1
    stagnant = 0
    for _pass in range(len(days) * 2 + len(agent_ids) + 3):
        offs = _count_folgas(entries)
        min_offs = min(offs[a] for a in agent_ids)
        if min_offs == prev_min:
            stagnant += 1
            if stagnant >= max(3, len(agent_ids)):
                break
        else:
            stagnant = 0
            prev_min = min_offs
        cov_min = min((offs[a] for a in cov_ids), default=min_offs)
        progress = False
        needy = sorted(
            agent_ids,
            key=lambda aid: (
                0 if aid in cov_ids else 1,
                offs[aid],
                -_current_max_streak(by_agent[aid]),
                by_agent[aid][0]["agent"].user_lan_id or "",
            ),
        )
        for idx, aid in enumerate(needy):
            streak_overflow = bool(
                max_consecutive and _current_max_streak(by_agent[aid]) > max_consecutive
            )
            if offs[aid] >= approx_target and not streak_overflow:
                continue
            if (
                aid not in cov_ids
                and offs[aid] > cov_min + soft_spread + extra_offs
                and not streak_overflow
            ):
                continue
            agent_entries = by_agent[aid]
            work_entries = [
                e
                for e in agent_entries
                if is_work_day_value(e.get("day_value") or "") and not _locked_day_value(e)
            ]
            lan = by_agent[aid][0]["agent"].user_lan_id or str(aid)
            weekend_folgas = _weekend_folga_counts(entries, agent_ids)

            def day_key(
                e: dict,
                _idx=idx,
                _ae=agent_entries,
                _wf=weekend_folgas,
                _aid=aid,
            ) -> tuple:
                d = e["date"]
                wd = d.weekday()
                double_weekend = 1
                if wd >= 5:
                    other = date.fromordinal(d.toordinal() + (1 if wd == 5 else -1))
                    oe = _entry_for(_ae, other)
                    if _works_on(oe):
                        double_weekend = 0
                prev_off = False
                nxt_off = False
                pe = _entry_for(_ae, date.fromordinal(d.toordinal() - 1))
                ne = _entry_for(_ae, date.fromordinal(d.toordinal() + 1))
                if pe and str(pe.get("day_value") or "").upper() == "FOLGA":
                    prev_off = True
                if ne and str(ne.get("day_value") or "").upper() == "FOLGA":
                    nxt_off = True
                double_pref = 0 if (prev_off or nxt_off) else 1
                week_bucket = (d.isocalendar()[1] + _idx) % 7
                return (
                    _wf[_aid] if wd >= 5 else 999,
                    double_weekend,
                    *_folga_day_preference_key(d)[:2],
                    double_pref,
                    _folgas_on_day(entries, d),
                    week_bucket,
                    d.toordinal(),
                )

            for e in sorted(work_entries, key=day_key):
                team_size = len(by_day[e["date"]])
                already = _folgas_on_day(entries, e["date"])
                if already >= max(1, team_size - 1) and not streak_overflow:
                    continue
                if _try_give_folga(
                    entries, e, configuration, allow_foreign_cover=True
                ):
                    d = e["date"]
                    offs_now = _count_folgas(entries)
                    if offs_now[aid] < approx_target:
                        for delta in (-1, 1):
                            neighbor = date.fromordinal(d.toordinal() + delta)
                            ne = _entry_for(agent_entries, neighbor)
                            if ne and _try_give_folga(
                                entries,
                                ne,
                                configuration,
                                allow_foreign_cover=True,
                            ):
                                break
                    progress = True
                    break
            if progress:
                break
        if not progress:
            break

    # Fase 3: quebrar jornadas consecutivas acima do limite (não se aplica a 5x2).
    if max_consecutive:
        for aid in agent_ids:
            agent_entries = by_agent[aid]
            guard = 0
            while _current_max_streak(agent_entries) > max_consecutive and guard < len(days):
                guard += 1
                offs_now = _count_folgas(entries)
                cov_min_now = min((offs_now[a] for a in cov_ids), default=0)
                # Helpers não ganham folga de sequência enquanto cobertura está atrás.
                if (
                    aid not in cov_ids
                    and offs_now[aid] > cov_min_now + soft_spread + extra_offs
                ):
                    break
                work_entries = [
                    e
                    for e in agent_entries
                    if is_work_day_value(e.get("day_value") or "") and not _locked_day_value(e)
                ]
                if not work_entries:
                    break
                best = None
                best_key = None
                cur = 0
                streak_start_idx = 0
                for i, e in enumerate(agent_entries):
                    if is_work_day_value(e.get("day_value") or ""):
                        if cur == 0:
                            streak_start_idx = i
                        cur += 1
                        if cur > max_consecutive:
                            mid = agent_entries[streak_start_idx + max_consecutive // 2]
                            if mid in work_entries:
                                d = mid["date"]
                                key = (0 if d.weekday() >= 5 else 1, d.toordinal())
                                if best_key is None or key < best_key:
                                    best = mid
                                    best_key = key
                    else:
                        cur = 0
                if best is None:
                    best = sorted(
                        work_entries,
                        key=lambda e: (0 if e["date"].weekday() >= 5 else 1, e["date"]),
                    )[0]
                if not _try_give_folga(
                    entries,
                    best,
                    configuration,
                    allow_foreign_cover=True,
                ):
                    break

    # Fase 4: equaliza quantidade de FOLGAs entre agentes operacionais.
    _equalize_folgas(entries, configuration, agent_ids, by_agent, by_day)
    _lift_coverage_agents_offs(entries, configuration, agent_ids, by_agent, by_day)


def _try_transfer_folga_same_day(
    entries: list[dict],
    rich_entry: dict,
    poor_entry: dict,
    configuration: dict,
) -> bool:
    """Rico em FOLGA cobre o slot do pobre; pobre folga (permite horário próximo)."""
    if rich_entry["date"] != poor_entry["date"]:
        return False
    if str(rich_entry.get("day_value") or "").upper() != "FOLGA":
        return False
    if _locked_day_value(rich_entry) or _locked_day_value(poor_entry):
        return False
    if not is_work_day_value(poor_entry.get("day_value") or ""):
        return False

    target = normalize_schedule_value(poor_entry.get("day_value") or "")
    rich_sched = normalize_schedule_value(rich_entry.get("schedule") or "")
    if not is_work_day_value(rich_sched):
        return False
    if not _schedule_types_compatible_for_cover(
        rich_sched, target, rich_entry["date"]
    ):
        return False
    if _skip_as_weekend_cover(rich_entry, rich_entry["date"]):
        return False
    if _would_break_rest(entries, rich_entry, target, configuration):
        return False

    _activate_substitute(rich_entry, target, configuration)
    if not _would_break_coverage(entries, poor_entry, configuration):
        _set_generated_folga(poor_entry)
        return True
    _set_generated_folga(rich_entry)
    return False


def _equalize_folgas(
    entries: list[dict],
    configuration: dict,
    agent_ids: list,
    by_agent: dict,
    by_day: dict[date, list[dict]],
) -> None:
    """Aproxima FOLGAs entre operacionais: sobe o piso e transfere se necessário.

    Folga a mais é ok; diferença grande (ex.: 11 vs 5) não. 5x2 fora de agent_ids.
    """
    if len(agent_ids) < 2:
        return
    soft_spread = FOLGA_SOFT_SPREAD
    days_n = len(by_day)

    max_rounds = min(60, days_n * 2 + len(agent_ids) * 3 + 10)
    for _ in range(max_rounds):
        offs = _count_folgas(entries)
        values = [offs[a] for a in agent_ids]
        lo, hi = min(values), max(values)
        if hi - lo <= soft_spread:
            return

        poor = sorted(
            agent_ids,
            key=lambda aid: (
                offs[aid],
                by_agent[aid][0]["agent"].user_lan_id or "",
            ),
        )
        rich = sorted(
            agent_ids,
            key=lambda aid: (
                -offs[aid],
                by_agent[aid][0]["agent"].user_lan_id or "",
            ),
        )

        # 1) Dar FOLGA a quem tem menos (FDS primeiro; slots de cobertura com substituto).
        gained = False
        for aid in poor:
            if offs[aid] > hi - soft_spread:
                continue
            agent_entries = by_agent[aid]
            foreign = True  # equalização pode usar horário próximo de quem tem mais FOLGA
            work_entries = [
                e
                for e in agent_entries
                if is_work_day_value(e.get("day_value") or "") and not _locked_day_value(e)
            ]

            def day_key(e: dict) -> tuple:
                d = e["date"]
                return (
                    _folgas_on_day(entries, d),
                    *_folga_day_preference_key(d),
                )

            for e in sorted(work_entries, key=day_key):
                team_size = len(by_day[e["date"]])
                if _folgas_on_day(entries, e["date"]) >= max(1, team_size - 1):
                    continue
                if _try_give_folga(
                    entries, e, configuration, allow_foreign_cover=foreign
                ):
                    gained = True
                    break
            if gained:
                break
        if gained:
            continue

        # 2) Transferir FOLGA do rico para o pobre no mesmo dia (mesmo slot / cobertura ok).
        transferred = False
        for rich_aid in rich:
            if offs[rich_aid] < hi:
                break
            rich_entries = by_agent[rich_aid]
            for poor_aid in poor:
                if offs[poor_aid] > lo:
                    break
                if rich_aid == poor_aid:
                    continue
                poor_by_day = {e["date"]: e for e in by_agent[poor_aid]}
                candidates = [
                    e
                    for e in rich_entries
                    if str(e.get("day_value") or "").upper() == "FOLGA"
                    and not _locked_day_value(e)
                    and e["date"] in poor_by_day
                    and is_work_day_value(poor_by_day[e["date"]].get("day_value") or "")
                    and not _locked_day_value(poor_by_day[e["date"]])
                ]

                def transfer_key(e: dict) -> tuple:
                    d = e["date"]
                    rich_sched = normalize_schedule_value(e.get("schedule") or "")
                    poor_slot = normalize_schedule_value(
                        poor_by_day[e["date"]].get("day_value") or ""
                    )
                    prox = _schedule_proximity_key(poor_slot, rich_sched)
                    return (
                        0 if rich_sched == poor_slot else 1,
                        prox,
                        0 if d.weekday() < 5 else 1,
                        d.toordinal(),
                    )

                for rich_e in sorted(candidates, key=transfer_key):
                    if _try_transfer_folga_same_day(
                        entries, rich_e, poor_by_day[rich_e["date"]], configuration
                    ):
                        transferred = True
                        break
                if transferred:
                    break
            if transferred:
                break
        if transferred:
            continue

        # 3) Último recurso: devolver jornada de dia útil de quem está muito acima do piso.
        reduced = False
        floor_keep = lo + soft_spread
        for aid in rich:
            if offs[aid] < hi:
                break
            if offs[aid] <= floor_keep:
                break
            folga_entries = [
                e
                for e in by_agent[aid]
                if str(e.get("day_value") or "").upper() == "FOLGA"
                and not _locked_day_value(e)
            ]

            def take_key(e: dict) -> tuple:
                d = e["date"]
                return (0 if d.weekday() < 5 else 1, -d.toordinal())

            for e in sorted(folga_entries, key=take_key):
                if not _restore_work(e, configuration):
                    continue
                if _would_break_rest(
                    entries, e, e.get("day_value") or "", configuration
                ):
                    _set_generated_folga(e)
                    continue
                reduced = True
                break
            if reduced:
                break
        if not reduced:
            return


def _lift_coverage_agents_offs(
    entries: list[dict],
    configuration: dict,
    agent_ids: list,
    by_agent: dict,
    by_day: dict[date, list[dict]],
) -> None:
    """Garante que quem é slot de cobertura não fique muito abaixo dos helpers."""
    cov_ids = [aid for aid in agent_ids if _is_coverage_agent(by_agent[aid][0], configuration)]
    if not cov_ids:
        return
    soft_spread = FOLGA_SOFT_SPREAD
    extra_offs = int(configuration.get("extra_offs_per_agent") or 0)

    def _clip_helpers(cov_floor: int) -> bool:
        """Corta FOLGA de helpers/noturno acima do piso. Retorna se cortou algo."""
        changed = False
        helpers = [
            aid
            for aid in agent_ids
            if aid not in cov_ids
        ]
        for rich_aid in sorted(
            helpers,
            key=lambda aid: (
                -_count_folgas(entries)[aid],
                by_agent[aid][0]["agent"].user_lan_id or "",
            ),
        ):
            while True:
                offs_now = _count_folgas(entries)
                if offs_now[rich_aid] <= cov_floor + soft_spread + extra_offs:
                    break
                cut = False
                for e in sorted(
                    (
                        x
                        for x in by_agent[rich_aid]
                        if str(x.get("day_value") or "").upper() == "FOLGA"
                        and not _locked_day_value(x)
                        and x.get("source")
                        not in {
                            EscalaGenerationEntry.SOURCE_ABSENCE,
                            EscalaGenerationEntry.SOURCE_HOLIDAY,
                        }
                    ),
                    key=lambda x: (
                        0 if x["date"].weekday() < 5 else 1,
                        -x["date"].toordinal(),
                    ),
                ):
                    if not _restore_work(e, configuration):
                        continue
                    if _would_break_rest(
                        entries, e, e.get("day_value") or "", configuration
                    ):
                        _set_generated_folga(e)
                        continue
                    cut = True
                    changed = True
                    break
                if not cut:
                    break
        return changed

    for _ in range(min(40, len(by_day) * 2 + len(agent_ids) + 5)):
        offs = _count_folgas(entries)
        cov_min = min(offs[a] for a in cov_ids)
        day_helpers = [
            a
            for a in agent_ids
            if a not in cov_ids and not _is_night_agent(by_agent[a][0])
        ]
        if not day_helpers:
            _clip_helpers(cov_min)
            return
        other_max = max(offs[a] for a in day_helpers)
        if other_max - cov_min <= soft_spread:
            _clip_helpers(cov_min)
            return

        poor = sorted(
            cov_ids,
            key=lambda aid: (offs[aid], by_agent[aid][0]["agent"].user_lan_id or ""),
        )
        rich = sorted(
            day_helpers,
            key=lambda aid: (-offs[aid], by_agent[aid][0]["agent"].user_lan_id or ""),
        )
        moved = False

        # 1) Dar FOLGA ao slot de cobertura com substituto próximo.
        for aid in poor:
            if offs[aid] > cov_min:
                break
            for e in sorted(
                (
                    x
                    for x in by_agent[aid]
                    if is_work_day_value(x.get("day_value") or "")
                    and not _locked_day_value(x)
                ),
                key=lambda x: (
                    0 if x["date"].weekday() >= 5 else 1,
                    x["date"].toordinal(),
                ),
            ):
                if _try_give_folga(entries, e, configuration, allow_foreign_cover=True):
                    moved = True
                    break
            if moved:
                break
        if moved:
            continue

        # 2) Transferir FOLGA de helper para o slot.
        for rich_aid in rich:
            if offs[rich_aid] < other_max:
                break
            for poor_aid in poor:
                if offs[poor_aid] > cov_min:
                    break
                poor_by_day = {e["date"]: e for e in by_agent[poor_aid]}
                for rich_e in by_agent[rich_aid]:
                    if str(rich_e.get("day_value") or "").upper() != "FOLGA":
                        continue
                    if _locked_day_value(rich_e):
                        continue
                    poor_e = poor_by_day.get(rich_e["date"])
                    if not poor_e or not is_work_day_value(
                        poor_e.get("day_value") or ""
                    ):
                        continue
                    if _try_transfer_folga_same_day(
                        entries, rich_e, poor_e, configuration
                    ):
                        moved = True
                        break
                if moved:
                    break
            if moved:
                break
        if moved:
            continue

        # 3) Cortar helpers acima da faixa.
        if _clip_helpers(cov_min):
            continue
        return


def _apply_extra_offs_per_agent(entries: list[dict], configuration: dict) -> None:
    """Concede folgas adicionais após cobertura; prefere FDS e dias adjacentes."""
    extra = int(configuration.get("extra_offs_per_agent") or 0)
    if extra <= 0:
        return

    by_agent: dict = defaultdict(list)
    by_day: dict[date, list[dict]] = defaultdict(list)
    for e in entries:
        by_agent[e["agent"].id].append(e)
        by_day[e["date"]].append(e)
    for agent_entries in by_agent.values():
        agent_entries.sort(key=lambda x: x["date"])

    agent_ids = sorted(
        (
            aid
            for aid, aes in by_agent.items()
            if aes and not _is_5x2_entry(aes[0])
        ),
        key=lambda aid: by_agent[aid][0]["agent"].user_lan_id or "",
    )
    if not agent_ids:
        return

    days = sorted(by_day.keys())
    target = _folga_target_with_extra(configuration, days)
    max_rounds = extra * len(agent_ids) * 3 + len(days)

    def _try_grant(aid) -> bool:
        agent_entries = by_agent[aid]
        work_entries = [
            e
            for e in agent_entries
            if is_work_day_value(e.get("day_value") or "")
            and not _locked_day_value(e)
        ]

        def day_key(e: dict) -> tuple:
            d = e["date"]
            return (_folgas_on_day(entries, d), *_folga_day_preference_key(d))

        for e in sorted(work_entries, key=day_key):
            team_size = len(by_day[e["date"]])
            if _folgas_on_day(entries, e["date"]) >= max(1, team_size - 1):
                continue
            if _try_give_folga(entries, e, configuration, allow_foreign_cover=True):
                return True
        return False

    for _ in range(max_rounds):
        offs = _count_folgas(entries)
        needy = [aid for aid in agent_ids if offs[aid] < target]
        if not needy:
            return
        needy.sort(
            key=lambda aid: (offs[aid], by_agent[aid][0]["agent"].user_lan_id or "")
        )
        progress = False
        for aid in needy:
            if _try_grant(aid):
                progress = True
                break
        if not progress:
            return


def _finalize_folga_balance(
    entries: list[dict],
    configuration: dict,
    agent_ids: list,
    by_agent: dict,
    by_day: dict[date, list[dict]],
) -> None:
    """Equalização final após folgas adicionais e cobertura."""
    if len(agent_ids) < 2:
        return
    rounds = max(4, len(agent_ids))
    for _ in range(rounds):
        _equalize_folgas(entries, configuration, agent_ids, by_agent, by_day)
        offs = _count_folgas(entries)
        values = [offs[a] for a in agent_ids]
        if max(values) - min(values) <= FOLGA_SOFT_SPREAD:
            break
        if parse_activity_coverage(configuration):
            _enforce_activity_coverage(entries, configuration)


def _pick_coverage_substitute(
    entries: list[dict],
    day_entries: list[dict],
    day: date,
    gap: str,
    activity: str,
    offs: dict,
    worked: dict,
    configuration: dict,
) -> dict | None:
    """Escolhe substituto: equilíbrio primeiro; próprio horário/proximidade só ordenam."""
    gap_norm = normalize_schedule_value(gap)
    cov_set = _coverage_schedule_set(configuration)
    candidates = []
    for e in day_entries:
        if is_work_day_value(e.get("day_value") or "") or _locked_day_value(e):
            continue
        schedule = normalize_schedule_value(e.get("schedule") or "")
        if not is_work_day_value(schedule):
            continue
        if is_night_shift_crossing(schedule):
            if day.weekday() < 5:
                continue
            effective = gap_norm
        else:
            effective = schedule if schedule == gap_norm else gap_norm
        if _skip_as_weekend_cover(e, day):
            continue
        if _would_break_rest(entries, e, effective, configuration):
            continue
        if _would_break_consecutive(entries, e, configuration):
            continue
        exact = 0 if schedule == gap_norm else 1
        same_activity = 0 if _activity_matches(e, activity) else 1
        prox = _schedule_proximity_key(gap_norm, schedule)
        weekend_pen = _weekend_work_penalty(entries, e, day)
        other_cov_pen = 1 if (schedule in cov_set and schedule != gap_norm) else 0
        candidates.append(
            (
                # Quem tem mais FOLGA / trabalhou menos cobre o buraco.
                -offs[e["agent"].id],
                weekend_pen,
                worked[e["agent"].id],
                # Prioridade suave: próprio horário → proximidade.
                exact,
                prox,
                other_cov_pen,
                same_activity,
                e["agent"].user_lan_id or "",
                e,
            )
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[:8])
    return candidates[0][-1]


def _enforce_activity_coverage(entries: list[dict], configuration: dict) -> None:
    """Cobertura mínima: quem tem mais FOLGA cobre; horário próprio/próximo só ordenam."""
    coverages = parse_activity_coverage(configuration)
    if not coverages:
        return
    by_day: dict[date, list[dict]] = defaultdict(list)
    for entry in entries:
        by_day[entry["date"]].append(entry)
    offs = _count_folgas(entries)
    worked: dict = defaultdict(int)
    for entry in entries:
        if is_work_day_value(entry.get("day_value") or ""):
            worked[entry["agent"].id] += 1
    for day in sorted(by_day.keys()):
        day_entries = by_day[day]
        for cov in coverages:
            if not cov.min_coverage or not weekday_allowed(day, cov.weekdays):
                continue
            for _ in range(len(day_entries) + 1):
                gaps = _schedules_below_min(
                    entries, day, cov.activity, cov.schedules, cov.min_coverage
                )
                if not gaps:
                    break
                gap = min(
                    gaps,
                    key=lambda s: (
                        _schedule_slot_count(entries, day, cov.activity, s),
                        s,
                    ),
                )
                pick = _pick_coverage_substitute(
                    entries,
                    day_entries,
                    day,
                    gap,
                    cov.activity,
                    offs,
                    worked,
                    configuration,
                )
                if pick is None:
                    break
                _activate_substitute(pick, gap, configuration)
                worked[pick["agent"].id] += 1
                offs[pick["agent"].id] = max(0, offs[pick["agent"].id] - 1)


def _normalize_weekend_coverage_slots(
    entries: list[dict],
    configuration: dict,
) -> None:
    """Garante que todo trabalho no FDS use só os slots de cobertura."""
    cov = _coverage_schedule_set(configuration) or {
        normalize_schedule_value(s) for s in DEFAULT_COVERAGE_SCHEDULES
    }
    for e in entries:
        day = e.get("date")
        if not isinstance(day, date) or day.weekday() < 5:
            continue
        if _locked_day_value(e):
            continue
        value = e.get("day_value") or ""
        if not is_work_day_value(value):
            continue
        norm = normalize_schedule_value(value)
        if norm in cov:
            continue
        mapped = _weekend_slot_for_schedule(e.get("schedule") or "", configuration)
        if mapped:
            e["day_value"] = mapped
            e["source"] = EscalaGenerationEntry.SOURCE_GENERATED
        else:
            # Sem slot diurno (ex.: noturno puro) → FOLGA no FDS.
            _set_generated_folga(e)


def _avoid_weekend_double_work(entries: list[dict], configuration: dict) -> None:
    """Evita a mesma pessoa trabalhar sábado e domingo; re-cobre com menor movimento."""
    by_day: dict[date, list[dict]] = defaultdict(list)
    by_agent: dict = defaultdict(list)
    for e in entries:
        by_day[e["date"]].append(e)
        by_agent[e["agent"].id].append(e)
    for agent_entries in by_agent.values():
        agent_entries.sort(key=lambda e: e["date"])
    pairs = _weekend_pairs(sorted(by_day.keys()))
    if not pairs:
        return

    for sat, sun in pairs:
        if sun is None:
            continue
        double_workers = []
        for aid, agent_entries in by_agent.items():
            if agent_entries and _is_5x2_entry(agent_entries[0]):
                continue
            ea = _entry_for(agent_entries, sat)
            eb = _entry_for(agent_entries, sun)
            if _works_on(ea) and _works_on(eb):
                double_workers.append((aid, ea, eb))
        double_workers.sort(
            key=lambda item: by_agent[item[0]][0]["agent"].user_lan_id or ""
        )
        for _aid, ea, eb in double_workers:
            # Prefere folgar domingo; se não der, sábado. 1 pessoa + 1 substituto.
            for entry in (eb, ea):
                if _try_give_folga(entries, entry, configuration):
                    break

    # Garante cobertura dos 3 slots após o ajuste.
    _enforce_activity_coverage(entries, configuration)

    # Última tentativa: quem ainda trabalha os 2 dias troca com FOLGA que não trabalha o par.
    offs = _count_folgas(entries)
    worked: dict = defaultdict(int)
    for entry in entries:
        if is_work_day_value(entry.get("day_value") or ""):
            worked[entry["agent"].id] += 1
    for sat, sun in pairs:
        if sun is None:
            continue
        for aid, agent_entries in list(by_agent.items()):
            if agent_entries and _is_5x2_entry(agent_entries[0]):
                continue
            ea = _entry_for(agent_entries, sat)
            eb = _entry_for(agent_entries, sun)
            if not (_works_on(ea) and _works_on(eb)):
                continue
            swapped = False
            for entry in (eb, ea):
                day = entry["date"]
                target = normalize_schedule_value(entry.get("day_value") or "")
                if not is_work_day_value(target):
                    continue
                pick = _pick_coverage_substitute(
                    entries,
                    by_day[day],
                    day,
                    target,
                    entry.get("activity_name") or "",
                    offs,
                    worked,
                    configuration,
                )
                if pick is None:
                    continue
                if _weekend_work_penalty(entries, pick, day):
                    continue
                # Simula: 1 movimento (substituto entra, double worker sai).
                prev_pick = pick.get("day_value")
                prev_entry = entry.get("day_value")
                _activate_substitute(pick, target, configuration)
                _set_generated_folga(entry)
                still_ok = True
                for cov in parse_activity_coverage(configuration):
                    if not cov.min_coverage or not weekday_allowed(day, cov.weekdays):
                        continue
                    if _schedules_below_min(
                        entries, day, cov.activity, cov.schedules, cov.min_coverage
                    ):
                        still_ok = False
                        break
                if still_ok:
                    offs[pick["agent"].id] = max(0, offs[pick["agent"].id] - 1)
                    offs[entry["agent"].id] = offs.get(entry["agent"].id, 0) + 1
                    worked[pick["agent"].id] += 1
                    swapped = True
                    break
                pick["day_value"] = prev_pick
                entry["day_value"] = prev_entry
            if swapped:
                continue

    _enforce_activity_coverage(entries, configuration)


def _balance_weekend_rotation(
    entries: list[dict],
    configuration: dict,
    agent_ids: list,
    by_agent: dict,
) -> bool:
    """Evita um agente folgar em todos os fins de semana enquanto outros cobrem o FDS."""
    operational = [
        aid
        for aid in agent_ids
        if by_agent.get(aid)
        and not _is_5x2_entry(by_agent[aid][0])
    ]
    if len(operational) < 2:
        return False

    pairs = _weekend_pairs(sorted({e["date"] for e in entries}))
    weekend_day_count = sum(1 + (1 if sun else 0) for sat, sun in pairs)
    if weekend_day_count < 4:
        return False

    wf = _weekend_folga_counts(entries, operational)
    ww = _weekend_work_counts(entries, operational)
    if max(ww.values()) == 0:
        return False

    for aid in sorted(
        operational,
        key=lambda a: (-wf[a], ww[a], by_agent[a][0]["agent"].user_lan_id or ""),
    ):
        if wf[aid] < weekend_day_count - 1 or ww[aid] > 0:
            continue
        restored = 0
        target_work = max(1, weekend_day_count // max(2, len(operational)))
        for sat, sun in pairs:
            for day in (sat, sun):
                if day is None:
                    continue
                entry = _entry_for(by_agent[aid], day)
                if not entry or str(entry.get("day_value") or "").upper() != "FOLGA":
                    continue
                if _locked_day_value(entry):
                    continue
                if not _restore_work(entry, configuration):
                    continue
                if _would_break_coverage(entries, entry, configuration):
                    _set_generated_folga(entry)
                    continue
                restored += 1
                wf[aid] -= 1
                ww[aid] += 1
                if ww[aid] >= target_work:
                    break
            if ww[aid] >= target_work:
                break
        if restored:
            _enforce_activity_coverage(entries, configuration)
            return True
    return False


def _build_entry_dicts(
    *,
    reference_month: date,
    configuration: dict,
) -> tuple[list[dict], list[dict], int]:
    target = configuration.get("target_job_title")
    eligible, warnings = load_eligible_agent_days(
        reference_month=reference_month,
        target_job_title=target,
    )
    excluded_ids = {
        str(item).strip()
        for item in (configuration.get("excluded_agent_ids") or [])
        if str(item or "").strip()
    }
    if excluded_ids:
        eligible = [
            item for item in eligible if str(item.agent.id) not in excluded_ids
        ]
    holidays = load_holidays_for_month(reference_month)
    absence_codes = {c.upper() for c in (configuration.get("absence_codes") or [])}
    allow_night = bool(configuration.get("allow_night_shift"))

    agent_ids = {item.agent.id for item in eligible}
    known_absences: dict[tuple, str] = {}
    if agent_ids:
        for agent_id, data, dia in Escala.objects.filter(
            agent_id__in=agent_ids,
            data__year=reference_month.year,
            data__month=reference_month.month,
        ).values_list("agent_id", "data", "dia_escala"):
            code = str(dia or "").strip().upper()
            if code in {"FERIAS", "AFASTADO"} or (
                code in absence_codes and code not in {"FOLGA", "BH"}
            ):
                known_absences[(agent_id, data)] = code

    stats: dict = defaultdict(
        lambda: {"worked": 0, "weekend": 0, "holiday": 0, "days_since_off": 99}
    )
    entries: list[dict] = []

    by_day: dict[date, list] = defaultdict(list)
    for item in eligible:
        by_day[item.day].append(item)

    for day in sorted(by_day.keys()):
        day_items = sorted(
            by_day[day],
            key=lambda item: _balance_key(
                stats[item.agent.id], item.agent.user_lan_id or ""
            ),
        )

        for item in day_items:
            history = item.history
            agent = item.agent
            schedule = normalize_schedule_value(history.journey or "")
            if schedule and not is_time_range_schedule(schedule):
                schedule = normalize_time_schedule(history.journey or "")
            if schedule and is_night_shift_crossing(schedule) and not allow_night:
                pass

            journey_shift = (history.journey_shift or "").strip()
            agent_is_5x2 = is_5x2_schedule(schedule, journey_shift)

            location_name = history.location or ""
            holiday_ov = holiday_override_for(
                day, location_name, configuration, holidays
            )
            source = EscalaGenerationEntry.SOURCE_GENERATED
            day_value = schedule
            holiday_work = False
            fixed_weekend_off = False

            known = known_absences.get((agent.id, day))
            if known:
                day_value = known
                source = EscalaGenerationEntry.SOURCE_ABSENCE
            elif holiday_ov:
                treatment = (holiday_ov.get("treatment") or "off").casefold()
                operate = bool(holiday_ov.get("operate"))
                if treatment == "off" or not operate:
                    day_value = "FOLGA"
                    source = EscalaGenerationEntry.SOURCE_HOLIDAY
                else:
                    holiday_work = True
                    if day.weekday() >= 5:
                        day_value = (
                            _weekend_slot_for_schedule(schedule, configuration)
                            or schedule
                        )
                    else:
                        day_value = schedule
            elif day.weekday() >= 5 and agent_is_5x2:
                # Escala 5x2 (Integral): folga fixa em todos os fins de semana.
                day_value = "FOLGA"
                source = EscalaGenerationEntry.SOURCE_GENERATED
                fixed_weekend_off = True
            elif day.weekday() >= 5:
                # FDS: apenas os 3 horários de cobertura (jornada → slot mais próximo).
                mapped = _weekend_slot_for_schedule(schedule, configuration)
                day_value = mapped if mapped else "FOLGA"
                source = EscalaGenerationEntry.SOURCE_GENERATED
            else:
                day_value = schedule or ""

            if is_work_day_value(day_value):
                stats[agent.id]["worked"] += 1
                if day.weekday() >= 5:
                    stats[agent.id]["weekend"] += 1
                if holiday_work:
                    stats[agent.id]["holiday"] += 1
                stats[agent.id]["days_since_off"] = 0
            else:
                stats[agent.id]["days_since_off"] = (
                    stats[agent.id].get("days_since_off", 0) + 1
                )

            entries.append(
                {
                    "agent": agent,
                    "date": day,
                    "leader": history.leader,
                    "team": history.team or "",
                    "sector": history.team_sector or "",
                    "activity_name": history.job_activity or "",
                    "location_name": location_name,
                    "schedule": schedule,
                    "journey_shift": journey_shift,
                    "is_5x2": agent_is_5x2,
                    "fixed_weekend_off": fixed_weekend_off,
                    "day_value": day_value,
                    "source": source,
                    "holiday_work": holiday_work,
                    "observation": (
                        "5x2 — folga fixa no fim de semana"
                        if fixed_weekend_off
                        else item.warning
                    ),
                }
            )

    # Distribui folgas, equaliza, aplica cobertura, evita plantão sáb+dom, revalida.
    _apply_balanced_offs(entries, configuration)
    _enforce_activity_coverage(entries, configuration)
    _avoid_weekend_double_work(entries, configuration)
    _enforce_activity_coverage(entries, configuration)
    # Equalização final após cobertura (5x2 continua fora).
    by_agent_final: dict = defaultdict(list)
    by_day_final: dict[date, list[dict]] = defaultdict(list)
    for e in entries:
        by_agent_final[e["agent"].id].append(e)
        by_day_final[e["date"]].append(e)
    for aes in by_agent_final.values():
        aes.sort(key=lambda x: x["date"])
    op_ids = sorted(
        (
            aid
            for aid, aes in by_agent_final.items()
            if aes and not _is_5x2_entry(aes[0])
        ),
        key=lambda aid: by_agent_final[aid][0]["agent"].user_lan_id or "",
    )
    for _ in range(2):
        _equalize_folgas(entries, configuration, op_ids, by_agent_final, by_day_final)
        _lift_coverage_agents_offs(entries, configuration, op_ids, by_agent_final, by_day_final)
        _enforce_activity_coverage(entries, configuration)
    for _ in range(max(3, len(op_ids))):
        if not _balance_weekend_rotation(
            entries, configuration, op_ids, by_agent_final
        ):
            break
    _normalize_weekend_coverage_slots(entries, configuration)
    _enforce_activity_coverage(entries, configuration)
    _apply_extra_offs_per_agent(entries, configuration)
    _enforce_activity_coverage(entries, configuration)
    _finalize_folga_balance(
        entries, configuration, op_ids, by_agent_final, by_day_final
    )
    _normalize_weekend_coverage_slots(entries, configuration)
    agents_considered = len({e["agent"].id for e in entries})
    return entries, warnings, agents_considered


@transaction.atomic
def generate_preview(
    *,
    reference_month: date,
    configuration: dict | None = None,
    user=None,
    run: EscalaGenerationRun | None = None,
) -> EscalaGenerationRun:
    """Gera (ou regenera) a prévia mensal de forma determinística."""
    month = reference_month.replace(day=1)
    config = merge_configuration(configuration)

    if run is None:
        run = EscalaGenerationRun.objects.create(
            reference_month=month,
            status=EscalaGenerationRun.STATUS_PROCESSING,
            configuration=config,
            created_by=user,
        )
    else:
        if run.status == EscalaGenerationRun.STATUS_PUBLISHED:
            raise ValueError("Não é possível regenerar uma execução já publicada.")
        run.status = EscalaGenerationRun.STATUS_PROCESSING
        run.configuration = config
        run.failure_detail = ""
        run.save(update_fields=["status", "configuration", "failure_detail"])
        run.entries.all().delete()
        run.conflicts.all().delete()

    try:
        entry_dicts, warnings, agents_considered = _build_entry_dicts(
            reference_month=month,
            configuration=config,
        )

        activity_cache: dict[str, JobActivity | None] = {}
        location_cache: dict[str, Location | None] = {}
        entry_objs: list[EscalaGenerationEntry] = []

        for data in entry_dicts:
            act_name = data["activity_name"]
            if act_name not in activity_cache:
                activity_cache[act_name] = _resolve_job_activity(act_name)
            loc_name = data["location_name"]
            if loc_name not in location_cache:
                location_cache[loc_name] = _resolve_location(loc_name)

            job_activity = activity_cache[act_name]
            location = location_cache[loc_name]
            data["job_activity"] = job_activity
            data["location"] = location

            entry_objs.append(
                EscalaGenerationEntry(
                    run=run,
                    agent=data["agent"],
                    date=data["date"],
                    leader=data["leader"],
                    job_activity=job_activity,
                    location=location,
                    team=data["team"],
                    sector=data["sector"],
                    schedule=data["schedule"],
                    day_value=data["day_value"],
                    source=data["source"],
                    observation=data.get("observation") or "",
                )
            )

        EscalaGenerationEntry.objects.bulk_create(entry_objs, batch_size=500)
        entry_by_key = {
            (obj.agent_id, obj.date): obj
            for obj in run.entries.select_related("agent").all()
        }

        drafts = collect_conflicts(
            entries=entry_dicts,
            configuration=config,
            reference_month=month,
        )
        conflict_objs: list[EscalaGenerationConflict] = []
        conflicted_keys: set[tuple] = set()
        for draft in drafts:
            entry = entry_by_key.get(draft.entry_key) if draft.entry_key else None
            if draft.entry_key:
                conflicted_keys.add(draft.entry_key)
            conflict_objs.append(
                EscalaGenerationConflict(
                    run=run,
                    entry=entry,
                    agent_id=draft.agent_id,
                    date=draft.date,
                    activity_name=draft.activity_name,
                    conflict_type=draft.conflict_type,
                    severity=draft.severity,
                    message=draft.message,
                    details=draft.details or {},
                )
            )
        EscalaGenerationConflict.objects.bulk_create(conflict_objs, batch_size=500)

        if conflicted_keys:
            to_flag = [entry_by_key[k].id for k in conflicted_keys if k in entry_by_key]
            EscalaGenerationEntry.objects.filter(id__in=to_flag).update(has_conflict=True)

        for data in entry_dicts:
            data["has_conflict"] = (data["agent"].id, data["date"]) in conflicted_keys

        analysis = build_preview_analysis(
            entry_dicts,
            conflicts=drafts,
            coverage_schedules=config.get("coverage_schedules") or [],
        )
        run.agents_considered = agents_considered
        run.entries_generated = len(entry_objs)
        run.conflicts_count = len(conflict_objs)
        run.summary = {
            "warnings": warnings,
            "blocking_conflicts": analysis["blocking_conflicts"],
            "warning_conflicts": analysis["warning_conflicts"],
            "folga_count": analysis["folga_days"],
            "folga_min": analysis["folga_min"],
            "folga_max": analysis["folga_max"],
            "folga_spread": analysis["folga_spread"],
            "work_days": analysis["work_days"],
            "weekend_work": analysis["weekend_work"],
            "weekend_folga": analysis["weekend_folga"],
            "substitutions": analysis["substitutions"],
            "agents_5x2": analysis["agents_5x2"],
            "by_schedule": analysis["by_schedule"],
            "weekend_coverage": analysis["weekend_coverage"],
            "agents_detail": analysis["agents_detail"],
            "conflicts_by_type": analysis["conflicts_by_type"],
            "priority_order": config.get("priority_order") or [],
        }
        run.status = EscalaGenerationRun.STATUS_READY
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "agents_considered",
                "entries_generated",
                "conflicts_count",
                "summary",
                "status",
                "finished_at",
            ]
        )
        return run
    except Exception as exc:
        run.status = EscalaGenerationRun.STATUS_FAILED
        run.failure_detail = str(exc)
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "failure_detail", "finished_at"])
        raise
