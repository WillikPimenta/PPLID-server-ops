"""SLA de reinspeção: prazo D+2 em dias úteis (exclui fim de semana e feriados nacionais)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from django.utils import timezone

# Feriados nacionais (e ponto facultativo nacional típico) — sem estaduais/municipais.
# Carnaval / Corpus Christi entram como nacionais operacionais (calendário federal).
_NATIONAL_FIXED: tuple[tuple[int, int], ...] = (
    (1, 1),  # Confraternização Universal
    (4, 21),  # Tiradentes
    (5, 1),  # Dia do Trabalho
    (9, 7),  # Independência
    (10, 12),  # Nossa Senhora Aparecida
    (11, 2),  # Finados
    (11, 15),  # Proclamação da República
    (11, 20),  # Consciência Negra
    (12, 25),  # Natal
)

SLA_BUSINESS_DAYS = 2
BRASILIA_TIME_ZONE = ZoneInfo("America/Sao_Paulo")

SLA_FAROL_D = "d"
SLA_FAROL_D1 = "d1"
SLA_FAROL_D2 = "d2"
SLA_FAROL_ESTOURADO = "estourado"

SLA_FAROL_LABELS = {
    SLA_FAROL_D: "D",
    SLA_FAROL_D1: "D+1",
    SLA_FAROL_D2: "D+2",
    SLA_FAROL_ESTOURADO: "Estourado",
}


def brasilia_date(value: date | datetime) -> date:
    """Extrai somente o dia civil de Brasília, sem considerar o horário."""
    if not isinstance(value, datetime):
        return value
    if timezone.is_aware(value):
        return value.astimezone(BRASILIA_TIME_ZONE).date()
    return value.date()


def format_brasilia_datetime(value: date | datetime | str | None) -> str:
    """Formata data/hora no fuso de Brasília: dd/mm/aaaa hh:mm:ss."""
    if not value:
        return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        value = parsed
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time(), tzinfo=BRASILIA_TIME_ZONE)
    if timezone.is_naive(value):
        value = timezone.make_aware(value, BRASILIA_TIME_ZONE)
    return value.astimezone(BRASILIA_TIME_ZONE).strftime("%d/%m/%Y %H:%M:%S")


def _easter_sunday(year: int) -> date:
    """Algoritmo de Meeus/Jones/Butcher (gregoriano)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month = (h + el - 7 * m + 114) // 31
    day = ((h + el - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def national_holidays_for_year(year: int) -> set[date]:
    holidays = {date(year, month, day) for month, day in _NATIONAL_FIXED}
    easter = _easter_sunday(year)
    holidays.add(easter - timedelta(days=48))  # Segunda de Carnaval
    holidays.add(easter - timedelta(days=47))  # Terça de Carnaval
    holidays.add(easter - timedelta(days=2))  # Sexta-feira Santa
    holidays.add(easter + timedelta(days=60))  # Corpus Christi
    return holidays


def _holiday_cache() -> dict[int, set[date]]:
    cache = getattr(national_holidays_for_year, "_cache", None)
    if cache is None:
        cache = {}
        setattr(national_holidays_for_year, "_cache", cache)
    return cache


def is_national_holiday(day: date) -> bool:
    cache = _holiday_cache()
    year_set = cache.get(day.year)
    if year_set is None:
        year_set = national_holidays_for_year(day.year)
        cache[day.year] = year_set
    return day in year_set


def is_business_day(day: date) -> bool:
    """Dia útil operacional: seg–sex, exceto feriado nacional."""
    if day.weekday() >= 5:
        return False
    return not is_national_holiday(day)


def add_business_days(start: date, days: int) -> date:
    """Avança ``days`` dias úteis a partir de ``start`` (D+0 = start)."""
    if days <= 0:
        return start
    current = start
    added = 0
    while added < days:
        current += timedelta(days=1)
        if is_business_day(current):
            added += 1
    return current


def subtract_business_days(start: date, days: int) -> date:
    """Retrocede ``days`` dias úteis a partir de ``start``."""
    if days <= 0:
        return start
    current = start
    removed = 0
    while removed < days:
        current -= timedelta(days=1)
        if is_business_day(current):
            removed += 1
    return current


def sla_panel_reference_dates(*, as_of: date | datetime | None = None) -> dict[str, str]:
    """Datas de referência exibidas abaixo dos cards de SLA (painel de distribuição)."""
    ref = brasilia_date(as_of or timezone.now())
    return {
        "d": ref.isoformat(),
        "d1": add_business_days(ref, 1).isoformat(),
        "d2": add_business_days(ref, 2).isoformat(),
        "estourado": "",
    }


def data_final_sla(data_contestacao: date | datetime | None) -> date | None:
    if data_contestacao is None:
        return None
    start = brasilia_date(data_contestacao)
    return add_business_days(start, SLA_BUSINESS_DAYS)


def business_day_offset(start: date, current: date) -> int:
    """Quantos dias úteis decorreram de ``start`` até ``current`` (0 no próprio D)."""
    if current <= start:
        return 0
    offset = 0
    day = start
    while day < current:
        day += timedelta(days=1)
        if is_business_day(day):
            offset += 1
    return offset


def sla_farol(
    data_contestacao: date | datetime | None,
    *,
    as_of: date | datetime | None = None,
) -> str | None:
    """
    Farol do protocolo:
    - D / D+1: dentro do prazo (verde)
    - D+2: último dia útil do prazo (amarelo)
    - estourado: após a data final (vermelho)
    """
    if data_contestacao is None:
        return None
    start = brasilia_date(data_contestacao)
    if as_of is None:
        ref = timezone.now().astimezone(BRASILIA_TIME_ZONE).date()
    else:
        ref = brasilia_date(as_of)

    deadline = add_business_days(start, SLA_BUSINESS_DAYS)
    if ref > deadline:
        return SLA_FAROL_ESTOURADO
    offset = business_day_offset(start, ref)
    if offset <= 0:
        return SLA_FAROL_D
    if offset == 1:
        return SLA_FAROL_D1
    return SLA_FAROL_D2


def sla_business_days_elapsed(
    data_contestacao: date | datetime | None,
    *,
    as_of: date | datetime | None = None,
) -> int | None:
    """Retorna D como 0 e continua contando os dias úteis após D+2."""
    if data_contestacao is None:
        return None
    start = brasilia_date(data_contestacao)
    ref = (
        timezone.now().astimezone(BRASILIA_TIME_ZONE).date()
        if as_of is None
        else brasilia_date(as_of)
    )
    return business_day_offset(start, ref)


def sla_farol_label(farol: str | None) -> str:
    if not farol:
        return ""
    return SLA_FAROL_LABELS.get(farol, farol)


def as_of_for_falha(falha, *, now: datetime | None = None) -> date:
    """Congela o farol na data de resposta/auditoria quando o protocolo já foi finalizado."""
    now = now or timezone.now()
    today = brasilia_date(now)

    status = getattr(falha, "analise_status", None) or ""
    has_resultado = bool((getattr(falha, "status", None) or "").strip())
    response_at = getattr(falha, "data_resposta", None) or getattr(falha, "analise_concluida_em", None)
    finalized = status == "concluido" or has_resultado or bool(response_at)
    if not finalized:
        return today

    for candidate in (
        getattr(falha, "data_resposta", None),
        getattr(falha, "analise_concluida_em", None),
        getattr(falha, "data_contestacao", None),
        getattr(falha, "data_analise", None),
    ):
        if not candidate:
            continue
        if isinstance(candidate, (date, datetime)):
            return brasilia_date(candidate)
    return today


def is_falha_aberta_para_sla(falha) -> bool:
    """Protocolos finalizados/respondidos saem do monitoramento de SLA."""
    status = getattr(falha, "analise_status", None) or ""
    if status == "concluido":
        return False
    if getattr(falha, "data_resposta", None):
        return False
    if (getattr(falha, "status", None) or "").strip():
        return False
    return True


def resolve_agente_names(matriculas: Iterable[str]) -> dict[str, str]:
    """Resolve matrícula (LAN) → nome do agente (workforce, fallback falhas)."""
    keys = {(m or "").strip().lower() for m in matriculas if (m or "").strip()}
    if not keys:
        return {}

    names: dict[str, str] = {}
    if "sistema" in keys:
        names["sistema"] = "Sistema"

    candidates: set[str] = set()
    for key in keys:
        if key == "sistema":
            continue
        candidates.add(key)
        candidates.add(key.upper())
        candidates.add(key.lower())

    try:
        from apps.workforce.models import Agent

        for agent in Agent.objects.filter(user_lan_id__in=candidates).only("user_lan_id", "full_name"):
            lan = (agent.user_lan_id or "").strip().lower()
            name = (agent.full_name or "").strip()
            if lan and name:
                names[lan] = name
    except Exception:
        pass

    missing = [k for k in keys if k not in names]
    if missing:
        try:
            from apps.falhas_criticas.models import FalhasAgent

            for agent in FalhasAgent.objects.filter(matricula_norm__in=missing).only("matricula_norm", "name"):
                lan = (agent.matricula_norm or "").strip().lower()
                name = (agent.name or "").strip()
                if lan and name and lan not in names:
                    names[lan] = name
        except Exception:
            pass

    return names


def build_sla_kpis_from_falhas(
    falhas: Iterable,
    *,
    now: datetime | None = None,
    data_origem_field: str = "data_contestacao",
) -> dict:
    """Conta protocolos abertos por farol (distinct por protocolo)."""
    now = now or timezone.now()
    today = brasilia_date(now)
    best: dict[str, date] = {}
    for falha in falhas:
        if not is_falha_aberta_para_sla(falha):
            continue
        proto = (getattr(falha, "protocolo", None) or "").strip() or f"id:{getattr(falha, 'id', '')}"
        data_origem = getattr(falha, data_origem_field, None)
        if not data_origem:
            continue
        start = brasilia_date(data_origem)
        prev = best.get(proto)
        if prev is None or start < prev:
            best[proto] = start

    counts = {
        SLA_FAROL_D: 0,
        SLA_FAROL_D1: 0,
        SLA_FAROL_D2: 0,
        SLA_FAROL_ESTOURADO: 0,
    }
    for start in best.values():
        farol = sla_farol(start, as_of=today)
        if farol in counts:
            counts[farol] += 1

    total = sum(counts.values())
    return {
        "em_monitoramento": total,
        "d": counts[SLA_FAROL_D],
        "d1": counts[SLA_FAROL_D1],
        "d2": counts[SLA_FAROL_D2],
        "estourado": counts[SLA_FAROL_ESTOURADO],
    }


def empty_sla_kpis() -> dict:
    return {
        "em_monitoramento": 0,
        "d": 0,
        "d1": 0,
        "d2": 0,
        "estourado": 0,
    }
