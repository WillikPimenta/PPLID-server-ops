from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.workforce.models import Agent

from ..models import OperationalAlertEvent, ScheduleToday
from .operational_dashboard import evaluate_operational_dashboard_rows


ALERT_TYPE_LABELS = {
    "break_exceeded": "Intervalo excedido",
    "break_not_started": "Intervalo não iniciado",
    "break_outside_schedule": "Intervalo fora do horário",
    "systemic_issue": "Problema sistêmico",
    "personal_status_exceeded": "Status pessoal prolongado",
    "occurrence_exceeded": "Tempo de ocorrência excedido",
    "occurrence_pending": "Ocorrência pendente",
    "occurrence_rejected": "Ocorrência recusada",
    "occurrence_missing": "Ocorrência não cadastrada",
    "early_logout": "Saída antecipada",
    "login_outside_schedule": "Login fora da escala",
    "invalid_schedule": "Escala inválida",
    "missing_break_configuration": "Intervalo não configurado",
    "status_start_missing": "Início do status indisponível",
    "overtime_information": "Hora extra",
}


@dataclass(frozen=True)
class EvaluatedAlert:
    agent_id: int
    operational_date: date
    alert_type: str
    fingerprint: str
    source_type: str
    source_id: str
    priority: str
    state: str
    reason: str
    recommended_action: str
    leader_lan_id: str
    leader_name: str
    location: str
    sector: str
    job_activity: str
    context: dict[str, Any]


def build_alert_fingerprint(
    *,
    agent_id: int,
    operational_date: date,
    alert_type: str,
    source_type: str = "",
    source_id: str = "",
) -> str:
    raw = "|".join(
        (str(agent_id), operational_date.isoformat(), alert_type, source_type, source_id)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_operational_alert_rows(
    rows: list[dict[str, Any]],
    operational_date: date,
) -> list[EvaluatedAlert]:
    normalized: list[EvaluatedAlert] = []
    for row in rows:
        alert_type = str(row.get("alert_type") or "")
        if not alert_type:
            continue
        source_type = str(row.get("alert_source_type") or "")
        source_id = str(row.get("alert_source_id") or "")
        agent_id = int(row["agent_id"])
        context = {
            key: row.get(key)
            for key in (
                "user_lan_id",
                "full_name",
                "status",
                "status_name",
                "work_schedule",
                "journey",
                "week_break",
                "weekend_break",
                "intervalo_atraso_minutos",
                "ocorrencia_excesso_minutos",
                "ocorrencia_situacao",
                "pessoal_duracao_minutos",
                "em_turno",
                "hora_extra",
            )
        }
        normalized.append(
            EvaluatedAlert(
                agent_id=agent_id,
                operational_date=operational_date,
                alert_type=alert_type,
                fingerprint=build_alert_fingerprint(
                    agent_id=agent_id,
                    operational_date=operational_date,
                    alert_type=alert_type,
                    source_type=source_type,
                    source_id=source_id,
                ),
                source_type=source_type,
                source_id=source_id,
                priority=str(row.get("prioridade") or ""),
                state=str(row.get("estado") or ""),
                reason=str(row.get("motivo") or ""),
                recommended_action=str(row.get("acao_recomendada") or ""),
                leader_lan_id=str(row.get("leader_lan_id") or ""),
                leader_name=str(row.get("leader_name") or ""),
                location=str(row.get("location") or ""),
                sector=str(row.get("sector") or ""),
                job_activity=str(row.get("job_activity") or ""),
                context=context,
            )
        )
    return normalized


def collect_operational_alerts(
    *,
    target_date: date | None = None,
    agent_lan_id: str = "",
    dry_run: bool = False,
    reference_time: datetime | None = None,
) -> dict[str, int | bool | str]:
    target_date = target_date or timezone.localdate()
    reference_time = reference_time or timezone.now()
    params: dict[str, str] = {}
    if agent_lan_id:
        params["search"] = agent_lan_id.strip()

    rows = evaluate_operational_dashboard_rows(target_date, params, reference_time)
    if agent_lan_id:
        expected = agent_lan_id.strip().lower()
        rows = [row for row in rows if str(row.get("user_lan_id") or "").lower() == expected]
    alerts = normalize_operational_alert_rows(rows, target_date)
    evaluated_agent_ids = {int(row["agent_id"]) for row in rows}

    if agent_lan_id and not evaluated_agent_ids:
        agent_id = (
            Agent.objects.filter(user_lan_id__iexact=agent_lan_id.strip())
            .values_list("id", flat=True)
            .first()
        )
        if agent_id:
            evaluated_agent_ids.add(agent_id)

    active_qs = OperationalAlertEvent.objects.filter(
        operational_date=target_date,
        status=OperationalAlertEvent.STATUS_ACTIVE,
        agent_id__in=evaluated_agent_ids,
    )
    existing_by_fp = {event.fingerprint: event for event in active_qs}
    incoming_fps = {alert.fingerprint for alert in alerts}
    created_count = sum(1 for alert in alerts if alert.fingerprint not in existing_by_fp)
    updated_count = len(alerts) - created_count
    resolved_count = sum(1 for fp in existing_by_fp if fp not in incoming_fps)

    if dry_run:
        return {
            "date": target_date.isoformat(),
            "evaluated_agents": len(evaluated_agent_ids),
            "alerts": len(alerts),
            "created": created_count,
            "updated": updated_count,
            "resolved": resolved_count,
            "dry_run": True,
        }

    with transaction.atomic():
        # Serializa coletores concorrentes no mesmo conjunto de agentes/data.
        list(
            ScheduleToday.objects.select_for_update()
            .filter(date=target_date, agent_id__in=evaluated_agent_ids)
            .order_by("agent_id")
            .values_list("id", flat=True)
        )
        locked_events = list(active_qs.select_for_update())
        existing_by_fp = {event.fingerprint: event for event in locked_events}

        for alert in alerts:
            event = existing_by_fp.get(alert.fingerprint)
            values = {
                "priority_current": alert.priority,
                "state": alert.state,
                "reason": alert.reason,
                "recommended_action": alert.recommended_action,
                "last_seen_at": reference_time,
                "leader_lan_id": alert.leader_lan_id,
                "leader_name": alert.leader_name,
                "location": alert.location,
                "sector": alert.sector,
                "job_activity": alert.job_activity,
                "context": alert.context,
            }
            if event:
                for field, value in values.items():
                    setattr(event, field, value)
                event.occurrence_count += 1
                event.save(update_fields=[*values.keys(), "occurrence_count", "updated_at"])
                continue

            OperationalAlertEvent.objects.create(
                agent_id=alert.agent_id,
                operational_date=alert.operational_date,
                alert_type=alert.alert_type,
                fingerprint=alert.fingerprint,
                source_type=alert.source_type,
                source_id=alert.source_id,
                priority_initial=alert.priority,
                first_seen_at=reference_time,
                occurrence_count=1,
                **values,
            )

        for event in locked_events:
            if event.fingerprint in incoming_fps:
                continue
            event.status = OperationalAlertEvent.STATUS_RESOLVED
            event.resolved_at = reference_time
            event.save(update_fields=["status", "resolved_at", "updated_at"])

    return {
        "date": target_date.isoformat(),
        "evaluated_agents": len(evaluated_agent_ids),
        "alerts": len(alerts),
        "created": created_count,
        "updated": updated_count,
        "resolved": resolved_count,
        "dry_run": False,
    }
