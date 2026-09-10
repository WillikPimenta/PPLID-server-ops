"""Conversor e importação de agent_history a partir de export SharePoint/XLSX.

Reutiliza as regras de parse de excel_utils (mesmo comportamento do seed
import_base_xlsx), mas:
- não cria/exclui Agent, UserProfile ou User;
- resolve agente/líder/facilitador por LAN ID;
- preserva UUIDs quando o match por sharepoint_item_id (ou chave temporal) é inequívoco;
- permite dry-run, backup JSON e restauração.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from apps.workforce.excel_utils import (
    AGENT_LAN_COLUMN_ALIASES,
    AGENT_NAME_ALIASES,
    FACILITATOR_LAN_COLUMN_ALIASES,
    LEADER_LAN_COLUMN_ALIASES,
    SHAREPOINT_ID_COLUMN_ALIASES,
    SHEET_HISTORY_ALIASES,
    is_empty,
    normalize_name,
    parse_active,
    parse_bool,
    parse_date,
    parse_decimal,
    pick_column,
    quantize_productivity_discount,
    read_sheet,
    str_or_blank,
)
from apps.workforce.models import Agent, AgentHistory

# Lock dedicado à carga de agent_history (namespace PPLID workforce).
_ADVISORY_LOCK_KEY = 0x50504C49_00414858  # "PPLI" + "AHX"


@dataclass
class HistoryRowDraft:
    """Registro convertido, ainda sem FK resolvida."""

    excel_row: int
    sharepoint_item_id: str
    agent_lan_id: str
    agent_name: str
    leader_lan_id: str
    leader_name: str
    facilitator_lan_id: str
    facilitator_name: str
    location: str = ""
    team: str = ""
    job_title: str = ""
    job_activity: str = ""
    journey: str = ""
    team_sector: str = ""
    job_title_sector: str = ""
    job_title_activity: str = ""
    journey_shift: str = ""
    inss_type: str = ""
    external_movement_type: str = ""
    start_date: date | None = None
    final_date: date | None = None
    productivity_discount: Decimal | None = None
    pcd: bool = False
    jira: str = ""
    formalization: str = ""
    band: str = ""
    active: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.start_date is not None and bool(self.agent_lan_id)

    def business_key(self) -> tuple:
        return (
            self.agent_lan_id,
            self.start_date.isoformat() if self.start_date else "",
            self.final_date.isoformat() if self.final_date else "",
            self.team,
            self.job_activity,
            self.sharepoint_item_id,
        )


@dataclass
class ResolvedHistoryRow:
    draft: HistoryRowDraft
    agent: Agent
    leader: Agent | None
    facilitator: Agent | None
    reuse_id: uuid.UUID | None = None
    match_reason: str = "new"

    def fingerprint(self) -> str:
        payload = {
            "agent": self.agent.user_lan_id,
            "leader": self.leader.user_lan_id if self.leader else "",
            "facilitator": self.facilitator.user_lan_id if self.facilitator else "",
            "location": self.draft.location,
            "team": self.draft.team,
            "job_title": self.draft.job_title,
            "job_activity": self.draft.job_activity,
            "journey": self.draft.journey,
            "team_sector": self.draft.team_sector,
            "job_title_sector": self.draft.job_title_sector,
            "job_title_activity": self.draft.job_title_activity,
            "journey_shift": self.draft.journey_shift,
            "inss_type": self.draft.inss_type,
            "external_movement_type": self.draft.external_movement_type,
            "start_date": self.draft.start_date.isoformat() if self.draft.start_date else "",
            "final_date": self.draft.final_date.isoformat() if self.draft.final_date else "",
            "productivity_discount": (
                str(self.draft.productivity_discount)
                if self.draft.productivity_discount is not None
                else ""
            ),
            "pcd": self.draft.pcd,
            "jira": self.draft.jira,
            "formalization": self.draft.formalization,
            "band": self.draft.band,
            "active": self.draft.active,
            "sharepoint_item_id": self.draft.sharepoint_item_id,
        }
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class PreflightReport:
    file_path: str
    file_sha256: str
    file_mtime: str
    sheet_name: str
    rows_read: int
    rows_converted: int
    rows_with_errors: int
    unresolved_agent_lans: list[str]
    unresolved_leader_lans: list[str]
    unresolved_facilitator_lans: list[str]
    type_or_size_errors: list[dict[str, Any]]
    temporal_warnings: list[dict[str, Any]]
    name_mismatches: list[dict[str, Any]]
    expected_inserts: int
    expected_uuid_reuse: int
    expected_uuid_new: int
    current_history_count: int
    expected_after_replace: int
    agent_count_unchanged: int
    user_profile_count_unchanged: int
    fingerprint_sample: str
    agent_active_consistency: dict[str, Any] = field(default_factory=dict)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert_history_row(row: Any, excel_row: int, columns: dict[str, str | None]) -> HistoryRowDraft:
    """Converte uma linha do DataFrame nas mesmas regras de parse do seed."""
    agent_lan_col = columns.get("agent_lan")
    agent_name_col = columns.get("agent_name")
    leader_lan_col = columns.get("leader_lan")
    leader_name_col = columns.get("leader_name")
    facilitator_lan_col = columns.get("facilitator_lan")
    facilitator_name_col = columns.get("facilitator_name")
    sharepoint_col = columns.get("sharepoint_id")

    agent_lan = ""
    if agent_lan_col and not is_empty(row.get(agent_lan_col)):
        agent_lan = str(row.get(agent_lan_col)).strip().lower()

    agent_name = ""
    if agent_name_col and not is_empty(row.get(agent_name_col)):
        agent_name = str(row.get(agent_name_col)).strip()

    leader_lan = ""
    if leader_lan_col and not is_empty(row.get(leader_lan_col)):
        leader_lan = str(row.get(leader_lan_col)).strip().lower()
    leader_name = ""
    if leader_name_col and not is_empty(row.get(leader_name_col)):
        leader_name = str(row.get(leader_name_col)).strip()

    facilitator_lan = ""
    if facilitator_lan_col and not is_empty(row.get(facilitator_lan_col)):
        facilitator_lan = str(row.get(facilitator_lan_col)).strip().lower()
    facilitator_name = ""
    if facilitator_name_col and not is_empty(row.get(facilitator_name_col)):
        facilitator_name = str(row.get(facilitator_name_col)).strip()

    sharepoint_item_id = ""
    if sharepoint_col and not is_empty(row.get(sharepoint_col)):
        sharepoint_item_id = str_or_blank(row.get(sharepoint_col))

    start_date = parse_date(row.get("start_date"))
    final_date = parse_date(row.get("final_date"))
    productivity = quantize_productivity_discount(
        parse_decimal(row.get("productivity_discount"))
    )

    draft = HistoryRowDraft(
        excel_row=excel_row,
        sharepoint_item_id=sharepoint_item_id,
        agent_lan_id=agent_lan,
        agent_name=agent_name,
        leader_lan_id=leader_lan,
        leader_name=leader_name,
        facilitator_lan_id=facilitator_lan,
        facilitator_name=facilitator_name,
        location=str_or_blank(row.get("location")),
        team=str_or_blank(row.get("team")),
        job_title=str_or_blank(row.get("job_title")),
        job_activity=str_or_blank(
            row.get("jobactivity") if not is_empty(row.get("jobactivity")) else row.get("job_activity")
        ),
        journey=str_or_blank(row.get("journey")),
        team_sector=str_or_blank(row.get("team_sector")),
        job_title_sector=str_or_blank(row.get("job_title_sector")),
        job_title_activity=str_or_blank(row.get("job_title_activity")),
        journey_shift=str_or_blank(row.get("journey_shift")),
        inss_type=str_or_blank(row.get("inss_type")),
        external_movement_type=str_or_blank(row.get("external_movement_type")),
        start_date=start_date,
        final_date=final_date,
        productivity_discount=productivity,
        pcd=parse_bool(row.get("pcd")),
        jira=str_or_blank(row.get("jira")),
        formalization=str_or_blank(row.get("formalization")),
        band=str_or_blank(row.get("band")),
        active=parse_active(row.get("active")),
    )

    if not draft.agent_lan_id:
        draft.errors.append("agent_lan_id ausente")
    if not draft.start_date:
        draft.errors.append("start_date inválida")

    # Limites de tamanho alinhados ao modelo.
    for attr, max_len in (
        ("location", 255),
        ("team", 255),
        ("job_title", 255),
        ("job_activity", 255),
        ("journey", 128),
        ("team_sector", 255),
        ("job_title_sector", 255),
        ("job_title_activity", 128),
        ("journey_shift", 64),
        ("band", 64),
        ("inss_type", 64),
        ("external_movement_type", 128),
        ("jira", 512),
        ("formalization", 255),
        ("sharepoint_item_id", 64),
    ):
        value = getattr(draft, attr)
        if isinstance(value, str) and len(value) > max_len:
            draft.errors.append(f"{attr} excede {max_len} caracteres ({len(value)})")

    if draft.productivity_discount is not None:
        # max_digits=5, decimal_places=2 → |value| < 1000
        if abs(draft.productivity_discount) >= Decimal("1000"):
            draft.errors.append(
                f"productivity_discount fora do range ({draft.productivity_discount})"
            )

    if draft.start_date and draft.final_date and draft.final_date < draft.start_date:
        draft.warnings.append("final_date anterior a start_date")

    if draft.active and draft.final_date is not None:
        draft.warnings.append("active=True com final_date preenchida")

    return draft


def detect_history_columns(df) -> dict[str, str | None]:
    return {
        "agent_lan": pick_column(df, AGENT_LAN_COLUMN_ALIASES),
        "agent_name": pick_column(df, AGENT_NAME_ALIASES),
        "leader_lan": pick_column(df, LEADER_LAN_COLUMN_ALIASES),
        "leader_name": pick_column(df, {"leader", "lider", "manager", "gestor"}),
        "facilitator_lan": pick_column(df, FACILITATOR_LAN_COLUMN_ALIASES),
        "facilitator_name": pick_column(df, {"facilitator", "facilitador"}),
        "sharepoint_id": pick_column(df, SHAREPOINT_ID_COLUMN_ALIASES),
    }


def load_and_convert_history_xlsx(path: Path) -> tuple[Any, str, dict[str, str | None], list[HistoryRowDraft]]:
    from apps.workforce.excel_utils import find_sheet

    sheet_name = find_sheet(path, SHEET_HISTORY_ALIASES)
    df = read_sheet(path, SHEET_HISTORY_ALIASES)
    columns = detect_history_columns(df)
    drafts: list[HistoryRowDraft] = []
    for idx, row in df.iterrows():
        drafts.append(convert_history_row(row, int(idx) + 2, columns))
    return df, sheet_name, columns, drafts


def _agent_map() -> dict[str, Agent]:
    return {
        (a.user_lan_id or "").strip().lower(): a
        for a in Agent.objects.all().only("id", "user_lan_id", "full_name")
        if a.user_lan_id
    }


def _normalize_open_actives(resolved: list[ResolvedHistoryRow]) -> None:
    """Garante no máximo um aberto (active + final_date null) por agente — espelho do seed."""
    by_agent: dict[str, list[ResolvedHistoryRow]] = {}
    for item in resolved:
        if item.draft.active and item.draft.final_date is None:
            by_agent.setdefault(item.agent.user_lan_id, []).append(item)
    for rows in by_agent.values():
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda r: r.draft.start_date or date.min, reverse=True)
        keeper = rows[0]
        for extra in rows[1:]:
            extra.draft.active = False
            if extra.draft.final_date is None and keeper.draft.start_date:
                extra.draft.final_date = keeper.draft.start_date
            extra.draft.warnings.append(
                "active aberto duplicado normalizado (mesmo comportamento do seed)"
            )


def resolve_drafts(
    drafts: list[HistoryRowDraft],
    *,
    by_lan: dict[str, Agent] | None = None,
) -> tuple[list[ResolvedHistoryRow], list[HistoryRowDraft], PreflightExtras]:
    agents = by_lan if by_lan is not None else _agent_map()
    existing = list(
        AgentHistory.objects.select_related("agent", "leader", "facilitator").all()
    )
    by_sp: dict[str, list[AgentHistory]] = {}
    by_temporal: dict[tuple, list[AgentHistory]] = {}
    for hist in existing:
        sp = (hist.sharepoint_item_id or "").strip()
        if sp:
            by_sp.setdefault(sp, []).append(hist)
        key = (
            (hist.agent.user_lan_id or "").strip().lower(),
            hist.start_date.isoformat() if hist.start_date else "",
            hist.final_date.isoformat() if hist.final_date else "",
            hist.team or "",
            hist.job_activity or "",
        )
        by_temporal.setdefault(key, []).append(hist)

    unresolved_agents: set[str] = set()
    unresolved_leaders: set[str] = set()
    unresolved_facilitators: set[str] = set()
    name_mismatches: list[dict[str, Any]] = []
    type_errors: list[dict[str, Any]] = []
    rejected: list[HistoryRowDraft] = []
    resolved: list[ResolvedHistoryRow] = []
    used_existing_ids: set[uuid.UUID] = set()

    for draft in drafts:
        if draft.errors:
            type_errors.append(
                {"row": draft.excel_row, "errors": list(draft.errors)}
            )
            rejected.append(draft)
            continue

        agent = agents.get(draft.agent_lan_id)
        if not agent:
            unresolved_agents.add(draft.agent_lan_id)
            draft.errors.append(f"agente não encontrado: {draft.agent_lan_id}")
            rejected.append(draft)
            continue

        if draft.agent_name and normalize_name(draft.agent_name) != normalize_name(
            agent.full_name
        ):
            name_mismatches.append(
                {
                    "row": draft.excel_row,
                    "role": "agent",
                    "lan": draft.agent_lan_id,
                    "xlsx": draft.agent_name,
                    "db": agent.full_name,
                }
            )

        leader = None
        if draft.leader_lan_id:
            leader = agents.get(draft.leader_lan_id)
            if not leader:
                unresolved_leaders.add(draft.leader_lan_id)
                draft.warnings.append(f"líder LAN não resolvido: {draft.leader_lan_id}")
            elif draft.leader_name and normalize_name(draft.leader_name) != normalize_name(
                leader.full_name
            ):
                name_mismatches.append(
                    {
                        "row": draft.excel_row,
                        "role": "leader",
                        "lan": draft.leader_lan_id,
                        "xlsx": draft.leader_name,
                        "db": leader.full_name,
                    }
                )

        facilitator = None
        if draft.facilitator_lan_id:
            facilitator = agents.get(draft.facilitator_lan_id)
            if not facilitator:
                unresolved_facilitators.add(draft.facilitator_lan_id)
                draft.warnings.append(
                    f"facilitador LAN não resolvido: {draft.facilitator_lan_id}"
                )
            elif draft.facilitator_name and normalize_name(
                draft.facilitator_name
            ) != normalize_name(facilitator.full_name):
                name_mismatches.append(
                    {
                        "row": draft.excel_row,
                        "role": "facilitator",
                        "lan": draft.facilitator_lan_id,
                        "xlsx": draft.facilitator_name,
                        "db": facilitator.full_name,
                    }
                )

        if leader and leader.pk == agent.pk:
            leader = None

        reuse_id = None
        match_reason = "new"
        if draft.sharepoint_item_id:
            matches = [
                h
                for h in by_sp.get(draft.sharepoint_item_id, [])
                if h.id not in used_existing_ids
            ]
            if len(matches) == 1:
                reuse_id = matches[0].id
                match_reason = "sharepoint_item_id"
                used_existing_ids.add(reuse_id)

        if reuse_id is None:
            temporal_key = (
                draft.agent_lan_id,
                draft.start_date.isoformat() if draft.start_date else "",
                draft.final_date.isoformat() if draft.final_date else "",
                draft.team,
                draft.job_activity,
            )
            matches = [
                h for h in by_temporal.get(temporal_key, []) if h.id not in used_existing_ids
            ]
            if len(matches) == 1:
                reuse_id = matches[0].id
                match_reason = "agent+start+final+team+activity"
                used_existing_ids.add(reuse_id)

        resolved.append(
            ResolvedHistoryRow(
                draft=draft,
                agent=agent,
                leader=leader,
                facilitator=facilitator,
                reuse_id=reuse_id,
                match_reason=match_reason,
            )
        )

    _normalize_open_actives(resolved)

    temporal_warnings: list[dict[str, Any]] = []
    for draft in list(rejected) + [r.draft for r in resolved]:
        if draft.warnings:
            temporal_warnings.append(
                {"row": draft.excel_row, "warnings": list(draft.warnings)}
            )

    extras = PreflightExtras(
        unresolved_agent_lans=sorted(unresolved_agents),
        unresolved_leader_lans=sorted(unresolved_leaders),
        unresolved_facilitator_lans=sorted(unresolved_facilitators),
        type_or_size_errors=type_errors,
        temporal_warnings=temporal_warnings,
        name_mismatches=name_mismatches,
    )
    return resolved, rejected, extras


@dataclass
class PreflightExtras:
    unresolved_agent_lans: list[str]
    unresolved_leader_lans: list[str]
    unresolved_facilitator_lans: list[str]
    type_or_size_errors: list[dict[str, Any]]
    temporal_warnings: list[dict[str, Any]]
    name_mismatches: list[dict[str, Any]]


def build_preflight(path: Path) -> tuple[PreflightReport, list[ResolvedHistoryRow], list[HistoryRowDraft]]:
    from apps.workforce.models import UserProfile
    from apps.workforce.services.agent_active_reconcile import (
        predict_consistency_after_open_agent_ids,
    )

    df, sheet_name, _columns, drafts = load_and_convert_history_xlsx(path)
    resolved, rejected, extras = resolve_drafts(drafts)
    reuse = sum(1 for r in resolved if r.reuse_id)
    fingerprint = ""
    if resolved:
        fingerprint = resolved[0].fingerprint()

    # Vigência aberta pós-carga: mesmo critério do dashboard (active + final_date null).
    open_after_ids: set = set()
    for row in resolved:
        if row.draft.active and row.draft.final_date is None:
            open_after_ids.add(row.agent.id)
    consistency = predict_consistency_after_open_agent_ids(open_after_ids)

    report = PreflightReport(
        file_path=str(path.resolve()),
        file_sha256=file_sha256(path),
        file_mtime=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        sheet_name=sheet_name,
        rows_read=len(df),
        rows_converted=len(drafts),
        rows_with_errors=len(rejected),
        unresolved_agent_lans=extras.unresolved_agent_lans,
        unresolved_leader_lans=extras.unresolved_leader_lans,
        unresolved_facilitator_lans=extras.unresolved_facilitator_lans,
        type_or_size_errors=extras.type_or_size_errors,
        temporal_warnings=extras.temporal_warnings,
        name_mismatches=extras.name_mismatches,
        expected_inserts=len(resolved),
        expected_uuid_reuse=reuse,
        expected_uuid_new=len(resolved) - reuse,
        current_history_count=AgentHistory.objects.count(),
        expected_after_replace=len(resolved),
        agent_count_unchanged=Agent.objects.count(),
        user_profile_count_unchanged=UserProfile.objects.count(),
        fingerprint_sample=fingerprint,
        agent_active_consistency=consistency,
    )
    return report, resolved, rejected


def history_to_backup_dict(hist: AgentHistory) -> dict[str, Any]:
    return {
        "id": str(hist.id),
        "agent_id": str(hist.agent_id),
        "agent_lan_id": hist.agent.user_lan_id if hist.agent_id else "",
        "leader_id": str(hist.leader_id) if hist.leader_id else None,
        "leader_lan_id": hist.leader.user_lan_id if hist.leader_id else "",
        "facilitator_id": str(hist.facilitator_id) if hist.facilitator_id else None,
        "facilitator_lan_id": hist.facilitator.user_lan_id if hist.facilitator_id else "",
        "location": hist.location,
        "team": hist.team,
        "job_title": hist.job_title,
        "job_activity": hist.job_activity,
        "journey": hist.journey,
        "team_sector": hist.team_sector,
        "job_title_sector": hist.job_title_sector,
        "job_title_activity": hist.job_title_activity,
        "journey_shift": hist.journey_shift,
        "band": hist.band,
        "inss_type": hist.inss_type,
        "external_movement_type": hist.external_movement_type,
        "start_date": hist.start_date.isoformat() if hist.start_date else None,
        "final_date": hist.final_date.isoformat() if hist.final_date else None,
        "productivity_discount": (
            str(hist.productivity_discount)
            if hist.productivity_discount is not None
            else None
        ),
        "pcd": hist.pcd,
        "jira": hist.jira,
        "formalization": hist.formalization,
        "active": hist.active,
        "sharepoint_item_id": hist.sharepoint_item_id or "",
        "created_at": hist.created_at.isoformat() if hist.created_at else None,
    }


def write_backup(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        history_to_backup_dict(h)
        for h in AgentHistory.objects.select_related("agent", "leader", "facilitator").iterator()
    ]
    payload = {
        "created_at": timezone.now().isoformat(),
        "count": len(rows),
        "rows": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _parse_backup_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value[:10])


def restore_backup(path: Path) -> int:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    agents = _agent_map()
    by_id = {str(a.id): a for a in Agent.objects.all().only("id", "user_lan_id")}

    objects: list[AgentHistory] = []
    for row in rows:
        agent = None
        if row.get("agent_id") and row["agent_id"] in by_id:
            agent = by_id[row["agent_id"]]
        elif row.get("agent_lan_id"):
            agent = agents.get(str(row["agent_lan_id"]).strip().lower())
        if not agent:
            raise ValueError(f"Backup: agente não encontrado para histórico {row.get('id')}")

        leader = None
        if row.get("leader_id") and row["leader_id"] in by_id:
            leader = by_id[row["leader_id"]]
        elif row.get("leader_lan_id"):
            leader = agents.get(str(row["leader_lan_id"]).strip().lower())

        facilitator = None
        if row.get("facilitator_id") and row["facilitator_id"] in by_id:
            facilitator = by_id[row["facilitator_id"]]
        elif row.get("facilitator_lan_id"):
            facilitator = agents.get(str(row["facilitator_lan_id"]).strip().lower())

        disc = row.get("productivity_discount")
        objects.append(
            AgentHistory(
                id=uuid.UUID(row["id"]),
                agent=agent,
                leader=leader,
                facilitator=facilitator,
                location=row.get("location") or "",
                team=row.get("team") or "",
                job_title=row.get("job_title") or "",
                job_activity=row.get("job_activity") or "",
                journey=row.get("journey") or "",
                team_sector=row.get("team_sector") or "",
                job_title_sector=row.get("job_title_sector") or "",
                job_title_activity=row.get("job_title_activity") or "",
                journey_shift=row.get("journey_shift") or "",
                band=row.get("band") or "",
                inss_type=row.get("inss_type") or "",
                external_movement_type=row.get("external_movement_type") or "",
                start_date=_parse_backup_date(row.get("start_date")),
                final_date=_parse_backup_date(row.get("final_date")),
                productivity_discount=Decimal(disc) if disc not in (None, "") else None,
                pcd=bool(row.get("pcd")),
                jira=row.get("jira") or "",
                formalization=row.get("formalization") or "",
                active=bool(row.get("active")),
                sharepoint_item_id=row.get("sharepoint_item_id") or "",
            )
        )

    with transaction.atomic():
        _advisory_lock()
        AgentHistory.objects.all().delete()
        AgentHistory.objects.bulk_create(objects, batch_size=500)
        count = AgentHistory.objects.count()
        if count != len(objects):
            raise RuntimeError(
                f"Restauração inconsistente: esperava {len(objects)}, ficou {count}"
            )
    _invalidate_caches()
    return count


def _advisory_lock() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_ADVISORY_LOCK_KEY])


def _invalidate_caches() -> None:
    try:
        from apps.qualidade_operacional.services.performance_cache import (
            bump_quality_cache_version,
        )

        bump_quality_cache_version()
    except Exception:  # noqa: BLE001
        pass


def resolved_to_model(item: ResolvedHistoryRow) -> AgentHistory:
    hist_id = item.reuse_id or uuid.uuid4()
    return AgentHistory(
        id=hist_id,
        agent=item.agent,
        leader=item.leader,
        facilitator=item.facilitator,
        location=item.draft.location,
        team=item.draft.team,
        job_title=item.draft.job_title,
        job_activity=item.draft.job_activity,
        journey=item.draft.journey,
        team_sector=item.draft.team_sector,
        job_title_sector=item.draft.job_title_sector,
        job_title_activity=item.draft.job_title_activity,
        journey_shift=item.draft.journey_shift,
        band=item.draft.band,
        inss_type=item.draft.inss_type,
        external_movement_type=item.draft.external_movement_type,
        start_date=item.draft.start_date,
        final_date=item.draft.final_date,
        productivity_discount=item.draft.productivity_discount,
        pcd=item.draft.pcd,
        jira=item.draft.jira,
        formalization=item.draft.formalization,
        active=item.draft.active,
        sharepoint_item_id=item.draft.sharepoint_item_id,
    )


def replace_agent_history(
    resolved: list[ResolvedHistoryRow],
    *,
    backup_path: Path | None = None,
    allow_unresolved_agents: bool = False,
) -> dict[str, Any]:
    """Substitui agent_history em uma transação. Em falha, o atomic faz rollback;
    se backup_path foi gravado antes, restore_backup pode ser usado manualmente.
    """
    from apps.workforce.models import UserProfile

    if not allow_unresolved_agents:
        bad = [r for r in resolved if not r.agent]
        if bad:
            raise ValueError("Há linhas sem agente resolvido")

    agent_before = Agent.objects.count()
    profile_before = UserProfile.objects.count()
    if backup_path:
        write_backup(backup_path)

    objects = [resolved_to_model(item) for item in resolved]
    fingerprints = {item.fingerprint() for item in resolved}

    try:
        with transaction.atomic():
            _advisory_lock()
            AgentHistory.objects.all().delete()
            AgentHistory.objects.bulk_create(objects, batch_size=500)

            after = AgentHistory.objects.count()
            if after != len(objects):
                raise RuntimeError(
                    f"Contagem pós-carga divergente: {after} != {len(objects)}"
                )
            if Agent.objects.count() != agent_before:
                raise RuntimeError("Contagem de Agent mudou durante a carga")
            if UserProfile.objects.count() != profile_before:
                raise RuntimeError("Contagem de UserProfile mudou durante a carga")

            # Relacionamentos órfãos (não deveria ocorrer com FKs do ORM).
            orphan_leaders = (
                AgentHistory.objects.exclude(leader__isnull=True)
                .exclude(leader_id__in=Agent.objects.values("id"))
                .count()
            )
            if orphan_leaders:
                raise RuntimeError(f"{orphan_leaders} líderes órfãos após carga")
    except Exception:
        if backup_path and backup_path.exists():
            # Falha após delete+insert já é revertida pelo atomic; backup permanece
            # para restauração manual se a falha for pós-commit em invalidação.
            pass
        raise

    _invalidate_caches()
    return {
        "inserted": len(objects),
        "uuid_reused": sum(1 for r in resolved if r.reuse_id),
        "uuid_new": sum(1 for r in resolved if not r.reuse_id),
        "agent_count": agent_before,
        "user_profile_count": profile_before,
        "fingerprint_count": len(fingerprints),
        "backup_path": str(backup_path) if backup_path else None,
    }


def legacy_seed_scalar_fields(row: Any) -> dict[str, Any]:
    """Campos escalares como o seed montava no AgentHistory (para teste de equivalência)."""
    return {
        "location": str_or_blank(row.get("location")),
        "team": str_or_blank(row.get("team")),
        "job_title": str_or_blank(row.get("job_title")),
        "job_activity": str_or_blank(
            row.get("jobactivity") if not is_empty(row.get("jobactivity")) else row.get("job_activity")
        ),
        "journey": str_or_blank(row.get("journey")),
        "team_sector": str_or_blank(row.get("team_sector")),
        "job_title_sector": str_or_blank(row.get("job_title_sector")),
        "job_title_activity": str_or_blank(row.get("job_title_activity")),
        "journey_shift": str_or_blank(row.get("journey_shift")),
        "inss_type": str_or_blank(row.get("inss_type")),
        "external_movement_type": str_or_blank(row.get("external_movement_type")),
        "start_date": parse_date(row.get("start_date")),
        "final_date": parse_date(row.get("final_date")),
        "productivity_discount": quantize_productivity_discount(
            parse_decimal(row.get("productivity_discount"))
        ),
        "pcd": parse_bool(row.get("pcd")),
        "jira": str_or_blank(row.get("jira")),
        "formalization": str_or_blank(row.get("formalization")),
        "band": str_or_blank(row.get("band")),
        "active": parse_active(row.get("active")),
    }


def preflight_to_dict(report: PreflightReport) -> dict[str, Any]:
    return asdict(report)


# ---------------------------------------------------------------------------
# Sessões de import via API (upload → preflight → confirm)
# ---------------------------------------------------------------------------

IMPORT_ROOT = Path(__file__).resolve().parents[3] / "data" / "imports" / "agent_history_xlsx"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB


class AgentHistoryImportError(Exception):
    """Erro de negócio do fluxo de importação via API."""


def _session_dir(import_id: str) -> Path:
    return IMPORT_ROOT / import_id


def save_upload_and_preflight(
    uploaded_file,
    *,
    original_name: str = "agent_history.xlsx",
) -> dict[str, Any]:
    """Persiste o XLSX enviado e devolve preflight + import_id."""
    size = getattr(uploaded_file, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise AgentHistoryImportError(
            f"Arquivo excede o limite de {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    name = (original_name or getattr(uploaded_file, "name", "") or "upload.xlsx").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise AgentHistoryImportError("Envie um arquivo Excel (.xlsx).")

    import_id = uuid.uuid4().hex
    folder = _session_dir(import_id)
    folder.mkdir(parents=True, exist_ok=True)
    xlsx_path = folder / "upload.xlsx"
    with xlsx_path.open("wb") as out:
        for chunk in uploaded_file.chunks():
            out.write(chunk)

    report, resolved, rejected = build_preflight(xlsx_path)
    meta = {
        "import_id": import_id,
        "created_at": timezone.now().isoformat(),
        "original_filename": original_name[:255],
        "xlsx_path": str(xlsx_path),
        "preflight": preflight_to_dict(report),
        "resolved_count": len(resolved),
        "rejected_count": len(rejected),
        "status": "preflight",
    }
    (folder / "session.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "import_id": import_id,
        "preflight": preflight_to_dict(report),
        "can_confirm": len(resolved) > 0,
        "blocking_unresolved_agents": bool(report.unresolved_agent_lans),
        "resolved_count": len(resolved),
        "rejected_count": len(rejected),
    }


def load_session(import_id: str) -> dict[str, Any]:
    folder = _session_dir(import_id)
    meta_path = folder / "session.json"
    if not meta_path.exists():
        raise AgentHistoryImportError("Sessão de importação não encontrada ou expirada.")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def confirm_session(
    import_id: str,
    *,
    allow_partial: bool = False,
    align_agent_active: bool = False,
    performed_by=None,
) -> dict[str, Any]:
    from apps.workforce.services.agent_active_reconcile import apply_align_agent_active

    meta = load_session(import_id)
    xlsx_path = Path(meta["xlsx_path"])
    if not xlsx_path.exists():
        raise AgentHistoryImportError("Arquivo da sessão não encontrado.")

    report, resolved, rejected = build_preflight(xlsx_path)
    if report.unresolved_agent_lans and not allow_partial:
        raise AgentHistoryImportError(
            "Há matrículas de agente não resolvidas. "
            "Corrija o cadastro ou confirme com allow_partial."
        )
    if not resolved:
        raise AgentHistoryImportError("Nenhum registro válido para inserir.")

    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    backup_path = (
        Path(__file__).resolve().parents[3]
        / "data"
        / "backups"
        / f"agent_history_backup_{stamp}_{import_id[:8]}.json"
    )
    write_backup(backup_path)
    try:
        result = replace_agent_history(
            resolved,
            backup_path=None,
            allow_unresolved_agents=allow_partial,
        )
    except Exception as exc:
        raise AgentHistoryImportError(str(exc)) from exc

    align_report = None
    if align_agent_active:
        align_report = apply_align_agent_active(
            dry_run=False,
            performed_by=performed_by,
        )

    meta["status"] = "applied"
    meta["backup_path"] = str(backup_path)
    meta["result"] = result
    meta["align_agent_active"] = bool(align_agent_active)
    meta["align_report"] = align_report
    meta["applied_at"] = timezone.now().isoformat()
    (_session_dir(import_id) / "session.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "import_id": import_id,
        "backup_path": str(backup_path),
        "preflight": preflight_to_dict(report),
        "result": result,
        "align_report": align_report,
        "rejected_count": len(rejected),
    }


def restore_from_backup_path(backup_path: str | Path) -> dict[str, Any]:
    path = Path(backup_path)
    if not path.exists():
        raise AgentHistoryImportError("Backup não encontrado.")
    # Segurança: só restaura de data/backups
    backups_root = (Path(__file__).resolve().parents[3] / "data" / "backups").resolve()
    try:
        path.resolve().relative_to(backups_root)
    except ValueError as exc:
        raise AgentHistoryImportError(
            "Caminho de backup inválido (deve estar em data/backups)."
        ) from exc
    count = restore_backup(path)
    return {"ok": True, "restored": count, "backup_path": str(path)}
