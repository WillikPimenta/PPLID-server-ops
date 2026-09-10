"""Projetos Jira e JQL da fila de demandas (geral + time)."""

from __future__ import annotations

import math
from datetime import datetime

from django.conf import settings
from django.utils import timezone

from apps.planejamento_demandas.services.status_utils import jql_status_not_terminal

DEFAULT_PROJECT_KEYS = [
    "PPLID",
    "ANTIFRAUDE",
    "PAPB",
    "AFOP",
    "PRODUCTGAQ",
    "IDAS",
    "Qualidade ID&F",
]

DEFAULT_TEAM_LAN_IDS = [
    "C91763A",
    "C92928A",
    "C91893A",
    "C93213A",
    "C93199A",
    "C93048A",
    "C93078A",
    "C91903A",
    "C93189A",
    "C93233A",
    "C93123A",
]


def _split_csv(raw: str | list | tuple | None) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [p.strip() for p in raw.split(",") if p.strip()]
    return [str(p).strip() for p in raw if str(p).strip()]


def jira_demandas_project_keys() -> list[str]:
    raw = getattr(settings, "JIRA_DEMANDAS_PROJECT_KEYS", None)
    if raw:
        return _split_csv(raw)
    keys = []
    for name in (
        "JIRA_PROFILE_PLANEJAMENTO_PROJECT_KEY",
        "JIRA_PROFILE_PROCESSOS_PROJECT_KEY",
    ):
        val = str(getattr(settings, name, "") or "").strip()
        if val and val not in keys:
            keys.append(val)
    return keys or list(DEFAULT_PROJECT_KEYS)


def jira_demandas_team_lan_ids() -> list[str]:
    raw = getattr(settings, "JIRA_DEMANDAS_TEAM_LAN_IDS", None)
    ids = _split_csv(raw) if raw else list(DEFAULT_TEAM_LAN_IDS)
    return [i.upper() for i in ids]


def jira_demandas_sync_days() -> int:
    try:
        return max(1, int(getattr(settings, "JIRA_DEMANDAS_SYNC_DAYS", 120)))
    except (TypeError, ValueError):
        return 120


def _jql_project_token(project: str) -> str:
    token = (project or "").strip()
    if not token:
        return ""
    if " " in token or "&" in token or "-" in token and not token.isupper():
        return f'"{token}"'
    return token


def _jql_project_in(projects: list[str]) -> str:
    tokens = [_jql_project_token(p) for p in projects if p]
    return ", ".join(tokens)


def _jql_user_in(field: str, lan_ids: list[str]) -> str:
    users = ", ".join(lan_ids)
    return f"{field} in ({users})"


def jira_demandas_primary_project() -> str:
    return str(getattr(settings, "JIRA_DEMANDAS_PRIMARY_PROJECT", "PPLID") or "PPLID").strip()


def build_sync_jql(
    project_keys: list[str] | None = None,
    *,
    since: datetime | None = None,
) -> str:
    keys = project_keys or jira_demandas_project_keys()
    projects = _jql_project_in(keys)
    if since is None:
        window = f"-{jira_demandas_sync_days()}d"
    else:
        if timezone.is_naive(since):
            since = timezone.make_aware(since, timezone.get_current_timezone())
        elapsed = max(1, math.ceil((timezone.now() - since).total_seconds() / 60))
        window = f"-{elapsed}m"
    return (
        f"project in ({projects}) AND updated >= {window} "
        "ORDER BY updated DESC, key DESC"
    )


def build_pplid_sync_jql() -> str:
    days = jira_demandas_sync_days()
    return f"project = PPLID AND updated >= -{days}d ORDER BY updated DESC, key DESC"


def build_team_terminal_jql(
    project_keys: list[str] | None = None,
    lan_ids: list[str] | None = None,
    *,
    since: datetime | None = None,
    pplid_only: bool = False,
) -> str:
    """Issues concluídas/fechadas do time dentro da janela de sync."""
    if pplid_only:
        keys = [jira_demandas_primary_project()]
    else:
        keys = project_keys or jira_demandas_project_keys()
    users = lan_ids or jira_demandas_team_lan_ids()
    projects = _jql_project_in(keys)
    assignee_clause = _jql_user_in("assignee", users)
    reporter_clause = _jql_user_in("reporter", users)
    if since is None:
        window = f"-{jira_demandas_sync_days()}d"
    else:
        if timezone.is_naive(since):
            since = timezone.make_aware(since, timezone.get_current_timezone())
        elapsed = max(1, math.ceil((timezone.now() - since).total_seconds() / 60))
        window = f"-{elapsed}m"
    clauses = [
        f"project in ({projects})",
        f"({assignee_clause} OR {reporter_clause})",
        "statusCategory = Done",
        f"updated >= {window}",
    ]
    return " AND ".join(clauses) + " ORDER BY updated DESC, key DESC"


def build_team_jql(
    project_keys: list[str] | None = None,
    lan_ids: list[str] | None = None,
    *,
    active_only: bool = True,
    pplid_only: bool = False,
) -> str:
    if pplid_only:
        keys = [jira_demandas_primary_project()]
    else:
        keys = project_keys or jira_demandas_project_keys()
    users = lan_ids or jira_demandas_team_lan_ids()
    projects = _jql_project_in(keys)
    assignee_clause = _jql_user_in("assignee", users)
    reporter_clause = _jql_user_in("reporter", users)
    clauses = [
        f"project in ({projects})",
        f"({assignee_clause} OR {reporter_clause})",
    ]
    terminal = jql_status_not_terminal()
    if active_only and terminal:
        clauses.append(terminal)
    return " AND ".join(clauses) + " ORDER BY Rank ASC"


def issue_matches_team_queue(
    *,
    project_key: str,
    assignee_username: str,
    reporter_username: str,
    project_keys: list[str] | None = None,
    lan_ids: list[str] | None = None,
) -> bool:
    allowed = project_keys or jira_demandas_project_keys()
    allowed_upper = {a.upper() for a in allowed}
    allowed_set = set(allowed)
    pk = (project_key or "").strip()
    if pk not in allowed_set and pk.upper() not in allowed_upper:
        return False
    team = {u.upper() for u in (lan_ids or jira_demandas_team_lan_ids())}
    assignee = (assignee_username or "").strip().upper()
    reporter = (reporter_username or "").strip().upper()
    return assignee in team or reporter in team
