# -*- coding: utf-8 -*-
"""Cliente REST Jira Server/DC para formalização Suporte Claro."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from django.conf import settings

from apps.suporte_claro.services.jira_profiles import build_issue_fields, get_rest_profile

logger = logging.getLogger(__name__)


class JiraRestError(RuntimeError):
    """Falha na API Jira (config, HTTP ou payload)."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass(frozen=True)
class JiraCredentials:
    """Credenciais por usuário. Username = LAN ID do headcount; token = PAT no Agent."""

    username: str
    api_token: str


def jira_base_url() -> str:
    return str(getattr(settings, "JIRA_BASE_URL", "") or "").rstrip("/")


def jira_auth_mode() -> str:
    """bearer = PAT Jira DC (padrão Experian); basic = user+senha/token Cloud-style."""
    mode = str(getattr(settings, "JIRA_AUTH_MODE", "bearer") or "bearer").strip().lower()
    if mode in ("basic", "bearer"):
        return mode
    return "bearer"


def jira_base_configured() -> bool:
    """Instância Jira configurada (URL). Tokens são por usuário no Agent."""
    return bool(jira_base_url())


def jira_timeout_seconds() -> float:
    try:
        return float(getattr(settings, "JIRA_HTTP_TIMEOUT", 30) or 30)
    except (TypeError, ValueError):
        return 30.0


def jira_proxy_mode() -> str:
    if str(getattr(settings, "JIRA_PROXY_URL", "") or "").strip():
        return "explicit"
    if bool(getattr(settings, "JIRA_TRUST_ENV_PROXY", False)):
        return "environment"
    return "disabled"


def _configure_session_proxy(session: requests.Session) -> None:
    """Aplica somente proxy Jira explícito, salvo opt-in para variáveis do ambiente."""
    proxy_url = str(getattr(settings, "JIRA_PROXY_URL", "") or "").strip()
    session.trust_env = bool(getattr(settings, "JIRA_TRUST_ENV_PROXY", False))
    if not proxy_url:
        return
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise JiraRestError("JIRA_PROXY_URL inválida; use uma URL http(s) completa.")
    if parsed.hostname.lower() in {"127.0.0.1", "localhost", "::1"} and parsed.port == 9:
        raise JiraRestError(
            "Proxy Jira inválido (loopback na porta 9). Remova JIRA_PROXY_URL ou configure o proxy corporativo."
        )
    session.trust_env = False
    session.proxies.update({"http": proxy_url, "https": proxy_url})


