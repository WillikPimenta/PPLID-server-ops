"""Aplicação de solicitações de troca aprovadas na escala publicada."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from apps.workforce.models import Agent

from ..models import Escala, ScheduleRequest
from .escala_edit import PublishedEscalaEditService


class SwapApplicationError(ValueError):
    """A solicitação aprovada não pôde ser aplicada na escala."""


def _agents_by_lan(*lan_ids: str) -> dict[str, Agent]:
    normalized = {str(value or "").strip().lower() for value in lan_ids if value}
    if not normalized:
        return {}

    query = Q()
    for lan_id in normalized:
        query |= Q(user_lan_id__iexact=lan_id)
    return {
        agent.user_lan_id.strip().lower(): agent
        for agent in Agent.objects.filter(query).order_by("pk")
    }


def _locked_escala(agent: Agent, target_date) -> Escala:
    PublishedEscalaEditService.get_or_create_escala_for_agent(agent, target_date)
    return Escala.objects.select_for_update().get(agent=agent, data=target_date)


def _published_day_value(escala: Escala) -> str:
    return str(escala.dia_escala or escala.horario or "").strip()


def _apply_peer_swap(request: ScheduleRequest) -> None:
    agents = _agents_by_lan(request.agent_lan_id, request.agent_lan_id_2)
    agent = agents.get((request.agent_lan_id or "").strip().lower())
    partner = agents.get((request.agent_lan_id_2 or "").strip().lower())
    if not agent or not partner:
        raise SwapApplicationError("Não foi possível localizar os agentes da troca.")

    escala_by_agent_id = {
        current_agent.pk: _locked_escala(current_agent, request.date_swap)
        for current_agent in sorted((agent, partner), key=lambda item: str(item.pk))
    }
    escala_agent = escala_by_agent_id[agent.pk]
    escala_partner = escala_by_agent_id[partner.pk]
    agent_day = _published_day_value(escala_agent)
    partner_day = _published_day_value(escala_partner)
    if not agent_day or not partner_day:
        raise SwapApplicationError("Não foi possível identificar as escalas dos dois agentes.")

    escala_agent.dia_escala = partner_day
    escala_partner.dia_escala = agent_day
    escala_agent.save(update_fields=["dia_escala", "updated_at"])
    escala_partner.save(update_fields=["dia_escala", "updated_at"])

    # Os valores já pertencem à escala publicada. A chamada sem dia_escala
    # apenas sincroniza Schedule e ScheduleToday com a troca realizada.
    PublishedEscalaEditService.apply_changes(escala_agent)
    PublishedEscalaEditService.apply_changes(escala_partner)


def _apply_single_agent_change(request: ScheduleRequest, day_value: str) -> None:
    agents = _agents_by_lan(request.agent_lan_id)
    agent = agents.get((request.agent_lan_id or "").strip().lower())
    if not agent:
        raise SwapApplicationError("Não foi possível localizar o agente da solicitação.")
    escala = _locked_escala(agent, request.date_swap)
    PublishedEscalaEditService.apply_changes(escala, dia_escala=day_value)


@transaction.atomic
def apply_approved_swap(request: ScheduleRequest) -> None:
    if not request.date_swap:
        raise SwapApplicationError("A solicitação não possui data de troca.")
    if request.swap_kind == "peer":
        _apply_peer_swap(request)
        return
    if request.swap_kind == "shift_schedule":
        if not request.new_journey:
            raise SwapApplicationError("A solicitação não possui o novo horário.")
        _apply_single_agent_change(request, request.new_journey)
        return
    if request.swap_kind == "shift_bh":
        _apply_single_agent_change(request, "BH")
        return
    raise SwapApplicationError("Tipo de troca inválido.")
