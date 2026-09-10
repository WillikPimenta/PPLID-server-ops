from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

from django.utils import timezone

from apps.auditoria.models import AuditoriaAtividade


def _make_aware(value: datetime) -> datetime:
    if timezone.is_naive(value):
        return timezone.make_aware(value, timezone.get_current_timezone())
    return value


def parse_data_recepcao(value: Any) -> tuple[datetime | None, str | None]:
    """Parse data/hora de recepção. Aceita AAAA-MM-DD ou AAAA-MM-DDTHH:MM[:SS]."""
    if value is None or value == "":
        return None, "A data de recepção da demanda é obrigatória."
    if isinstance(value, datetime):
        return _make_aware(value), None
    if isinstance(value, date):
        return _make_aware(datetime.combine(value, time.min)), None
    if not isinstance(value, str):
        return None, "Informe a data de recepção no formato DD/MM/AAAA, HH:MM."

    cleaned = value.strip()
    if not cleaned:
        return None, "A data de recepção da demanda é obrigatória."

    normalized = cleaned.replace(" ", "T", 1)
    try:
        if "T" in normalized:
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = _make_aware(dt)
            return dt, None
        parsed_date = date.fromisoformat(normalized[:10])
        return _make_aware(datetime.combine(parsed_date, time.min)), None
    except ValueError:
        return None, "Informe a data de recepção no formato DD/MM/AAAA, HH:MM."


def recepcao_start(atividade: AuditoriaAtividade) -> datetime | None:
    if not atividade.data_recepcao:
        return None
    start = atividade.data_recepcao
    if isinstance(start, date) and not isinstance(start, datetime):
        start = datetime.combine(start, time.min)
    return _make_aware(start)


def compute_sla_seconds(atividade: AuditoriaAtividade, *, now: datetime | None = None) -> int | None:
    start = recepcao_start(atividade)
    if start is None:
        return None
    end = atividade.encerrado_em or now or timezone.now()
    return max(0, int((end - start).total_seconds()))


def format_sla_label(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}min")
    return " ".join(parts)


def _coerce_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _day_start(value: date) -> datetime:
    return _make_aware(datetime.combine(value, time.min))


def _day_end(value: date) -> datetime:
    return _make_aware(datetime.combine(value, time.max.replace(microsecond=0)))


def average_sla_seconds(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(sum(values) / len(values)))


def compute_controle_sla_seconds(
    dados: dict | None,
    *,
    situacao: str = "",
    now: datetime | None = None,
) -> int | None:
    """
    SLA de remoção/solicitação:
    - início: data_abertura (fallback data_criacao)
    - fim: data_retorno / data_conclusao se finalizada; senão agora
    """
    payload = dados if isinstance(dados, dict) else {}
    start_date = _coerce_date(payload.get("data_abertura") or payload.get("data_criacao"))
    if start_date is None:
        return None

    start = _day_start(start_date)
    is_finalizada = (situacao or str(payload.get("situacao") or "")).strip().casefold() == "finalizada"
    end_date = _coerce_date(
        payload.get("data_retorno")
        or payload.get("data_conclusao")
        or payload.get("data_retorno / higienização")
    )
    if is_finalizada and end_date is not None:
        end = _day_end(end_date)
    else:
        end = now or timezone.now()
    return max(0, int((end - start).total_seconds()))
