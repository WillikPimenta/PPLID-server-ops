"""Enriquecimento e refresh de demandas Jira."""

from __future__ import annotations

from apps.planejamento_demandas.models import JiraDemanda
from apps.planejamento_demandas.services.jira_description import normalize_jira_description
from apps.planejamento_demandas.services.sync import parse_issue
from apps.planejamento_demandas.services.team_roster import agents_by_lan, resolve_person
from apps.suporte_claro.services.jira_rest import (
    JiraCredentials,
    JiraRestError,
    assign_issue,
    get_issue,
    resolve_jira_credentials,
    resolve_service_jira_credentials,
)


def _jira_creds(user=None) -> JiraCredentials | None:
    if user is not None:
        return resolve_jira_credentials(user)
    return resolve_service_jira_credentials()


def list_demanda_comments(issue_key: str, *, limit: int = 8, user=None) -> list[dict]:
    from apps.suporte_claro.services.jira_rest import list_issue_comments

    creds = _jira_creds(user)
    if creds is None:
        return []
    result = list_issue_comments(issue_key, credentials=creds, max_results=limit)
    if not result.get("ok"):
        return []
    from apps.planejamento_demandas.services.jira_description import normalize_jira_description

    out: list[dict] = []
    for item in result.get("comments") or []:
        normalized = normalize_jira_description(item.get("body"))
        out.append(
            {
                "id": item.get("id"),
                "author": item.get("author") or "",
                "created": item.get("created") or "",
                "body": normalized["plain"] or normalized["html"],
            }
        )
    return out


def comment_demanda(issue_key: str, body: str, *, user=None) -> dict:
    from apps.suporte_claro.services.jira_rest import add_issue_comment

    creds = _jira_creds(user)
    if creds is None:
        raise JiraRestError("Jira não configurado.")
    result = add_issue_comment(issue_key, body, credentials=creds)
    if not result.get("ok"):
        raise JiraRestError(result.get("error") or "Falha ao comentar no Jira.")
    return result


def demanda_description_fields(raw) -> dict[str, str]:
    return normalize_jira_description(raw)


def list_demanda_transitions(issue_key: str, *, user=None) -> list[dict]:
    from apps.suporte_claro.services.jira_rest import list_transitions

    creds = _jira_creds(user)
    if creds is None:
        return []
    result = list_transitions(issue_key, credentials=creds)
    if not result.get("ok"):
        return []
    return result.get("transitions") or []


def transition_demanda(issue_key: str, transition_id: str, *, user=None) -> JiraDemanda:
    from apps.suporte_claro.services.jira_rest import transition_issue

    creds = _jira_creds(user)
    if creds is None:
        raise JiraRestError("Jira não configurado.")
    result = transition_issue(issue_key, transition_id=transition_id, credentials=creds)
    if not result.get("ok"):
        raise JiraRestError(result.get("error") or "Falha na transição Jira.")
    return refresh_demanda_from_jira(issue_key)


def enrich_demanda(obj: JiraDemanda, *, agents=None) -> dict:
    roster = agents if agents is not None else agents_by_lan()
    assignee = resolve_person(obj.assignee_username, agents=roster)
    reporter = resolve_person(obj.reporter_username, agents=roster)
    assignee_ok = bool(obj.assignee_username) and assignee["mapped"]
    return {
        "assignee_person": assignee,
        "reporter_person": reporter,
        "assignee_needs_review": bool(obj.assignee_username) and not assignee["mapped"],
        "assignee_unassigned": not obj.assignee_username,
    }


def refresh_demanda_from_jira(issue_key: str, *, user=None) -> JiraDemanda:
    creds = _jira_creds(user)
    payload = get_issue(issue_key, credentials=creds)
    parsed = parse_issue(payload)
    obj, _ = JiraDemanda.objects.update_or_create(
        issue_key=parsed["issue_key"],
        defaults=parsed,
    )
    return obj


def assign_demanda(issue_key: str, assignee_lan_id: str, *, user=None) -> JiraDemanda:
    creds = _jira_creds(user)
    assign_issue(issue_key, assignee_lan_id, credentials=creds)
    try:
        return refresh_demanda_from_jira(issue_key, user=user)
    except JiraRestError:
        obj = JiraDemanda.objects.filter(issue_key=issue_key).first()
        if obj is None:
            raise
        obj.assignee_username = assignee_lan_id.strip().upper()
        agents = agents_by_lan()
        person = resolve_person(assignee_lan_id, agents=agents)
        obj.assignee_display = person["agent_name"] or assignee_lan_id
        obj.save(update_fields=["assignee_username", "assignee_display", "synced_at"])
        return obj
