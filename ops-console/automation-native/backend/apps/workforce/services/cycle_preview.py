"""Simulação (dry-run) de alteração de ciclo de Headcount."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from apps.workforce.models import Agent, AgentHistory
from apps.workforce.services.cycle_change import (
    ACTION_TERMINATE,
    ACTION_UPDATE,
    CYCLE_COMPARE_FIELDS,
    CycleChangeError,
    _cycle_changed,
    _cycle_values_from_history,
    _cycle_values_from_payload,
    _normalize_lan,
    _normalize_str,
    _resolve_termination_movement_type,
    _resolve_update_movement_type,
    _validate_fk_agents,
)
from apps.workforce.services.catalog import assert_cycle_catalog_values
from apps.workforce.services.journey_shift import hours_to_hms

PREVIEW_CACHE_PREFIX = "hc_cycle_preview:"
PREVIEW_TTL_SECONDS = 30 * 60

FIELD_LABELS: dict[str, str] = {
    "full_name": "Nome completo",
    "user_lan_id": "LAN ID",
    "email": "E-mail",
    "hire_date": "Admissão",
    "time_tracking_id": "ID controle de ponto",
    "oracle_id": "Oracle ID",
    "jira_api_token": "Token API Jira",
    "team": "Time",
    "job_title": "Cargo",
    "job_activity": "Atividade",
    "location": "Local",
    "journey": "Jornada",
    "journey_shift": "Turno",
    "band": "Band",
    "pcd": "PCD",
    "team_sector": "Setor do time",
    "job_title_sector": "Setor do cargo",
    "job_title_activity": "Atividade do cargo",
    "inss_type": "Tipo INSS",
    "productivity_discount": "Desconto produtividade",
    "formalization": "Formalização",
    "leader_id": "Líder",
    "facilitator_id": "Facilitador",
    "external_movement_type": "Tipo de movimentação",
    "movement_date": "Data da movimentação",
    "terminate": "Desligamento",
}

EMPTY_DISPLAY = "Sem valor"


@dataclass
class PreviewPayload:
    action: str
    movement_date: date | None
    external_movement_type: str | None
    agent_payload: dict[str, Any]
    cycle_payload: dict[str, Any]


@dataclass
class CyclePreviewResult:
    preview_id: str
    valid: bool
    blocked: bool
    action: str
    errors: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    field_changes: list[dict[str, Any]] = field(default_factory=list)
    cycle: dict[str, Any] = field(default_factory=dict)
    entities: list[dict[str, Any]] = field(default_factory=list)
    technical: list[dict[str, Any]] = field(default_factory=list)
    fingerprint: dict[str, Any] = field(default_factory=dict)
    estimated_records: int = 0
    operation_id: str = ""

    def to_response(self) -> dict[str, Any]:
        data = asdict(self)
        return data


def _display_value(field: str, value: Any) -> str:
    if value is None or value == "":
        return EMPTY_DISPLAY
    if field == "pcd":
        return "Sim" if value else "Não"
    if field == "productivity_discount":
        if isinstance(value, Decimal):
            return hours_to_hms(value) or EMPTY_DISPLAY
        text = str(value).strip()
        return text or EMPTY_DISPLAY
    if field in {"leader_id", "facilitator_id"}:
        agent = Agent.objects.filter(id=value).only("full_name", "user_lan_id").first()
        if agent:
            return f"{agent.full_name} ({agent.user_lan_id})"
        return str(value)
    if field == "hire_date" and hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _change_kind(before: Any, after: Any) -> str:
    before_empty = before in (None, "")
    after_empty = after in (None, "")
    if before_empty and not after_empty:
        return "add"
    if not before_empty and after_empty:
        return "remove"
    return "replace"


def _agent_fingerprint(agent: Agent, history: AgentHistory | None) -> dict[str, Any]:
    return {
        "agent_id": str(agent.id),
        "agent_updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
        "history_id": str(history.id) if history else None,
        "history_active": bool(history.active) if history else None,
        "history_final_date": history.final_date.isoformat() if history and history.final_date else None,
        "history_start_date": history.start_date.isoformat() if history else None,
    }


def _current_history(agent: Agent) -> AgentHistory | None:
    return (
        AgentHistory.objects.select_related("leader", "facilitator")
        .filter(agent=agent, active=True, final_date__isnull=True)
        .order_by("-start_date")
        .first()
    )


def _agent_field_changes(agent: Agent, agent_payload: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    if not agent_payload:
        return changes
    for field, new_raw in agent_payload.items():
        if field == "jira_api_token":
            if not str(new_raw or "").strip():
                continue
            changes.append(
                {
                    "field": field,
                    "label": FIELD_LABELS.get(field, field),
                    "before": "••••••••" if agent.jira_api_token else EMPTY_DISPLAY,
                    "after": "(novo token)",
                    "before_raw": None,
                    "after_raw": "***",
                    "change_kind": "replace" if agent.jira_api_token else "add",
                    "source": "user",
                    "caused_by": None,
                }
            )
            continue
        old = getattr(agent, field, None)
        new = new_raw
        if field == "user_lan_id":
            new = _normalize_lan(new_raw)
            old_cmp = _normalize_lan(old)
            new_cmp = new
        elif field == "hire_date":
            old_cmp = old
            new_cmp = new_raw if new_raw != "" else None
            new = new_cmp
        else:
            old_cmp = _normalize_str(old) if old is not None else ""
            new_cmp = _normalize_str(new_raw) if new_raw is not None else ""
            new = new_cmp
            old = old_cmp
        if old_cmp == new_cmp:
            continue
        changes.append(
            {
                "field": field,
                "label": FIELD_LABELS.get(field, field),
                "before": _display_value(field, old),
                "after": _display_value(field, new),
                "before_raw": old if not hasattr(old, "isoformat") else old.isoformat(),
                "after_raw": new if not hasattr(new, "isoformat") else new.isoformat(),
                "change_kind": _change_kind(old, new),
                "source": "user",
                "caused_by": None,
            }
        )
    return changes


def _cycle_field_changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in CYCLE_COMPARE_FIELDS:
        b = before.get(field)
        a = after.get(field)
        if b == a:
            continue
        source = "user"
        caused_by = None
        if field == "journey_shift" and before.get("journey") != after.get("journey"):
            source = "auto"
            caused_by = "journey"
        changes.append(
            {
                "field": field,
                "label": FIELD_LABELS.get(field, field),
                "before": _display_value(field, b),
                "after": _display_value(field, a),
                "before_raw": str(b) if b is not None else None,
                "after_raw": str(a) if a is not None else None,
                "change_kind": _change_kind(b, a),
                "source": source,
                "caused_by": caused_by,
            }
        )
    return changes


def _build_entities(
    *,
    action: str,
    cycle_changed: bool,
    agent_changed: bool,
    terminate: bool,
    field_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entities: list[dict[str, Any]] = []
    changed_fields = {c["field"] for c in field_changes}

    if terminate or cycle_changed:
        entities.append(
            {
                "key": "cycle_history_close",
                "label": "Histórico de ciclos",
                "action": "encerrar",
                "count": 1,
                "description": "Ciclo vigente: 1 registro será encerrado",
            }
        )
    if cycle_changed and not terminate:
        entities.append(
            {
                "key": "cycle_history_create",
                "label": "Histórico de ciclos",
                "action": "criar",
                "count": 1,
                "description": "Histórico de ciclos: 1 registro será criado",
            }
        )

    org_fields = {"team", "team_sector", "location"}
    if changed_fields & org_fields and not terminate:
        entities.append(
            {
                "key": "org_structure",
                "label": "Estrutura organizacional",
                "action": "atualizar",
                "count": 1,
                "description": "Estrutura organizacional: dados do ciclo serão atualizados no novo registro",
            }
        )

    job_fields = {"job_title", "job_activity", "job_title_sector", "job_title_activity", "band", "inss_type"}
    if changed_fields & job_fields and not terminate:
        entities.append(
            {
                "key": "job_info",
                "label": "Informações funcionais",
                "action": "atualizar",
                "count": 1,
                "description": "Informações funcionais: 1 registro será atualizado",
            }
        )

    journey_fields = {"journey", "journey_shift", "productivity_discount"}
    if changed_fields & journey_fields and not terminate:
        entities.append(
            {
                "key": "journey",
                "label": "Jornada",
                "action": "atualizar",
                "count": 1,
                "description": "Jornada: 1 registro será atualizado",
            }
        )

    if "leader_id" in changed_fields and not terminate:
        entities.append(
            {
                "key": "leader_link",
                "label": "Vínculo de liderança",
                "action": "atualizar",
                "count": 1,
                "description": "Vínculo de liderança: 1 registro será atualizado",
            }
        )
    if "facilitator_id" in changed_fields and not terminate:
        entities.append(
            {
                "key": "facilitator_link",
                "label": "Vínculo de facilitador",
                "action": "atualizar",
                "count": 1,
                "description": "Vínculo de facilitador: 1 registro será atualizado",
            }
        )

    if agent_changed:
        entities.append(
            {
                "key": "agent_cadastro",
                "label": "Cadastro do colaborador",
                "action": "atualizar",
                "count": 1,
                "description": "Cadastro do colaborador: 1 registro será atualizado",
            }
        )

    if terminate:
        entities.append(
            {
                "key": "agent_status",
                "label": "Situação do colaborador",
                "action": "inativar",
                "count": 1,
                "description": "Situação do colaborador: 1 registro será marcado como inativo",
            }
        )

    return entities


def _build_technical(*, terminate: bool, cycle_changed: bool, agent_changed: bool) -> list[dict[str, Any]]:
    technical: list[dict[str, Any]] = []
    if terminate or cycle_changed:
        technical.append(
            {
                "table": "agent_history",
                "operation": "encerramento lógico",
                "count": 1,
            }
        )
    if cycle_changed and not terminate:
        technical.append(
            {
                "table": "agent_history",
                "operation": "INSERT",
                "count": 1,
            }
        )
    if agent_changed or terminate:
        technical.append(
            {
                "table": "agent",
                "operation": "UPDATE",
                "count": 1,
            }
        )
    technical.append(
        {
            "table": "cycle_change_audit",
            "operation": "INSERT",
            "count": 1,
        }
    )
    return technical


def _cycle_summary(values: dict[str, Any], *, start_date: date | None, final_date: date | None) -> dict[str, Any]:
    return {
        "start_date": start_date.isoformat() if start_date else None,
        "final_date": final_date.isoformat() if final_date else None,
        "team": values.get("team") or EMPTY_DISPLAY,
        "job_title": values.get("job_title") or EMPTY_DISPLAY,
        "location": values.get("location") or EMPTY_DISPLAY,
        "journey": values.get("journey") or EMPTY_DISPLAY,
        "journey_shift": values.get("journey_shift") or EMPTY_DISPLAY,
        "leader": _display_value("leader_id", values.get("leader_id")),
        "facilitator": _display_value("facilitator_id", values.get("facilitator_id")),
    }


def build_cycle_change_preview(
    agent: Agent,
    *,
    action: str,
    movement_date: date | None,
    agent_payload: dict[str, Any] | None = None,
    cycle_payload: dict[str, Any] | None = None,
    external_movement_type: str | None = None,
) -> CyclePreviewResult:
    agent_payload = dict(agent_payload or {})
    cycle_payload = dict(cycle_payload or {})
    history = _current_history(agent)
    fingerprint = _agent_fingerprint(agent, history)
    operation_id = str(uuid.uuid4())
    preview_id = str(uuid.uuid4())
    warnings: list[str] = []
    errors: dict[str, Any] = {}

    if action not in {ACTION_UPDATE, ACTION_TERMINATE}:
        errors["action"] = ['Ação inválida. Use "update" ou "terminate".']

    agent_changes = _agent_field_changes(agent, agent_payload)
    field_changes = list(agent_changes)
    cycle_changed = False
    terminate = action == ACTION_TERMINATE
    after: dict[str, Any] | None = None
    before: dict[str, Any] | None = None

    try:
        if terminate:
            if movement_date is None:
                raise CycleChangeError(
                    {"movement_date": ["Data da movimentação é obrigatória para demissão."]}
                )
            if history is None:
                raise CycleChangeError(
                    {"detail": ["Não há vigência aberta para encerrar nesta demissão."]}
                )
            if movement_date < history.start_date:
                raise CycleChangeError(
                    {
                        "movement_date": [
                            "Data da movimentação não pode ser anterior ao início da vigência atual."
                        ]
                    }
                )
            _resolve_termination_movement_type(external_movement_type)
            before = _cycle_values_from_history(history)
            field_changes.append(
                {
                    "field": "terminate",
                    "label": "Desligamento",
                    "before": "Ciclo aberto",
                    "after": "Ciclo encerrado sem novo ciclo",
                    "before_raw": False,
                    "after_raw": True,
                    "change_kind": "replace",
                    "source": "user",
                    "caused_by": None,
                }
            )
            field_changes.append(
                {
                    "field": "external_movement_type",
                    "label": FIELD_LABELS["external_movement_type"],
                    "before": history.external_movement_type or EMPTY_DISPLAY,
                    "after": _normalize_str(external_movement_type) or EMPTY_DISPLAY,
                    "before_raw": history.external_movement_type or "",
                    "after_raw": _normalize_str(external_movement_type),
                    "change_kind": "replace",
                    "source": "user",
                    "caused_by": None,
                }
            )
        else:
            wants_cycle_fields = bool(cycle_payload)
            if wants_cycle_fields:
                if history is None:
                    raise CycleChangeError(
                        {
                            "detail": [
                                "Não há vigência aberta para alterar o ciclo deste colaborador."
                            ]
                        }
                    )
                before = _cycle_values_from_history(history)
                after = _cycle_values_from_payload(cycle_payload, history)
                cycle_changed = _cycle_changed(before, after)
                if cycle_changed:
                    if movement_date is None:
                        raise CycleChangeError(
                            {
                                "movement_date": [
                                    "Data da movimentação é obrigatória quando o ciclo muda."
                                ]
                            }
                        )
                    if movement_date < history.start_date:
                        raise CycleChangeError(
                            {
                                "movement_date": [
                                    "Data da movimentação não pode ser anterior ao início da vigência atual."
                                ]
                            }
                        )
                    _validate_fk_agents(after)
                    assert_cycle_catalog_values(after)
                    close_type = _resolve_update_movement_type(external_movement_type)
                    if close_type:
                        field_changes.append(
                            {
                                "field": "external_movement_type",
                                "label": FIELD_LABELS["external_movement_type"],
                                "before": EMPTY_DISPLAY,
                                "after": close_type,
                                "before_raw": "",
                                "after_raw": close_type,
                                "change_kind": "add",
                                "source": "user",
                                "caused_by": None,
                            }
                        )
                    field_changes.extend(_cycle_field_changes(before, after))
                elif not agent_changes:
                    warnings.append("Nenhuma alteração efetiva foi detectada.")
    except CycleChangeError as exc:
        errors = getattr(exc, "message_dict", None) or {"detail": exc.messages}

    agent_changed = bool(agent_changes)
    if terminate:
        cycle_changed = True

    if not field_changes and not errors and not terminate:
        warnings.append("Nenhuma alteração efetiva foi detectada.")

    if terminate:
        warnings.append(
            "Nenhum ciclo novo será criado. O histórico anterior permanece preservado."
        )
    elif cycle_changed:
        warnings.append(
            "O ciclo vigente será encerrado e um novo ciclo será criado. "
            "O histórico anterior permanece preservado (não é sobrescrito)."
        )

    cycle_block: dict[str, Any] = {
        "preserves_history": True,
        "closing": None,
        "opening": None,
        "message": "",
    }
    if history and (terminate or cycle_changed):
        before_vals = before or _cycle_values_from_history(history)
        cycle_block["closing"] = _cycle_summary(
            before_vals,
            start_date=history.start_date,
            final_date=movement_date,
        )
        if terminate:
            cycle_block["opening"] = None
            cycle_block["message"] = (
                "O ciclo vigente será encerrado na data informada. "
                "Nenhum ciclo novo será aberto. O histórico permanece disponível."
            )
        else:
            after_vals = after or before_vals
            cycle_block["opening"] = _cycle_summary(
                after_vals,
                start_date=movement_date,
                final_date=None,
            )
            cycle_block["message"] = (
                "O ciclo vigente será encerrado e um novo ciclo será iniciado "
                "na data da movimentação. O histórico anterior será preservado."
            )

    entities = []
    technical = []
    if not errors:
        entities = _build_entities(
            action=action,
            cycle_changed=cycle_changed,
            agent_changed=agent_changed,
            terminate=terminate,
            field_changes=field_changes,
        )
        technical = _build_technical(
            terminate=terminate,
            cycle_changed=cycle_changed,
            agent_changed=agent_changed,
        )

    estimated = sum(item["count"] for item in technical)
    blocked = bool(errors)
    valid = not blocked and (bool(field_changes) or terminate)

    result = CyclePreviewResult(
        preview_id=preview_id,
        valid=valid,
        blocked=blocked,
        action=action,
        errors=errors,
        warnings=warnings,
        field_changes=field_changes,
        cycle=cycle_block,
        entities=entities,
        technical=technical,
        fingerprint=fingerprint,
        estimated_records=estimated,
        operation_id=operation_id,
    )

    cache.set(
        f"{PREVIEW_CACHE_PREFIX}{preview_id}",
        {
            "preview_id": preview_id,
            "operation_id": operation_id,
            "agent_id": str(agent.id),
            "fingerprint": fingerprint,
            "payload": {
                "action": action,
                "movement_date": movement_date.isoformat() if movement_date else None,
                "external_movement_type": external_movement_type,
                "agent": agent_payload,
                "cycle": cycle_payload,
            },
            "created_at": timezone.now().isoformat(),
        },
        PREVIEW_TTL_SECONDS,
    )
    return result


def get_cached_preview(preview_id: str) -> dict[str, Any] | None:
    if not preview_id:
        return None
    data = cache.get(f"{PREVIEW_CACHE_PREFIX}{preview_id}")
    return data if isinstance(data, dict) else None


def assert_preview_still_valid(agent: Agent, preview: dict[str, Any]) -> None:
    if str(preview.get("agent_id")) != str(agent.id):
        raise CycleChangeError(
            {"preview_id": ["Simulação não corresponde a este colaborador. Revise novamente."]}
        )
    history = _current_history(agent)
    current_fp = _agent_fingerprint(agent, history)
    if current_fp != preview.get("fingerprint"):
        raise CycleChangeError(
            {
                "preview_id": [
                    "Os dados foram alterados desde a revisão. Solicite uma nova revisão antes de confirmar."
                ]
            }
        )


def consume_preview(preview_id: str) -> None:
    cache.delete(f"{PREVIEW_CACHE_PREFIX}{preview_id}")
