"""Sincroniza issues Jira → JiraDemanda."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.planejamento_demandas.models import JiraDemanda, JiraDemandaSyncRun
from apps.planejamento_demandas.services.classifier import classify_demanda_text
from apps.planejamento_demandas.services.jira_description import normalize_jira_description
from apps.planejamento_demandas.services.projects import (
    build_sync_jql,
    build_team_jql,
    build_team_terminal_jql,
    issue_matches_team_queue,
    jira_demandas_project_keys,
)
from apps.planejamento_demandas.services.status_utils import (
    classify_status_name,
    is_status_open,
)
from apps.suporte_claro.services.jira_rest import (
    JiraCredentials,
    JiraRestError,
    jira_browse_url,
    resolve_jira_credentials,
    resolve_service_jira_credentials,
    search_issues,
)

log = logging.getLogger(__name__)

PAGE_SIZE = 100


class SyncCancelled(RuntimeError):
    pass


def _overlap_minutes() -> int:
    from django.conf import settings

    try:
        return max(0, int(getattr(settings, "JIRA_DEMANDAS_SYNC_OVERLAP_MINUTES", 10)))
    except (TypeError, ValueError):
        return 10


def _error_code(exc: Exception) -> str:
    text = str(exc).casefold()
    status_code = getattr(exc, "status_code", None)
    if status_code:
        return f"jira_http_{status_code}"
    if "proxy" in text:
        return "proxy_error"
    if "tempo esgotado" in text or "timeout" in text:
        return "timeout"
    if "vpn" in text or "conectar" in text:
        return "connection_error"
    return "sync_error"


def _sync_credentials(user=None) -> JiraCredentials | None:
    """Portal: PAT do Agent (Headcount) do usuário. CLI/cron: PAT de serviço (.env)."""
    if user is not None:
        return resolve_jira_credentials(user)
    return resolve_service_jira_credentials()


def _sync_credentials_error(user=None) -> str:
    if user is not None:
        return (
            "Cadastre seu token Jira no colaborador (Headcount) para sincronizar demandas."
        )
    return "Configure JIRA_BASE_URL e JIRA_API_TOKEN para sincronizar demandas (CLI)."


def create_sync_run(
    *,
    user=None,
    project_keys: list[str] | None = None,
    force_full: bool = False,
    retry_of: JiraDemandaSyncRun | None = None,
) -> JiraDemandaSyncRun:
    if _sync_credentials(user) is None:
        raise JiraRestError(_sync_credentials_error(user))
    keys = project_keys or jira_demandas_project_keys()
    last_success = JiraDemandaSyncRun.objects.filter(
        status=JiraDemandaSyncRun.STATUS_SUCCESS
    ).first()
    checkpoint = None
    if last_success is not None and not force_full:
        checkpoint = last_success.started_at - timezone.timedelta(minutes=_overlap_minutes())
    mode = (
        JiraDemandaSyncRun.MODE_INCREMENTAL
        if checkpoint is not None
        else JiraDemandaSyncRun.MODE_FULL
    )
    jql_general = build_sync_jql(keys, since=checkpoint)
    jql_team = build_team_jql(keys, active_only=True)
    jql_team_done = build_team_terminal_jql(keys, since=checkpoint)
    return JiraDemandaSyncRun.objects.create(
        status=JiraDemandaSyncRun.STATUS_QUEUED,
        mode=mode,
        projects=",".join(keys),
        jql=f"GERAL: {jql_general} | TIME: {jql_team} | TIME_DONE: {jql_team_done}",
        user=user,
        retry_of=retry_of,
        checkpoint_at=checkpoint,
        heartbeat_at=timezone.now(),
        phase_count=3,
        message="Aguardando início da sincronização…",
    )


def execute_sync_run(run_id: int) -> JiraDemandaSyncRun:
    run = JiraDemandaSyncRun.objects.select_related("user").get(pk=run_id)
    if run.status not in {
        JiraDemandaSyncRun.STATUS_QUEUED,
        JiraDemandaSyncRun.STATUS_RUNNING,
    }:
        return run

    t0 = time.perf_counter()
    fetched = created = updated = duplicates = pages = 0
    run.status = JiraDemandaSyncRun.STATUS_RUNNING
    run.phase = "PRECHECK"
    run.progress_percent = 1
    run.heartbeat_at = timezone.now()
    run.message = "Validando conexão e credenciais do Jira…"
    run.save()

    try:
        credentials = _sync_credentials(run.user)
        if credentials is None:
            raise JiraRestError(_sync_credentials_error(run.user))
        search_issues(
            "ORDER BY updated DESC",
            credentials=credentials,
            max_results=1,
            fields=["summary"],
        )
        _raise_if_cancelled(run.pk)
        keys = [part.strip() for part in run.projects.split(",") if part.strip()]
        phases = (
            ("GERAL", build_sync_jql(keys, since=run.checkpoint_at)),
            ("TIME", build_team_jql(keys, active_only=True)),
            ("TIME_DONE", build_team_terminal_jql(keys, since=run.checkpoint_at)),
        )
        seen_keys: set[str] = set()
        for phase_index, (label, jql) in enumerate(phases, start=1):
            f, c, u, d, p = _sync_jql(
                jql,
                run=run,
                phase=label,
                phase_index=phase_index,
                phase_count=len(phases),
                seen_keys=seen_keys,
                credentials=credentials,
                base_fetched=fetched,
                base_created=created,
                base_updated=updated,
                base_duplicates=duplicates,
                base_pages=pages,
            )
            fetched += f
            created += c
            updated += u
            duplicates += d
            pages += p

        run.status = JiraDemandaSyncRun.STATUS_SUCCESS
        run.total_fetched = fetched
        run.created_count = created
        run.updated_count = updated
        run.duplicate_count = duplicates
        run.pages_processed = pages
        run.progress_percent = 100
        run.phase_fetched = 0
        run.phase_total = 0
        run.message = (
            f"{fetched} issues únicas ({created} novas, {updated} atualizadas; "
            f"{duplicates} duplicadas ignoradas)."
        )
    except SyncCancelled:
        run.refresh_from_db()
        run.status = JiraDemandaSyncRun.STATUS_CANCELLED
        run.error_code = "cancelled_by_user"
        run.message = "Sincronização cancelada pelo usuário."
    except Exception as exc:
        log.exception("Falha sync Jira demandas: %s", exc)
        run.status = JiraDemandaSyncRun.STATUS_FAILED
        run.message = str(exc)
        run.error_code = _error_code(exc)
        run.total_fetched = fetched
        run.created_count = created
        run.updated_count = updated
        run.duplicate_count = duplicates
        run.pages_processed = pages
    finally:
        run.duration_seconds = round(time.perf_counter() - t0, 2)
        run.finished_at = timezone.now()
        run.heartbeat_at = timezone.now()
        run.save()

    return run


def sync_jira_demandas(
    *, user=None, project_keys: list[str] | None = None, force_full: bool = False
) -> JiraDemandaSyncRun:
    """Sync síncrono — uso CLI/management command."""
    run = create_sync_run(user=user, project_keys=project_keys, force_full=force_full)
    run = execute_sync_run(run.pk)
    if run.status == JiraDemandaSyncRun.STATUS_FAILED:
        raise JiraRestError(run.message)
    return run


def _raise_if_cancelled(run_id: int) -> None:
    state = JiraDemandaSyncRun.objects.filter(pk=run_id).values(
        "status", "cancel_requested_at"
    ).first()
    if not state or state["cancel_requested_at"] is not None or state["status"] == JiraDemandaSyncRun.STATUS_CANCELLED:
        raise SyncCancelled()


def _parse_jira_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = parse_datetime(str(value))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _user_field(raw: dict | None) -> tuple[str, str]:
    if not raw:
        return "", ""
    display = str(raw.get("displayName") or raw.get("name") or "").strip()
    username = str(raw.get("name") or raw.get("key") or "").strip()
    return display, username


def _list_field(items: list | None, key: str = "name") -> list[str]:
    if not items:
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict):
            val = str(item.get(key) or "").strip()
        else:
            val = str(item).strip()
        if val:
            out.append(val)
    return out


def _description_excerpt(raw: Any, limit: int = 600) -> str:
    text = normalize_jira_description(raw)["plain"]
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def parse_issue(issue: dict[str, Any]) -> dict[str, Any]:
    key = str(issue.get("key") or "").strip()
    fields = issue.get("fields") or {}
    project = fields.get("project") or {}
    project_key = str(project.get("key") or "").strip()
    project_name = str(project.get("name") or "").strip()
    summary = str(fields.get("summary") or "").strip()
    status = fields.get("status") or {}
    status_name = str(status.get("name") or "").strip()
    status_kind = classify_status_name(status_name)
    assignee_display, assignee_username = _user_field(fields.get("assignee"))
    reporter_display, reporter_username = _user_field(fields.get("reporter"))
    priority = fields.get("priority") or {}
    priority_name = str(priority.get("name") or "").strip()
    issue_type = fields.get("issuetype") or {}
    issue_type_name = str(issue_type.get("name") or "").strip()
    labels = _list_field(fields.get("labels"))
    components = _list_field(fields.get("components"))
    description = _description_excerpt(fields.get("description"))
    blob_parts = [summary, description, " ".join(labels), " ".join(components)]
    categoria, portal_path = classify_demanda_text(*blob_parts)
    in_team = issue_matches_team_queue(
        project_key=project_key,
        assignee_username=assignee_username,
        reporter_username=reporter_username,
    )
    return {
        "issue_key": key,
        "project_key": project_key,
        "project_name": project_name,
        "summary": summary[:512],
        "description_excerpt": description,
        "status_name": status_name[:128],
        "status_kind": status_kind,
        "is_open": is_status_open(status_name),
        "assignee_display": assignee_display[:255],
        "assignee_username": assignee_username[:128],
        "reporter_display": reporter_display[:255],
        "reporter_username": reporter_username[:128],
        "in_team_queue": in_team,
        "priority_name": priority_name[:64],
        "issue_type": issue_type_name[:128],
        "labels": labels,
        "components": components,
        "categoria": categoria,
        "portal_path": portal_path,
        "jira_url": jira_browse_url(key),
        "created_at_jira": _parse_jira_dt(fields.get("created")),
        "updated_at_jira": _parse_jira_dt(fields.get("updated")),
    }


def _upsert_demanda(parsed: dict[str, Any]) -> bool:
    """Persiste issue; retorna True se foi criada. Tolera corrida entre workers."""
    issue_key = parsed["issue_key"]
    try:
        with transaction.atomic():
            _, was_created = JiraDemanda.objects.update_or_create(
                issue_key=issue_key,
                defaults=parsed,
            )
        return was_created
    except IntegrityError:
        log.warning("IntegrityError ao upsert %s; aplicando update", issue_key)
        if JiraDemanda.objects.filter(issue_key=issue_key).update(**parsed):
            return False
        _, was_created = JiraDemanda.objects.update_or_create(
            issue_key=issue_key,
            defaults=parsed,
        )
        return was_created


def _sync_jql(
    jql: str,
    *,
    run: JiraDemandaSyncRun | None = None,
    phase: str = "",
    phase_index: int = 1,
    phase_count: int = 1,
    seen_keys: set[str] | None = None,
    credentials: JiraCredentials | None = None,
    base_fetched: int = 0,
    base_created: int = 0,
    base_updated: int = 0,
    base_duplicates: int = 0,
    base_pages: int = 0,
) -> tuple[int, int, int, int, int]:
    created = 0
    updated = 0
    fetched = 0
    duplicates = 0
    pages = 0
    seen = seen_keys if seen_keys is not None else set()
    start_at = 0
    while True:
        if run is not None:
            _raise_if_cancelled(run.pk)
        payload = search_issues(
            jql,
            credentials=credentials,
            start_at=start_at,
            max_results=PAGE_SIZE,
        )
        issues = payload.get("issues") or []
        if not issues:
            break
        total = int(payload.get("total") or 0)
        pages += 1
        for issue in issues:
            issue_key = str(issue.get("key") or "").strip()
            if not issue_key:
                continue
            if issue_key in seen:
                duplicates += 1
                continue
            seen.add(issue_key)
            parsed = parse_issue(issue)
            fetched += 1
            if _upsert_demanda(parsed):
                created += 1
            else:
                updated += 1
        if run is not None:
            _raise_if_cancelled(run.pk)
            total_fetched = base_fetched + fetched
            total_created = base_created + created
            total_updated = base_updated + updated
            total_duplicates = base_duplicates + duplicates
            total_pages = base_pages + pages
            label = f" [{phase}]" if phase else ""
            api_fetched = min(total, start_at + len(issues)) if total else start_at + len(issues)
            phase_ratio = min(1.0, api_fetched / total) if total else 1.0
            progress = round(((phase_index - 1 + phase_ratio) / max(1, phase_count)) * 100, 1)
            run.total_fetched = total_fetched
            run.created_count = total_created
            run.updated_count = total_updated
            run.duplicate_count = total_duplicates
            run.pages_processed = total_pages
            run.phase = phase
            run.phase_index = phase_index
            run.phase_count = phase_count
            run.phase_fetched = api_fetched
            run.phase_total = total
            run.progress_percent = progress
            run.heartbeat_at = timezone.now()
            run.message = (
                f"Sincronizando{label}… {total_fetched} issues únicas; "
                f"{total_duplicates} duplicadas ignoradas"
            )
            run.save(
                update_fields=[
                    "total_fetched",
                    "created_count",
                    "updated_count",
                    "duplicate_count",
                    "pages_processed",
                    "phase",
                    "phase_index",
                    "phase_count",
                    "phase_fetched",
                    "phase_total",
                    "progress_percent",
                    "heartbeat_at",
                    "message",
                ]
            )
        start_at += len(issues)
        if start_at >= total or len(issues) < PAGE_SIZE:
            break
    return fetched, created, updated, duplicates, pages
