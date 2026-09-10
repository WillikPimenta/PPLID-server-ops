"""Aplica mudança de ciclo (vigência) no headcount."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction

from apps.workforce.models import Agent, AgentHistory
from apps.workforce.services.journey_shift import (
    EXTERNAL_MOVEMENT_CHOICES,
    TERMINATION_MOVEMENT_CHOICES,
    parse_hms_to_hours,
    resolve_journey_shift,
)

CYCLE_COMPARE_FIELDS = (
    "team",
    "job_title",
    "job_activity",
    "location",
    "leader_id",
    "facilitator_id",
    "journey",
    "journey_shift",
    "band",
    "pcd",
    "team_sector",
    "job_title_sector",
    "job_title_activity",
    "inss_type",
    "productivity_discount",
    "formalization",
)

CYCLE_PAYLOAD_FIELDS = (
    "team",
    "job_title",
    "job_activity",
    "location",
    "leader",
    "facilitator",
    "journey",
    "band",
    "pcd",
    "team_sector",
    "job_title_sector",
    "job_title_activity",
    "inss_type",
    "productivity_discount",
    "formalization",
)

AGENT_PAYLOAD_FIELDS = (
    "full_name",
    "user_lan_id",
    "email",
    "hire_date",
    "time_tracking_id",
    "oracle_id",
    "jira_api_token",
    "active",
)

# Preservados sem edição na UI (campo jira do histórico ≠ token API).
COPY_FROM_CURRENT_FIELDS = ("jira",)

ACTION_UPDATE = "update"
ACTION_TERMINATE = "terminate"


class CycleChangeError(ValidationError):
    """Erro de regra de negócio na mudança de ciclo."""


@dataclass
class CycleChangeResult:
    agent: Agent
    cycle_changed: bool
    terminated: bool
    operation_id: str | None = None
    closed_history_id: str | None = None
    created_history_id: str | None = None
    field_changes: list | None = None
    entities: list | None = None
    technical: list | None = None
    estimated_records: int = 0


def _normalize_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_lan(value: Any) -> str:
    return _normalize_str(value).lower()


def _leader_facilitator_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _normalize_discount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"))
    text = str(value).strip()
    if not text:
        return None
    if ":" in text:
        try:
            return parse_hms_to_hours(text)
        except ValueError as exc:
            raise CycleChangeError({"productivity_discount": [str(exc)]}) from exc
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except Exception as exc:
        raise CycleChangeError(
            {"productivity_discount": ["Informe o desconto em HH:MM:SS ou horas decimais."]}
        ) from exc


def _cycle_values_from_history(history: AgentHistory) -> dict[str, Any]:
    return {
        "team": history.team or "",
        "job_title": history.job_title or "",
        "job_activity": history.job_activity or "",
        "location": history.location or "",
        "leader_id": str(history.leader_id) if history.leader_id else None,
        "facilitator_id": str(history.facilitator_id) if history.facilitator_id else None,
        "journey": history.journey or "",
        "journey_shift": (history.journey_shift or "").strip()
        or resolve_journey_shift(history.journey or ""),
        "band": history.band or "",
        "pcd": bool(history.pcd),
        "team_sector": history.team_sector or "",
        "job_title_sector": history.job_title_sector or "",
        "job_title_activity": history.job_title_activity or "",
        "inss_type": history.inss_type or "",
        "productivity_discount": history.productivity_discount,
        "formalization": history.formalization or "",
    }


def _cycle_values_from_payload(payload: dict[str, Any], current: AgentHistory) -> dict[str, Any]:
    values = _cycle_values_from_history(current)
    str_fields = (
        "team",
        "job_title",
        "job_activity",
        "location",
        "journey",
        "band",
        "team_sector",
        "job_title_sector",
        "job_title_activity",
        "inss_type",
        "formalization",
    )
    for field in str_fields:
        if field in payload:
            values[field] = _normalize_str(payload.get(field))
    if "pcd" in payload:
        values["pcd"] = bool(payload.get("pcd"))
    if "leader" in payload:
        values["leader_id"] = _leader_facilitator_id(payload.get("leader"))
    if "facilitator" in payload:
        values["facilitator_id"] = _leader_facilitator_id(payload.get("facilitator"))
    if "productivity_discount" in payload:
        values["productivity_discount"] = _normalize_discount(payload.get("productivity_discount"))
    if "journey" in payload:
        values["journey_shift"] = resolve_journey_shift(values["journey"])
    return values


def _cycle_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    for field in CYCLE_COMPARE_FIELDS:
        if before.get(field) != after.get(field):
            return True
    return False


def _apply_agent_patch(agent: Agent, agent_payload: dict[str, Any] | None) -> None:
    if not agent_payload:
        return
    update_fields: list[str] = []
    for field in AGENT_PAYLOAD_FIELDS:
        if field not in agent_payload:
            continue
        value = agent_payload[field]
        if field == "user_lan_id":
            value = _normalize_lan(value)
        elif field in {"full_name", "email", "time_tracking_id", "oracle_id", "jira_api_token"}:
            value = _normalize_str(value) if value is not None else ""
        elif field == "hire_date" and value == "":
            value = None
        setattr(agent, field, value)
        update_fields.append(field)
    if update_fields:
        update_fields.append("updated_at")
        agent.save(update_fields=update_fields)


def _close_history(
    history: AgentHistory,
    *,
    movement_date: date,
    movement_type: str | None = None,
) -> None:
    history.final_date = movement_date
    history.active = False
    update_fields = ["final_date", "active"]
    if movement_type is not None:
        history.external_movement_type = movement_type
        update_fields.append("external_movement_type")
    history.save(update_fields=update_fields)


def _create_new_history(
    agent: Agent,
    current: AgentHistory,
    cycle_values: dict[str, Any],
    movement_date: date,
) -> AgentHistory:
    kwargs: dict[str, Any] = {
        "agent": agent,
        "start_date": movement_date,
        "final_date": None,
        "active": True,
        "team": cycle_values["team"],
        "job_title": cycle_values["job_title"],
        "job_activity": cycle_values["job_activity"],
        "location": cycle_values["location"],
        "journey": cycle_values["journey"],
        "journey_shift": cycle_values["journey_shift"]
        or resolve_journey_shift(cycle_values["journey"]),
        "band": cycle_values["band"],
        "pcd": cycle_values["pcd"],
        "leader_id": cycle_values["leader_id"],
        "facilitator_id": cycle_values["facilitator_id"],
        "team_sector": cycle_values["team_sector"],
        "job_title_sector": cycle_values["job_title_sector"],
        "job_title_activity": cycle_values["job_title_activity"],
        "inss_type": cycle_values["inss_type"],
        "productivity_discount": cycle_values["productivity_discount"],
        "formalization": cycle_values["formalization"],
        "external_movement_type": "",
    }
    for field in COPY_FROM_CURRENT_FIELDS:
        kwargs[field] = getattr(current, field)
    return AgentHistory.objects.create(**kwargs)


def _validate_fk_agents(cycle_values: dict[str, Any]) -> None:
    for field, key in (("leader", "leader_id"), ("facilitator", "facilitator_id")):
        agent_id = cycle_values.get(key)
        if agent_id is None:
            continue
        if not Agent.objects.filter(id=agent_id).exists():
            raise CycleChangeError({field: [f"Colaborador informado em {field} não existe."]})


def _resolve_termination_movement_type(raw: Any) -> str:
    text = _normalize_str(raw)
    if text not in TERMINATION_MOVEMENT_CHOICES:
        raise CycleChangeError(
            {
                "external_movement_type": [
                    "Selecione Desligamento Voluntário ou Desligamento Involuntário."
                ]
            }
        )
    return text


def _resolve_update_movement_type(raw: Any) -> str | None:
    text = _normalize_str(raw)
    if not text:
        return None
    if text not in EXTERNAL_MOVEMENT_CHOICES:
        raise CycleChangeError({"external_movement_type": ["Tipo de movimentação inválido."]})
    return text


@transaction.atomic
def apply_cycle_change(
    agent: Agent,
    *,
    action: str,
    movement_date: date | None,
    agent_payload: dict[str, Any] | None = None,
    cycle_payload: dict[str, Any] | None = None,
    external_movement_type: str | None = None,
    updated_by=None,
    preview_id: str | None = None,
) -> CycleChangeResult:
    from apps.workforce.models import CycleChangeAudit
    from apps.workforce.services.cycle_preview import (
        assert_preview_still_valid,
        build_cycle_change_preview,
        consume_preview,
        get_cached_preview,
    )

    preview_cache = None
    if preview_id:
        preview_cache = get_cached_preview(preview_id)
        if not preview_cache:
            raise CycleChangeError(
                {
                    "preview_id": [
                        "Simulação expirada ou inválida. Revise as alterações novamente."
                    ]
                }
            )
        assert_preview_still_valid(agent, preview_cache)

    preview = build_cycle_change_preview(
        agent,
        action=action,
        movement_date=movement_date,
        agent_payload=agent_payload,
        cycle_payload=cycle_payload,
        external_movement_type=external_movement_type,
    )
    if preview.blocked or not preview.valid:
        raise CycleChangeError(preview.errors or {"detail": ["Revisão inválida."]})

    operation_id = (
        str(preview_cache["operation_id"])
        if preview_cache and preview_cache.get("operation_id")
        else preview.operation_id
    )
    closed_history_id: str | None = None
    created_history_id: str | None = None

    if action not in {ACTION_UPDATE, ACTION_TERMINATE}:
        raise CycleChangeError({"action": ['Ação inválida. Use "update" ou "terminate".']})

    locked = Agent.objects.select_for_update().get(pk=agent.pk)
    current = (
        AgentHistory.objects.select_for_update(of=("self",))
        .filter(agent=locked, active=True, final_date__isnull=True)
        .order_by("-start_date")
        .first()
    )
    if current is not None:
        current = (
            AgentHistory.objects.select_related("leader", "facilitator").get(pk=current.pk)
        )

    if updated_by is not None:
        locked.updated_by = updated_by
        locked.save(update_fields=["updated_by", "updated_at"])

    _apply_agent_patch(locked, agent_payload)

    result: CycleChangeResult
    try:
        if action == ACTION_TERMINATE:
            if movement_date is None:
                raise CycleChangeError(
                    {"movement_date": ["Data da movimentação é obrigatória para demissão."]}
                )
            if current is None:
                raise CycleChangeError(
                    {"detail": ["Não há vigência aberta para encerrar nesta demissão."]}
                )
            if movement_date < current.start_date:
                raise CycleChangeError(
                    {
                        "movement_date": [
                            "Data da movimentação não pode ser anterior ao início da vigência atual."
                        ]
                    }
                )
            movement_type = _resolve_termination_movement_type(external_movement_type)
            _close_history(
                current,
                movement_date=movement_date,
                movement_type=movement_type,
            )
            closed_history_id = str(current.id)
            locked.active = False
            locked.save(update_fields=["active", "updated_at"])
            locked.refresh_from_db()
            result = CycleChangeResult(
                agent=locked,
                cycle_changed=True,
                terminated=True,
                operation_id=operation_id,
                closed_history_id=closed_history_id,
                field_changes=preview.field_changes,
                entities=preview.entities,
                technical=preview.technical,
                estimated_records=preview.estimated_records,
            )
        else:
            cycle_payload = cycle_payload or {}
            wants_cycle_fields = any(field in cycle_payload for field in CYCLE_PAYLOAD_FIELDS)

            if not wants_cycle_fields:
                locked.refresh_from_db()
                result = CycleChangeResult(
                    agent=locked,
                    cycle_changed=False,
                    terminated=False,
                    operation_id=operation_id,
                    field_changes=preview.field_changes,
                    entities=preview.entities,
                    technical=preview.technical,
                    estimated_records=preview.estimated_records,
                )
            else:
                if current is None:
                    raise CycleChangeError(
                        {
                            "detail": [
                                "Não há vigência aberta para alterar o ciclo deste colaborador."
                            ]
                        }
                    )

                before = _cycle_values_from_history(current)
                after = _cycle_values_from_payload(cycle_payload, current)
                if not _cycle_changed(before, after):
                    locked.refresh_from_db()
                    result = CycleChangeResult(
                        agent=locked,
                        cycle_changed=False,
                        terminated=False,
                        operation_id=operation_id,
                        field_changes=preview.field_changes,
                        entities=preview.entities,
                        technical=preview.technical,
                        estimated_records=preview.estimated_records,
                    )
                else:
                    if movement_date is None:
                        raise CycleChangeError(
                            {
                                "movement_date": [
                                    "Data da movimentação é obrigatória quando o ciclo muda."
                                ]
                            }
                        )
                    if movement_date < current.start_date:
                        raise CycleChangeError(
                            {
                                "movement_date": [
                                    "Data da movimentação não pode ser anterior ao início da vigência atual."
                                ]
                            }
                        )

                    _validate_fk_agents(after)
                    from apps.workforce.services.catalog import assert_cycle_catalog_values

                    assert_cycle_catalog_values(after)
                    close_type = _resolve_update_movement_type(external_movement_type)
                    _close_history(
                        current, movement_date=movement_date, movement_type=close_type or ""
                    )
                    closed_history_id = str(current.id)
                    created = _create_new_history(locked, current, after, movement_date)
                    created_history_id = str(created.id)
                    locked.refresh_from_db()
                    result = CycleChangeResult(
                        agent=locked,
                        cycle_changed=True,
                        terminated=False,
                        operation_id=operation_id,
                        closed_history_id=closed_history_id,
                        created_history_id=created_history_id,
                        field_changes=preview.field_changes,
                        entities=preview.entities,
                        technical=preview.technical,
                        estimated_records=preview.estimated_records,
                    )

        CycleChangeAudit.objects.create(
            operation_id=operation_id,
            agent=locked,
            performed_by=updated_by if getattr(updated_by, "pk", None) else None,
            action=action,
            success=True,
            closed_history_id=closed_history_id,
            created_history_id=created_history_id,
            movement_date=movement_date,
            field_changes=preview.field_changes,
            entities=preview.entities,
            technical=preview.technical,
            result_summary={
                "cycle_changed": result.cycle_changed,
                "terminated": result.terminated,
                "estimated_records": result.estimated_records,
            },
        )
        if preview_id:
            consume_preview(preview_id)
        return result
    except CycleChangeError as exc:
        CycleChangeAudit.objects.create(
            operation_id=operation_id,
            agent=locked,
            performed_by=updated_by if getattr(updated_by, "pk", None) else None,
            action=action,
            success=False,
            error_detail=str(getattr(exc, "message_dict", None) or exc.messages),
            movement_date=movement_date,
            field_changes=preview.field_changes,
            entities=preview.entities,
            technical=preview.technical,
            result_summary={"failed": True},
        )
        raise