def resolve_jira_credentials(user) -> JiraCredentials | None:
    """Resolve LAN ID + PAT do Agent vinculado ao usuário do portal."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        from apps.escala_flex.services.permissions import get_agent_for_user

        agent = get_agent_for_user(user)
    except Exception:
        agent = None
    if agent is None:
        return None
    username = (agent.user_lan_id or "").strip()
    token = (getattr(agent, "jira_api_token", None) or "").strip()
    if not username or not token:
        return None
    return JiraCredentials(username=username, api_token=token)


def user_jira_configured(user) -> bool:
    return jira_base_configured() and resolve_jira_credentials(user) is not None


def save_user_jira_token(user, token: str) -> tuple[bool, str]:
    """
    Persiste PAT no Agent do usuário.
    Retorno: (ok, message).
    """
    try:
        from apps.escala_flex.services.permissions import get_agent_for_user

        agent = get_agent_for_user(user)
    except Exception:
        agent = None
    if agent is None:
        return False, "Usuário sem colaborador (Agent) vinculado no headcount."
    cleaned = (token or "").strip()
    if not cleaned:
        return False, "Informe o Personal Access Token do Jira."
    agent.jira_api_token = cleaned
    agent.save(update_fields=["jira_api_token", "updated_at"])
    return True, "Token Jira salvo no seu cadastro de colaborador."


def clear_user_jira_token(user) -> tuple[bool, str]:
    try:
        from apps.escala_flex.services.permissions import get_agent_for_user

        agent = get_agent_for_user(user)
    except Exception:
        agent = None
    if agent is None:
        return False, "Usuário sem colaborador (Agent) vinculado no headcount."
    agent.jira_api_token = ""
    agent.save(update_fields=["jira_api_token", "updated_at"])
    return True, "Token Jira removido."


def _session(credentials: JiraCredentials) -> requests.Session:
    session = requests.Session()
    _configure_session_proxy(session)
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if jira_auth_mode() == "basic":
        session.auth = (credentials.username, credentials.api_token)
    else:
        headers["Authorization"] = f"Bearer {credentials.api_token}"
    session.headers.update(headers)
    return session


def _session_for_attachments(credentials: JiraCredentials) -> requests.Session:
    """Sessão sem Content-Type fixo — o multipart define o boundary."""
    session = requests.Session()
    _configure_session_proxy(session)
    headers = {
        "Accept": "application/json",
        "X-Atlassian-Token": "no-check",
    }
    if jira_auth_mode() == "basic":
        session.auth = (credentials.username, credentials.api_token)
    else:
        headers["Authorization"] = f"Bearer {credentials.api_token}"
    session.headers.update(headers)
    return session


_SSO_GATEWAY_HINT = (
    "O gateway do Jira interceptou a chamada (SSO/VPN) em vez da API REST. "
    "Conecte-se à VPN corporativa e confira se o Personal Access Token ainda é válido."
)


def _is_html_body(resp: requests.Response) -> bool:
    content_type = (resp.headers.get("Content-Type") or "").lower()
    text = (resp.text or "").strip().lower()
    return (
        "html" in content_type
        or text.startswith("<!doctype")
        or text.startswith("<html")
        or "<html" in text[:200]
    )


def _is_sso_interstitial(resp: requests.Response) -> bool:
    """
    Detecta página intermediária do gateway (ex.: F5/Okta 'Loading') antes da API Jira.

    Seguindo esse 302, o requests converte POST→GET e a API responde 405
    com {"message":"HTTP 405 Method Not Allowed"} — erro enganoso no cadastro.
    """
    if resp.status_code not in (301, 302, 303, 307, 308):
        return False
    if _is_html_body(resp):
        return True
    location = (resp.headers.get("Location") or "").strip().rstrip("/")
    request_url = (getattr(resp, "url", None) or "").strip().rstrip("/")
    return bool(location and request_url and location == request_url)


def _jira_request(session: requests.Session, method: str, url: str, **kwargs) -> requests.Response:
    """
    Executa chamada à API Jira.

    Mutações não seguem redirect: um 302 de SSO transformaria POST em GET e
    geraria HTTP 405 Method Not Allowed na UI de cadastro.
    """
    method_u = (method or "GET").upper()
    if method_u in {"POST", "PUT", "PATCH", "DELETE"}:
        kwargs.setdefault("allow_redirects", False)
    try:
        return session.request(method_u, url, **kwargs)
    except requests.exceptions.ProxyError as exc:
        raise JiraRestError(
            "Não foi possível conectar ao proxy do Jira. Revise JIRA_PROXY_URL e a VPN corporativa."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise JiraRestError(
            f"Tempo esgotado ao acessar o Jira ({jira_timeout_seconds():g}s)."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise JiraRestError(
            "Não foi possível conectar ao Jira. Confirme a VPN e a configuração de rede."
        ) from exc


def _format_jira_error(resp: requests.Response) -> str:
    if _is_sso_interstitial(resp):
        return _SSO_GATEWAY_HINT

    text = (resp.text or "").strip()

    if _is_html_body(resp):
        if resp.status_code == 401:
            return (
                "Unauthorized (401) no gateway Jira/SSO. "
                "Confirme o Personal Access Token salvo no seu Agent e se ele ainda está ativo."
            )
        if resp.status_code in (301, 302, 303, 307, 308):
            return _SSO_GATEWAY_HINT
        return f"HTTP {resp.status_code} (resposta HTML do gateway, não JSON da API Jira)."

    try:
        data = resp.json()
    except Exception:
        return text[:500] if text else f"HTTP {resp.status_code}"

    if isinstance(data, dict):
        errors = data.get("errorMessages") or []
        field_errors = data.get("errors") or {}
        parts: list[str] = []
        if isinstance(errors, list):
            parts.extend(str(e) for e in errors if e)
        if isinstance(field_errors, dict):
            for key, val in field_errors.items():
                parts.append(f"{key}: {val}")
        if parts:
            return "; ".join(parts)[:500]
        msg = data.get("message")
        status_code = data.get("status-code") or data.get("statusCode") or resp.status_code
        # Gateway SSO → redirect → GET /issue → JSON 405 enganoso.
        if status_code == 405 or resp.status_code == 405:
            return (
                "Jira respondeu HTTP 405 (método não permitido). "
                "Isso costuma ocorrer quando o gateway SSO intercepta o POST de criação. "
                "Conecte-se à VPN e confira o Personal Access Token."
            )
        if msg:
            return str(msg)[:500]
    return f"HTTP {resp.status_code}"


def create_issue(
    profile_key: str,
    *,
    summary: str,
    description: str,
    credentials: JiraCredentials | None = None,
    user=None,
    assignee_name: str | None = None,
    reporter_name: str | None = None,
) -> dict[str, Any]:
    """
    Cria issue via POST /rest/api/2/issue.

    Credenciais: `credentials` explícitas ou resolvidas de `user` (Agent).
    assignee: LAN ID (padrão = username do PAT). reporter só se passado explicitamente
    (muitos workflows não permitem setar reporter no create).
    Retorno: {ok, issue_key?, error?, status_code?}
    """
    if not jira_base_configured():
        return {
            "ok": False,
            "issue_key": None,
            "error": "Integração Jira não configurada. Defina JIRA_BASE_URL no backend.",
            "status_code": None,
        }

    creds = credentials or (resolve_jira_credentials(user) if user is not None else None)
    if creds is None:
        return {
            "ok": False,
            "issue_key": None,
            "error": (
                "Seu Personal Access Token do Jira não está cadastrado. "
                "Salve o token no modal de formalização ou no Headcount (cadastro Agent)."
            ),
            "status_code": None,
        }

    lan = creds.username
    profile = get_rest_profile(profile_key)
    # Não setar reporter por padrão: muitos projetos Experian não expõem o campo no create screen.
    # Com PAT do usuário, o Jira já atribui o reporter = autenticado.
    # Processos/ANTIFRAUDE: create screen sem assignee — omitir mesmo se LAN for passado.
    effective_assignee = None
    if profile.set_assignee_on_create:
        effective_assignee = assignee_name if assignee_name is not None else lan
    fields = build_issue_fields(
        profile,
        summary=summary,
        description=description,
        assignee_name=effective_assignee,
        reporter_name=reporter_name,
    )
    url = f"{jira_base_url()}/rest/api/2/issue"
    timeout = jira_timeout_seconds()

    try:
        with _session(creds) as session:
            resp = _jira_request(
                session, "POST", url, json={"fields": fields}, timeout=timeout
            )
    except requests.Timeout:
        return {
            "ok": False,
            "issue_key": None,
            "error": "Timeout ao criar issue no Jira.",
            "status_code": None,
        }
    except requests.RequestException as exc:
        logger.warning("jira_rest create_issue network error: %s", exc)
        return {
            "ok": False,
            "issue_key": None,
            "error": f"Falha de rede ao falar com o Jira: {exc}",
            "status_code": None,
        }

    if _is_sso_interstitial(resp):
        return {
            "ok": False,
            "issue_key": None,
            "error": _SSO_GATEWAY_HINT,
            "status_code": resp.status_code,
        }

    if resp.status_code in (200, 201):
        try:
            data = resp.json()
        except Exception:
            return {
                "ok": False,
                "issue_key": None,
                "error": "Resposta Jira inválida (sem JSON).",
                "status_code": resp.status_code,
            }
        key = str((data or {}).get("key") or "").strip().upper()
        if not key:
            return {
                "ok": False,
                "issue_key": None,
                "error": "Jira respondeu sem issue key.",
                "status_code": resp.status_code,
            }
        return {"ok": True, "issue_key": key, "error": None, "status_code": resp.status_code}

    error = _format_jira_error(resp)
    logger.info(
        "jira_rest create_issue failed profile=%s user=%s status=%s error=%s",
        profile.key,
        creds.username,
        resp.status_code,
        error,
    )
    return {
        "ok": False,
        "issue_key": None,
        "error": error,
        "status_code": resp.status_code,
    }


def list_transitions(
    issue_key: str,
    *,
    credentials: JiraCredentials | None = None,
    user=None,
) -> dict[str, Any]:
    """GET /rest/api/2/issue/{key}/transitions → {ok, transitions:[{id,name}], error?}."""
    key = (issue_key or "").strip().upper()
    if not key:
        return {"ok": False, "transitions": [], "error": "Issue key inválida."}
    if not jira_base_configured():
        return {"ok": False, "transitions": [], "error": "Integração Jira não configurada."}

    creds = credentials or (resolve_jira_credentials(user) if user is not None else None)
    if creds is None:
        return {"ok": False, "transitions": [], "error": "Token Jira não cadastrado."}

    url = f"{jira_base_url()}/rest/api/2/issue/{key}/transitions"
    try:
        with _session(creds) as session:
            resp = _jira_request(session, "GET", url, timeout=jira_timeout_seconds())
    except requests.Timeout:
        return {"ok": False, "transitions": [], "error": "Timeout ao listar transições Jira."}
    except requests.RequestException as exc:
        return {"ok": False, "transitions": [], "error": f"Falha de rede: {exc}"}

    if _is_sso_interstitial(resp) or resp.status_code != 200:
        return {"ok": False, "transitions": [], "error": _format_jira_error(resp)}

    try:
        data = resp.json() or {}
    except Exception:
        return {"ok": False, "transitions": [], "error": "Resposta Jira inválida (sem JSON)."}

    raw = data.get("transitions") or []
    transitions: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("id") or "").strip()
        name = str(item.get("name") or "").strip()
        to_obj = item.get("to") if isinstance(item.get("to"), dict) else {}
        to_name = str((to_obj or {}).get("name") or "").strip()
        if tid and name:
            transitions.append({"id": tid, "name": name, "to": to_name})
    return {"ok": True, "transitions": transitions, "error": None}


def add_issue_comment(
    issue_key: str,
    body: str,
    *,
    credentials: JiraCredentials | None = None,
    user=None,
) -> dict[str, Any]:
    """POST /rest/api/2/issue/{key}/comment."""
    key = (issue_key or "").strip().upper()
    text = (body or "").strip()
    if not key:
        return {"ok": False, "error": "Issue key inválida."}
    if not text:
        return {"ok": False, "error": "Comentário vazio."}
    if not jira_base_configured():
        return {"ok": False, "error": "Integração Jira não configurada."}

    creds = credentials or (resolve_jira_credentials(user) if user is not None else None)
    if creds is None:
        return {"ok": False, "error": "Token Jira não cadastrado."}

    url = f"{jira_base_url()}/rest/api/2/issue/{key}/comment"
    try:
        with _session(creds) as session:
            resp = _jira_request(
                session, "POST", url, json={"body": text}, timeout=jira_timeout_seconds()
            )
    except requests.Timeout:
        return {"ok": False, "error": "Timeout ao comentar no Jira."}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"Falha de rede: {exc}"}

    if _is_sso_interstitial(resp):
        return {"ok": False, "error": _SSO_GATEWAY_HINT, "issue_key": key}

    if resp.status_code in (200, 201):
        comment_id = None
        try:
            comment_id = str((resp.json() or {}).get("id") or "") or None
        except Exception:
            comment_id = None
        return {"ok": True, "error": None, "comment_id": comment_id, "issue_key": key}

    return {"ok": False, "error": _format_jira_error(resp), "issue_key": key}


def list_issue_comments(
    issue_key: str,
    *,
    credentials: JiraCredentials | None = None,
    max_results: int = 10,
) -> dict[str, Any]:
    """GET /rest/api/2/issue/{key}/comment — comentários recentes."""
    key = (issue_key or "").strip().upper()
    if not key:
        return {"ok": False, "comments": [], "error": "Issue key inválida."}
    if not jira_base_configured():
        return {"ok": False, "comments": [], "error": "Integração Jira não configurada."}

    creds = credentials
    if creds is None:
        return {"ok": False, "comments": [], "error": "Token Jira não cadastrado."}

    url = f"{jira_base_url()}/rest/api/2/issue/{key}/comment"
    try:
        with _session(creds) as session:
            resp = _jira_request(
                session,
                "GET",
                url,
                params={"maxResults": max(1, min(max_results, 50)), "orderBy": "-created"},
                timeout=jira_timeout_seconds(),
            )
    except requests.Timeout:
        return {"ok": False, "comments": [], "error": "Timeout ao listar comentários Jira."}
    except requests.RequestException as exc:
        return {"ok": False, "comments": [], "error": f"Falha de rede: {exc}"}

    if _is_sso_interstitial(resp):
        return {"ok": False, "comments": [], "error": _SSO_GATEWAY_HINT}

    if resp.status_code != 200:
        return {"ok": False, "comments": [], "error": _format_jira_error(resp)}

    payload = resp.json() or {}
    comments = []
    for item in payload.get("comments") or []:
        author = item.get("author") or {}
        comments.append(
            {
                "id": str(item.get("id") or ""),
                "author": str(author.get("displayName") or author.get("name") or ""),
                "created": str(item.get("created") or ""),
                "body": item.get("body") or "",
            }
        )
    return {"ok": True, "comments": comments, "error": None}


def delete_issue_comment(
    issue_key: str,
    comment_id: str,
    *,
    credentials: JiraCredentials | None = None,
    user=None,
) -> dict[str, Any]:
    """DELETE /rest/api/2/issue/{key}/comment/{id}."""
    key = (issue_key or "").strip().upper()
    cid = str(comment_id or "").strip()
    if not key:
        return {"ok": False, "error": "Issue key inválida.", "issue_key": key, "comment_id": cid}
    if not cid:
        return {"ok": False, "error": "Comment id inválido.", "issue_key": key, "comment_id": cid}
    if not jira_base_configured():
        return {
            "ok": False,
            "error": "Integração Jira não configurada.",
            "issue_key": key,
            "comment_id": cid,
        }

    creds = credentials or (resolve_jira_credentials(user) if user is not None else None)
    if creds is None:
        return {
            "ok": False,
            "error": "Token Jira não cadastrado.",
            "issue_key": key,
            "comment_id": cid,
        }

    url = f"{jira_base_url()}/rest/api/2/issue/{key}/comment/{cid}"
    try:
        with _session(creds) as session:
            resp = _jira_request(session, "DELETE", url, timeout=jira_timeout_seconds())
    except requests.Timeout:
        return {
            "ok": False,
            "error": "Timeout ao excluir comentário no Jira.",
            "issue_key": key,
            "comment_id": cid,
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": f"Falha de rede: {exc}",
            "issue_key": key,
            "comment_id": cid,
        }

    if _is_sso_interstitial(resp):
        return {
            "ok": False,
            "error": _SSO_GATEWAY_HINT,
            "issue_key": key,
            "comment_id": cid,
        }

    # 204 No Content típico; 404 = já removido → tratar como sucesso idempotente.
    if resp.status_code in (200, 204, 404):
        return {"ok": True, "error": None, "issue_key": key, "comment_id": cid}

    return {
        "ok": False,
        "error": _format_jira_error(resp),
        "issue_key": key,
        "comment_id": cid,
    }


def delete_formalized_jira_comments(comentario, user) -> dict[str, Any]:
    """
    Remove no Jira os comentários referenciados em formalizado_jira_refs.
    Best-effort: retorna itens por issue; não impede exclusão no portal.
    """
    refs = getattr(comentario, "formalizado_jira_refs", None) or []
    if not isinstance(refs, list) or not refs:
        keys = (getattr(comentario, "formalizado_issue_keys", None) or "").strip()
        if keys and getattr(comentario, "formalizado_externo", False):
            return {
                "ok": False,
                "skipped": True,
                "items": [],
                "error": (
                    "Comentário formalizado sem id Jira armazenado "
                    "(criado antes do vínculo). Remova manualmente no Jira se necessário."
                ),
            }
        return {"ok": True, "skipped": True, "items": [], "error": None}

    items = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        issue_key = str(ref.get("issue_key") or "").strip().upper()
        comment_id = str(ref.get("comment_id") or "").strip()
        if not issue_key or not comment_id:
            items.append(
                {
                    "issue_key": issue_key or None,
                    "comment_id": comment_id or None,
                    "ok": False,
                    "error": "Referência Jira incompleta.",
                }
            )
            continue
        items.append(delete_issue_comment(issue_key, comment_id, user=user))

    ok_count = sum(1 for i in items if i.get("ok"))
    return {
        "ok": ok_count == len(items) and len(items) > 0,
        "partial": 0 < ok_count < len(items),
        "skipped": False,
        "items": items,
        "error": None if ok_count == len(items) else (
            next((i.get("error") for i in items if not i.get("ok")), None)
        ),
    }


def attach_file_to_issue(
    issue_key: str,
    *,
    filename: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    credentials: JiraCredentials | None = None,
    user=None,
) -> dict[str, Any]:
    """
    POST /rest/api/2/issue/{key}/attachments (multipart field `file`).
    Requer header X-Atlassian-Token: no-check.
    """
    key = (issue_key or "").strip().upper()
    name = (filename or "attachment").strip() or "attachment"
    if not key:
        return {"ok": False, "error": "Issue key inválida.", "filename": name}
    if content is None:
        return {"ok": False, "error": "Arquivo vazio.", "filename": name}
    if not jira_base_configured():
        return {"ok": False, "error": "Integração Jira não configurada.", "filename": name}

    creds = credentials or (resolve_jira_credentials(user) if user is not None else None)
    if creds is None:
        return {"ok": False, "error": "Token Jira não cadastrado.", "filename": name}

    url = f"{jira_base_url()}/rest/api/2/issue/{key}/attachments"
    files = {
        "file": (name, content, (content_type or "application/octet-stream").strip() or "application/octet-stream"),
    }
    try:
        with _session_for_attachments(creds) as session:
            resp = _jira_request(session, "POST", url, files=files, timeout=jira_timeout_seconds())
    except requests.Timeout:
        return {"ok": False, "error": "Timeout ao anexar arquivo no Jira.", "filename": name}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"Falha de rede: {exc}", "filename": name}

    if _is_sso_interstitial(resp):
        return {
            "ok": False,
            "error": _SSO_GATEWAY_HINT,
            "filename": name,
            "issue_key": key,
            "status_code": resp.status_code,
        }

    if resp.status_code in (200, 201):
        return {"ok": True, "error": None, "filename": name, "issue_key": key}

    return {
        "ok": False,
        "error": _format_jira_error(resp),
        "filename": name,
        "issue_key": key,
        "status_code": resp.status_code,
    }


def attach_registro_anexos_to_jira(
    registro,
    issue_key: str,
    user,
    *,
    credentials: JiraCredentials | None = None,
    anexos=None,
) -> dict[str, Any]:
    """
    Envia anexos do registro para o issue Jira.
    Se `anexos` for passado, envia só esses; senão todos.
    Soft-fail por arquivo: não interrompe o lote.
    """
    from pathlib import Path

    key = (issue_key or "").strip().upper()
    if not key:
        return {"ok": True, "skipped": True, "uploaded": 0, "failed": 0, "items": []}

    creds = credentials or (resolve_jira_credentials(user) if user is not None else None)
    if creds is None:
        return {
            "ok": False,
            "skipped": True,
            "uploaded": 0,
            "failed": 0,
            "error": "Token Jira não cadastrado.",
            "items": [],
        }

    if anexos is None:
        anexo_list = list(registro.anexos.all())
    else:
        anexo_list = list(anexos)
    if not anexo_list:
        return {"ok": True, "skipped": True, "uploaded": 0, "failed": 0, "items": []}

    items: list[dict[str, Any]] = []
    uploaded = 0
    failed = 0
    for anexo in anexo_list:
        filename = (anexo.original_name or "").strip()
        if not filename and anexo.file:
            filename = Path(anexo.file.name).name
        if not filename:
            filename = f"anexo-{anexo.pk}"
        try:
            if not anexo.file:
                raise FileNotFoundError("Arquivo ausente no storage.")
            with anexo.file.open("rb") as fh:
                content = fh.read()
        except Exception as exc:
            failed += 1
            items.append(
                {
                    "ok": False,
                    "anexo_id": anexo.pk,
                    "filename": filename,
                    "error": str(exc),
                }
            )
            continue

        result = attach_file_to_issue(
            key,
            filename=filename,
            content=content,
            content_type=anexo.content_type or "application/octet-stream",
            credentials=creds,
        )
        entry = {
            "ok": bool(result.get("ok")),
            "anexo_id": anexo.pk,
            "filename": filename,
            "error": result.get("error"),
        }
        items.append(entry)
        if entry["ok"]:
            uploaded += 1
        else:
            failed += 1
            logger.info(
                "jira_attach failed issue=%s file=%s error=%s",
                key,
                filename,
                result.get("error"),
            )

    return {
        "ok": failed == 0,
        "skipped": False,
        "uploaded": uploaded,
        "failed": failed,
        "items": items,
        "issue_key": key,
        "error": None if failed == 0 else f"{failed} anexo(s) não enviados ao Jira.",
    }


def transition_issue(
    issue_key: str,
    *,
    transition_id: str | None = None,
    transition_name: str | None = None,
    credentials: JiraCredentials | None = None,
    user=None,
) -> dict[str, Any]:
    """
    POST /rest/api/2/issue/{key}/transitions.
    Aceita id direto ou nome (case-insensitive) resolvido via list_transitions.
    """
    key = (issue_key or "").strip().upper()
    if not key:
        return {"ok": False, "error": "Issue key inválida.", "skipped": False}

    creds = credentials or (resolve_jira_credentials(user) if user is not None else None)
    if creds is None:
        return {"ok": False, "error": "Token Jira não cadastrado.", "skipped": False}

    tid = (transition_id or "").strip()
    tname = (transition_name or "").strip()
    matched_name = tname

    if not tid:
        if not tname:
            return {"ok": False, "error": "Informe transition_id ou transition_name.", "skipped": False}
        listed = list_transitions(key, credentials=creds)
        if not listed.get("ok"):
            return {"ok": False, "error": listed.get("error") or "Falha ao listar transições.", "skipped": False}
        needle = tname.casefold()
        match = next(
            (t for t in listed["transitions"] if t["name"].casefold() == needle),
            None,
        )
        if match is None:
            return {
                "ok": False,
                "error": f'Transição "{tname}" não disponível no workflow atual.',
                "skipped": True,
                "available": [t["name"] for t in listed["transitions"]],
            }
        tid = match["id"]
        matched_name = match["name"]

    url = f"{jira_base_url()}/rest/api/2/issue/{key}/transitions"
    try:
        with _session(creds) as session:
            resp = _jira_request(
                session,
                "POST",
                url,
                json={"transition": {"id": tid}},
                timeout=jira_timeout_seconds(),
            )
    except requests.Timeout:
        return {"ok": False, "error": "Timeout ao aplicar transição Jira.", "skipped": False}
    except requests.RequestException as exc:
        return {"ok": False, "error": f"Falha de rede: {exc}", "skipped": False}

    if _is_sso_interstitial(resp):
        return {
            "ok": False,
            "error": _SSO_GATEWAY_HINT,
            "skipped": False,
            "issue_key": key,
        }

    if resp.status_code in (200, 204):
        return {
            "ok": True,
            "error": None,
            "skipped": False,
            "transition_id": tid,
            "transition_name": matched_name or None,
            "issue_key": key,
        }

    return {"ok": False, "error": _format_jira_error(resp), "skipped": False, "issue_key": key}


def _pick_transition(
    transitions: list[dict[str, str]],
    *,
    transition_names: list[str],
    target_status_names: list[str],
) -> dict[str, str] | None:
    by_name = {t["name"].casefold(): t for t in transitions}
    for candidate in transition_names:
        found = by_name.get(candidate.casefold())
        if found:
            return found
    by_to = {t["to"].casefold(): t for t in transitions if t.get("to")}
    for candidate in target_status_names:
        found = by_to.get(candidate.casefold())
        if found:
            return found
    return None


def _apply_portal_transition(
    issue_key: str,
    portal_status: str,
    user,
    *,
    transitions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    from apps.suporte_claro.services.jira_status_map import (
        target_status_names_for_portal_status,
        transition_names_for_portal_status,
    )

    names = transition_names_for_portal_status(portal_status)
    targets = target_status_names_for_portal_status(portal_status)
    if not names and not targets:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_transition_mapped",
            "issue_key": issue_key,
            "error": None,
        }

    if transitions is None:
        listed = list_transitions(issue_key, user=user)
        if not listed.get("ok"):
            return {
                "ok": False,
                "skipped": False,
                "reason": "list_failed",
                "issue_key": issue_key,
                "error": listed.get("error") or "Falha ao listar transições.",
            }
        transitions = listed["transitions"]

    if not transitions:
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_at_target_or_no_transitions",
            "issue_key": issue_key,
            "error": None,
        }

    match = _pick_transition(
        transitions,
        transition_names=names,
        target_status_names=targets,
    )
    if match is None:
        logger.info(
            "jira_sync skip issue=%s portal_status=%s available=%s tried=%s targets=%s",
            issue_key,
            portal_status,
            [(t["name"], t.get("to")) for t in transitions],
            names,
            targets,
        )
        return {
            "ok": False,
            "skipped": True,
            "reason": "transition_unavailable",
            "issue_key": issue_key,
            "error": (
                "Nenhuma transição Jira disponível para este status. "
                f"Disponíveis: {', '.join(t['name'] for t in transitions) or 'nenhuma'}."
            ),
            "tried": names,
            "available": [t["name"] for t in transitions],
        }

    applied = transition_issue(
        issue_key,
        transition_id=match["id"],
        transition_name=match["name"],
        user=user,
    )
    if applied.get("ok"):
        return {
            "ok": True,
            "skipped": False,
            "reason": None,
            "issue_key": issue_key,
            "transition": match["name"],
            "error": None,
        }

    return {
        "ok": False,
        "skipped": bool(applied.get("skipped")),
        "reason": "transition_failed",
        "issue_key": issue_key,
        "transition": match["name"],
        "error": applied.get("error") or "Falha ao aplicar transição no Jira.",
    }


def sync_portal_status_to_jira(registro, portal_status: str, user) -> dict[str, Any]:
    """
    Aplica transição Jira correspondente ao status do portal.
    Só no chamado interno. Externos nunca recebem transição automática.
    Se `concluido` não estiver disponível direto, tenta passar por `em_atendimento` antes.
    """
    from apps.suporte_claro.models import SuporteClaroRegistro
    from apps.suporte_claro.services.jira_link import (
        get_registro_jira_key,
        registro_has_jira_externo,
    )

    issue_key = get_registro_jira_key(registro)
    if not issue_key:
        if registro_has_jira_externo(registro):
            return {
                "ok": True,
                "skipped": True,
                "reason": "external_only",
                "issue_key": None,
                "error": None,
                "message": (
                    "Chamados externos não foram alterados. "
                    "Use Formalizar para postar um texto neles."
                ),
            }
        return {
            "ok": True,
            "skipped": True,
            "reason": "no_internal_jira",
            "issue_key": None,
            "error": None,
            "message": (
                "Nenhum chamado Jira interno vinculado; "
                "status salvo só no portal."
            ),
        }

    if not user_jira_configured(user):
        return {
            "ok": False,
            "skipped": True,
            "reason": "no_credentials",
            "issue_key": issue_key,
            "error": "Token Jira não cadastrado; status do portal foi salvo sem sync.",
        }

    result = _apply_portal_transition(issue_key, portal_status, user)

    # Workflow comum: Open → In Progress → Done (não dá Done direto).
    if (
        result.get("skipped")
        and result.get("reason") == "transition_unavailable"
        and (portal_status or "").strip().lower() == SuporteClaroRegistro.STATUS_CONCLUIDO
    ):
        mid = _apply_portal_transition(
            issue_key,
            SuporteClaroRegistro.STATUS_EM_ATENDIMENTO,
            user,
        )
        if mid.get("ok") and not mid.get("skipped"):
            result = _apply_portal_transition(issue_key, portal_status, user)
            if result.get("ok") and not result.get("skipped"):
                result["via"] = mid.get("transition")

    if registro_has_jira_externo(registro) and result.get("ok"):
        result = dict(result)
        result.setdefault(
            "message",
            "Chamados externos não foram alterados. Use Formalizar para postar um texto neles.",
        )

    return result


def formalizar_texto_em_externos(
    registro,
    user,
    *,
    chamado_ids: list[int],
    texto: str,
) -> dict[str, Any]:
    """
    Posta o mesmo texto como comentário nos chamados externos Jira selecionados.
    Não altera status. ServiceNow e ids inválidos falham por item (ou 400 se nenhum válido).
    """
    from apps.suporte_claro.models import SuporteClaroChamadoExterno, SuporteClaroRegistro
    from apps.suporte_claro.services.jira_link import list_jira_externos

    body = (texto or "").strip()
    if not body:
        return {
            "ok": False,
            "error": "Informe o texto para formalizar nos chamados externos.",
            "items": [],
        }

    ids = []
    for raw in chamado_ids or []:
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            return {
                "ok": False,
                "error": "chamado_ids inválido.",
                "items": [],
            }
    if not ids:
        return {
            "ok": False,
            "error": "Selecione ao menos um chamado externo Jira.",
            "items": [],
        }

    elegiveis = {c.id: c for c in list_jira_externos(registro)}
    items: list[dict[str, Any]] = []
    for cid in ids:
        chamado = elegiveis.get(cid)
        if chamado is None:
            row = (
                registro.chamados_externos.filter(pk=cid).first()
                if hasattr(registro, "chamados_externos")
                else None
            )
            if row and row.sistema == SuporteClaroRegistro.CHAMADO_SERVICE:
                items.append(
                    {
                        "id": cid,
                        "issue_key": (row.codigo or "").strip() or None,
                        "ok": False,
                        "error": "Formalização em ServiceNow não é suportada.",
                    }
                )
            elif row and row.natureza == SuporteClaroChamadoExterno.NATUREZA_INTERNO:
                items.append(
                    {
                        "id": cid,
                        "issue_key": (row.codigo or "").strip().upper() or None,
                        "ok": False,
                        "error": "Use Formalizar apenas em chamados externos.",
                    }
                )
            else:
                items.append(
                    {
                        "id": cid,
                        "issue_key": None,
                        "ok": False,
                        "error": "Chamado externo Jira não encontrado neste registro.",
                    }
                )
            continue

        key = (chamado.codigo or "").strip().upper()
        posted = add_issue_comment(key, body, user=user)
        items.append(
            {
                "id": cid,
                "issue_key": key,
                "ok": bool(posted.get("ok")),
                "error": posted.get("error"),
                "comment_id": posted.get("comment_id"),
            }
        )

    ok_count = sum(1 for i in items if i.get("ok"))
    return {
        "ok": ok_count == len(items) and len(items) > 0,
        "partial": 0 < ok_count < len(items),
        "items": items,
        "error": None if ok_count else (items[0].get("error") if items else "Nada formalizado."),
    }


def parse_formalizar_externos_payload(data) -> tuple[dict | None, str | None]:
    """
    Lê formalizar_externos do request.
    Retorno: (payload_dict | None se desligado, erro).
    """
    raw = data.get("formalizar_externos") if hasattr(data, "get") else None
    if raw is None:
        return None, None
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return None, "formalizar_externos inválido."
    if not isinstance(raw, dict):
        return None, "formalizar_externos inválido."
    enabled = bool(raw.get("enabled"))
    if not enabled:
        return None, None
    ids_raw = raw.get("chamado_ids") or raw.get("chamadoIds") or []
    if not isinstance(ids_raw, (list, tuple)):
        return None, "chamado_ids inválido."
    texto = raw.get("texto")
    return {
        "enabled": True,
        "chamado_ids": list(ids_raw),
        "texto": (str(texto) if texto is not None else None),
    }, None


def sync_registro_to_jira(
    registro,
    portal_status: str,
    user,
    *,
    comment: str | None = None,
) -> dict[str, Any]:
    """
    Sincroniza status do portal para o Jira interno vinculado.

    ``comment`` é aceito por compatibilidade de assinatura, mas **não** é mais
    postado como comentário no issue.
    """
    del comment  # legado — não postar retorno no Jira
    return sync_portal_status_to_jira(registro, portal_status, user)


def idle_jira_job(*, message: str = "Nenhuma formalização em execução.") -> dict:
    return {
        "running": False,
        "message": message,
        "step": None,
        "step_label": None,
        "detail": None,
        "result": None,
        "started_at": None,
        "ended_at": None,
    }


def jira_config_payload(profile_key: str, profile_label: str, user=None) -> dict:
    creds = resolve_jira_credentials(user) if user is not None else None
    agent_lan = creds.username if creds else None
    if agent_lan is None and user is not None:
        try:
            from apps.escala_flex.services.permissions import get_agent_for_user

            agent = get_agent_for_user(user)
            if agent:
                agent_lan = (agent.user_lan_id or "").strip() or None
        except Exception:
            agent_lan = None
    has_token = bool(creds and creds.api_token)
    base_ok = jira_base_configured()
    return {
        "profile_key": profile_key,
        "profile_label": profile_label,
        "jira_base_configured": base_ok,
        "jira_api_configured": base_ok and has_token,
        "jira_username": agent_lan,
        "jira_token_configured": has_token,
        "has_agent": agent_lan is not None,
        "automacoes_available": False,
        "job": idle_jira_job(),
    }


# Compat: management commands / testes antigos
def resolve_service_jira_credentials() -> JiraCredentials | None:
    """PAT de serviço (.env) para sync/leitura em lote — não depende do Agent do usuário."""
    if not jira_base_configured():
        return None
    username = str(getattr(settings, "JIRA_USERNAME", "") or "").strip() or "service"
    token = str(getattr(settings, "JIRA_API_TOKEN", "") or "").strip()
    if not token:
        return None
    return JiraCredentials(username=username, api_token=token)


def resolve_batch_jira_credentials(user=None) -> JiraCredentials | None:
    """PAT de serviço (.env) ou PAT do Agent do usuário — para sync/leitura em lote."""
    creds = resolve_service_jira_credentials()
    if creds is not None:
        return creds
    if user is not None:
        return resolve_jira_credentials(user)
    return None


def search_issues(
    jql: str,
    *,
    credentials: JiraCredentials | None = None,
    start_at: int = 0,
    max_results: int = 50,
    fields: list[str] | None = None,
) -> dict[str, Any]:
    """
    POST /rest/api/2/search — retorna payload bruto com issues, total, etc.
    """
    creds = credentials or resolve_service_jira_credentials()
    if creds is None:
        raise JiraRestError(
            "Jira não configurado. Defina JIRA_BASE_URL e JIRA_API_TOKEN no ambiente."
        )
    base = jira_base_url()
    url = f"{base}/rest/api/2/search"
    payload = {
        "jql": jql,
        "startAt": max(0, int(start_at)),
        "maxResults": min(max(1, int(max_results)), 100),
        "fields": fields
        or [
            "summary",
            "description",
            "status",
            "assignee",
            "reporter",
            "priority",
            "labels",
            "components",
            "issuetype",
            "created",
            "updated",
            "project",
        ],
    }
    session = _session(creds)
    resp = _jira_request(
        session,
        "POST",
        url,
        json=payload,
        timeout=jira_timeout_seconds(),
    )
    if resp.status_code >= 400:
        raise JiraRestError(_format_jira_error(resp), status_code=resp.status_code, body=resp.text)
    try:
        return resp.json()
    except Exception as exc:
        raise JiraRestError(f"Resposta inválida do Jira search: {exc}") from exc


def jira_browse_url(issue_key: str) -> str:
    key = (issue_key or "").strip()
    base = jira_base_url()
    if not key or not base:
        return ""
    return f"{base}/browse/{key}"


def get_issue(
    issue_key: str,
    *,
    credentials: JiraCredentials | None = None,
    expand: str = "transitions,names",
) -> dict[str, Any]:
    creds = credentials or resolve_service_jira_credentials()
    if creds is None:
        raise JiraRestError("Jira não configurado.")
    key = (issue_key or "").strip()
    if not key:
        raise JiraRestError("Informe a chave da issue.")
    base = jira_base_url()
    url = f"{base}/rest/api/2/issue/{key}"
    session = _session(creds)
    resp = _jira_request(
        session,
        "GET",
        url,
        params={"expand": expand} if expand else None,
        timeout=jira_timeout_seconds(),
    )
    if resp.status_code >= 400:
        raise JiraRestError(_format_jira_error(resp), status_code=resp.status_code, body=resp.text)
    try:
        return resp.json()
    except Exception as exc:
        raise JiraRestError(f"Resposta inválida do Jira get issue: {exc}") from exc


def assign_issue(
    issue_key: str,
    username: str,
    *,
    credentials: JiraCredentials | None = None,
) -> None:
    creds = credentials or resolve_service_jira_credentials()
    if creds is None:
        raise JiraRestError("Jira não configurado.")
    key = (issue_key or "").strip()
    lan = (username or "").strip()
    if not key or not lan:
        raise JiraRestError("Informe issue e responsável (LAN ID).")
    base = jira_base_url()
    url = f"{base}/rest/api/2/issue/{key}/assignee"
    session = _session(creds)
    resp = _jira_request(
        session,
        "PUT",
        url,
        json={"name": lan},
        timeout=jira_timeout_seconds(),
    )
    if resp.status_code >= 400:
        raise JiraRestError(_format_jira_error(resp), status_code=resp.status_code, body=resp.text)


def jira_rest_configured() -> bool:
    """True se a URL base está configurada (tokens são por usuário)."""
    return jira_base_configured()
