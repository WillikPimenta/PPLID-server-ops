"""Reconcile Agent.active with open AgentHistory vigências.

Open history = active=True AND final_date IS NULL (mesmo critério do dashboard
de distribuição / serializers de ciclo atual).
"""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Literal

from django.db import transaction
from django.db.models import Count, Max, Prefetch, Q
from django.utils import timezone

from apps.workforce.models import Agent, AgentHistory, CycleChangeAudit

MismatchKind = Literal[
    "stale_active",
    "orphan_open",
    "multi_open",
    "consistent_active",
    "consistent_inactive",
]

ALIGNABLE_KINDS = ("stale_active", "orphan_open")


@dataclass
class MismatchRow:
    agent_id: str
    user_lan_id: str
    full_name: str
    agent_active: bool
    open_history_count: int
    kind: MismatchKind
    last_final_date: str | None = None
    last_external_movement_type: str = ""
    open_history_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _open_history_qs():
    return AgentHistory.objects.filter(active=True, final_date__isnull=True)


def _classify_agent(
    *,
    agent_active: bool,
    open_count: int,
) -> MismatchKind:
    if open_count > 1:
        return "multi_open"
    has_open = open_count == 1
    if agent_active and has_open:
        return "consistent_active"
    if not agent_active and not has_open:
        return "consistent_inactive"
    if agent_active and not has_open:
        return "stale_active"
    return "orphan_open"


def _last_closed_meta(agent_ids: Iterable) -> dict[Any, dict[str, Any]]:
    """Último histórico fechado por agente (final_date / movement)."""
    rows = (
        AgentHistory.objects.filter(agent_id__in=list(agent_ids))
        .exclude(final_date__isnull=True)
        .values("agent_id")
        .annotate(last_final=Max("final_date"))
    )
    last_by_agent = {r["agent_id"]: r["last_final"] for r in rows}
    meta: dict[Any, dict[str, Any]] = {}
    if not last_by_agent:
        return meta
    # Busca movement type do registro com esse final_date (1 query + map).
    pairs = [
        Q(agent_id=aid, final_date=fd) for aid, fd in last_by_agent.items() if fd
    ]
    if not pairs:
        return {aid: {"last_final_date": None, "last_external_movement_type": ""} for aid in last_by_agent}
    combined = pairs[0]
    for p in pairs[1:]:
        combined |= p
    for hist in AgentHistory.objects.filter(combined).only(
        "agent_id", "final_date", "external_movement_type"
    ):
        expected = last_by_agent.get(hist.agent_id)
        if expected and hist.final_date == expected:
            meta[hist.agent_id] = {
                "last_final_date": hist.final_date.isoformat() if hist.final_date else None,
                "last_external_movement_type": hist.external_movement_type or "",
            }
    for aid, fd in last_by_agent.items():
        meta.setdefault(
            aid,
            {
                "last_final_date": fd.isoformat() if isinstance(fd, date) else None,
                "last_external_movement_type": "",
            },
        )
    return meta


def iter_consistency_rows(
    *,
    kinds: Iterable[MismatchKind] | None = None,
) -> list[MismatchRow]:
    kind_filter = set(kinds) if kinds is not None else None
    open_counts = {
        row["agent_id"]: row["n"]
        for row in _open_history_qs().values("agent_id").annotate(n=Count("id"))
    }
    open_ids: dict[Any, list[str]] = defaultdict(list)
    for hist in _open_history_qs().only("id", "agent_id"):
        open_ids[hist.agent_id].append(str(hist.id))

    agents = list(Agent.objects.only("id", "user_lan_id", "full_name", "active"))
    closed_meta = _last_closed_meta(
        a.id
        for a in agents
        if open_counts.get(a.id, 0) == 0
    )

    rows: list[MismatchRow] = []
    for agent in agents:
        open_count = open_counts.get(agent.id, 0)
        kind = _classify_agent(agent_active=agent.active, open_count=open_count)
        if kind_filter is not None and kind not in kind_filter:
            continue
        meta = closed_meta.get(agent.id, {})
        rows.append(
            MismatchRow(
                agent_id=str(agent.id),
                user_lan_id=agent.user_lan_id or "",
                full_name=agent.full_name or "",
                agent_active=bool(agent.active),
                open_history_count=open_count,
                kind=kind,
                last_final_date=meta.get("last_final_date"),
                last_external_movement_type=meta.get("last_external_movement_type", ""),
                open_history_ids=list(open_ids.get(agent.id, [])),
            )
        )
    rows.sort(key=lambda r: (r.kind, r.user_lan_id))
    return rows


def summarize_agent_history_consistency(*, sample_limit: int = 15) -> dict[str, Any]:
    rows = iter_consistency_rows()
    counts: dict[str, int] = {
        "consistent_active": 0,
        "consistent_inactive": 0,
        "stale_active": 0,
        "orphan_open": 0,
        "multi_open": 0,
    }
    samples: dict[str, list[dict[str, Any]]] = {k: [] for k in counts}
    for row in rows:
        counts[row.kind] = counts.get(row.kind, 0) + 1
        if len(samples[row.kind]) < sample_limit:
            samples[row.kind].append(row.to_dict())

    open_histories = _open_history_qs().count()
    active_agents = Agent.objects.filter(active=True).count()
    return {
        "total_agents": Agent.objects.count(),
        "active_agents": active_agents,
        "open_histories": open_histories,
        "active_agents_vs_open_histories_delta": active_agents - open_histories,
        "counts": counts,
        "stale_active": counts["stale_active"],
        "orphan_open": counts["orphan_open"],
        "multi_open": counts["multi_open"],
        "samples": samples,
    }


def list_mismatches(
    kind: MismatchKind,
    *,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    rows = iter_consistency_rows(kinds=(kind,))
    total = len(rows)
    page = rows[offset : offset + limit]
    return {
        "kind": kind,
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [r.to_dict() for r in page],
    }


def predict_consistency_after_open_agent_ids(
    open_agent_ids: set[Any],
) -> dict[str, Any]:
    """Compara Agent.active atual com o conjunto de agentes que terão vigência aberta."""
    agents = list(Agent.objects.only("id", "user_lan_id", "full_name", "active"))
    stale: list[dict[str, Any]] = []
    orphan: list[dict[str, Any]] = []
    for agent in agents:
        will_have_open = agent.id in open_agent_ids
        if agent.active and not will_have_open:
            stale.append(
                {
                    "agent_id": str(agent.id),
                    "user_lan_id": agent.user_lan_id,
                    "full_name": agent.full_name,
                }
            )
        elif not agent.active and will_have_open:
            orphan.append(
                {
                    "agent_id": str(agent.id),
                    "user_lan_id": agent.user_lan_id,
                    "full_name": agent.full_name,
                }
            )

    active_now = sum(1 for a in agents if a.active)
    open_after = len(open_agent_ids)
    return {
        "agents_stale_active_after_import": len(stale),
        "agents_orphan_open_after_import": len(orphan),
        "agents_active_delta": len(stale),  # ativos que cairiam se alinhasse stale
        "open_histories_after_import": open_after,
        "active_agents_now": active_now,
        "active_after_align_estimate": active_now - len(stale) + len(orphan),
        "stale_active_sample": stale[:15],
        "orphan_open_sample": orphan[:15],
    }


def apply_align_agent_active(
    *,
    kinds: Iterable[str] = ALIGNABLE_KINDS,
    dry_run: bool = True,
    performed_by=None,
) -> dict[str, Any]:
    wanted = tuple(k for k in kinds if k in ALIGNABLE_KINDS)
    if not wanted:
        raise ValueError("Informe ao menos um kind alinhável: stale_active, orphan_open.")

    rows = iter_consistency_rows(kinds=wanted)  # type: ignore[arg-type]
    changes: list[dict[str, Any]] = []
    to_deactivate: list[Agent] = []
    to_activate: list[Agent] = []

    agents_by_id = {
        str(a.id): a
        for a in Agent.objects.filter(id__in=[r.agent_id for r in rows]).only(
            "id", "user_lan_id", "full_name", "active"
        )
    }

    for row in rows:
        agent = agents_by_id.get(row.agent_id)
        if not agent:
            continue
        if row.kind == "stale_active" and agent.active:
            changes.append(
                {
                    "agent_id": row.agent_id,
                    "user_lan_id": row.user_lan_id,
                    "full_name": row.full_name,
                    "kind": row.kind,
                    "from": True,
                    "to": False,
                }
            )
            agent.active = False
            to_deactivate.append(agent)
        elif row.kind == "orphan_open" and not agent.active:
            changes.append(
                {
                    "agent_id": row.agent_id,
                    "user_lan_id": row.user_lan_id,
                    "full_name": row.full_name,
                    "kind": row.kind,
                    "from": False,
                    "to": True,
                }
            )
            agent.active = True
            to_activate.append(agent)

    report = {
        "dry_run": dry_run,
        "kinds": list(wanted),
        "would_change": len(changes),
        "deactivate_count": len(to_deactivate),
        "activate_count": len(to_activate),
        "changes": changes[:200],
        "changes_truncated": max(0, len(changes) - 200),
        "applied": False,
        "audit_operation_id": None,
        "backup_path": None,
    }

    if dry_run or not changes:
        return report

    backups_root = Path(__file__).resolve().parents[3] / "data" / "backups"
    backups_root.mkdir(parents=True, exist_ok=True)
    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    backup_path = backups_root / f"agent_active_align_{stamp}.json"
    backup_path.write_text(
        json.dumps(
            {
                "created_at": timezone.now().isoformat(),
                "changes": changes,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    operation_id = uuid.uuid4()
    with transaction.atomic():
        if to_deactivate:
            Agent.objects.bulk_update(to_deactivate, ["active"], batch_size=500)
        if to_activate:
            Agent.objects.bulk_update(to_activate, ["active"], batch_size=500)

        # Uma auditoria agregada + uma por agente (amostra limitada para não explodir).
        CycleChangeAudit.objects.create(
            operation_id=operation_id,
            agent=to_deactivate[0] if to_deactivate else to_activate[0],
            performed_by=performed_by if getattr(performed_by, "is_authenticated", False) else None,
            action="align_agent_active",
            success=True,
            field_changes=[
                {
                    "field": "active",
                    "summary": f"{len(to_deactivate)} desativados, {len(to_activate)} ativados",
                }
            ],
            result_summary={
                "deactivate_count": len(to_deactivate),
                "activate_count": len(to_activate),
                "backup_path": str(backup_path),
            },
            technical=[{"backup_path": str(backup_path), "kinds": list(wanted)}],
        )

    report["applied"] = True
    report["audit_operation_id"] = str(operation_id)
    report["backup_path"] = str(backup_path)
    return report
