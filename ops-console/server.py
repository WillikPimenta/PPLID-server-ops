"""
Console de operacoes PPLID — servidor HTTP (stdlib apenas).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.cookiejar
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import server_ops
import server_db
import server_monitoring

OPS_ROOT = Path(__file__).resolve().parent
OPS_REPO_ROOT = OPS_ROOT.parent
PUBLIC_DIR = OPS_ROOT / "public"
DEFAULT_CONFIG = OPS_REPO_ROOT / "config" / "env.config.json"
ENV_ORDER = ("MAIN", "DEV", "HOM")
DEFAULT_BASE_DIR = Path("C:/PPLID")
SESSION_COOKIE_NAME = "ops_session"
PROTECTED_API_PREFIXES = (
    "/api/v1/overview",
    "/api/v1/overview-lite",
    "/api/v1/commits/",
    "/api/v1/logs/",
    "/api/v1/database/",
    "/api/v1/env/",
    "/api/v1/runs/",
    "/api/v1/actions/",
    "/api/v1/monitoring",
)
AUTH_PUBLIC_PATHS = {"/api/v1/auth/status"}


def get_ops_console_settings(config: dict[str, Any]) -> dict[str, Any]:
    return config.get("opsConsole") or {}


def get_session_secret() -> str:
    secret = os.environ.get("OPS_SESSION_SECRET", "").strip()
    if secret:
        return secret
    return "pplid-ops-dev-change-me"


def sign_session_payload(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(
        get_session_secret().encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def parse_session_cookie(cookie_value: str | None) -> dict[str, Any] | None:
    if not cookie_value or "." not in cookie_value:
        return None
    encoded, signature = cookie_value.rsplit(".", 1)
    expected = hmac.new(
        get_session_secret().encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    padding = "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded + padding)
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None
    exp = payload.get("exp")
    if exp is not None and time.time() > float(exp):
        return None
    return payload


def session_max_age_seconds(config: dict[str, Any]) -> int:
    hours = float(get_ops_console_settings(config).get("sessionHours", 12))
    return max(300, int(hours * 3600))


def build_session_payload(
    username: str,
    display_name: str,
    auth_source: str,
    locked: bool,
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "username": username,
        "displayName": display_name,
        "authSource": auth_source,
        "locked": locked,
        "exp": time.time() + session_max_age_seconds(config),
    }


def try_bootstrap_login(
    config: dict[str, Any],
    username: str,
    password: str,
) -> dict[str, Any] | None:
    bootstrap = get_ops_console_settings(config).get("bootstrapAuth") or {}
    if not bootstrap.get("enabled"):
        return None
    expected_user = (bootstrap.get("username") or "admin1").strip()
    expected_pass = (
        os.environ.get("OPS_BOOTSTRAP_PASSWORD", "").strip()
        or bootstrap.get("password")
        or ""
    )
    if secrets.compare_digest(username.strip(), expected_user) and secrets.compare_digest(
        password, expected_pass
    ):
        return {
            "username": expected_user,
            "displayName": "Administrador (bootstrap)",
            "authSource": "bootstrap",
        }
    return None


def validate_django_login(
    api_base: str,
    username: str,
    password: str,
) -> dict[str, Any] | None:
    base = api_base.rstrip("/")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    csrf_url = f"{base}/api/v1/auth/csrf/"
    try:
        csrf_request = urllib.request.Request(
            csrf_url,
            headers={"Accept": "application/json"},
        )
        with opener.open(csrf_request, timeout=10) as response:
            csrf_data = json.loads(response.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
        return None

    csrf_token = csrf_data.get("csrfToken")
    if not csrf_token:
        return None

    login_url = f"{base}/api/v1/auth/login/"
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    login_request = urllib.request.Request(
        login_url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-CSRFToken": csrf_token,
            "Referer": csrf_url,
        },
    )
    try:
        with opener.open(login_request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None

    display_name = data.get("display_name") or data.get("username") or username
    return {
        "username": data.get("username") or username,
        "displayName": display_name,
        "authSource": "django",
    }


def resolve_auth_backend(config: dict[str, Any]) -> str | None:
    ops = get_ops_console_settings(config)
    env_name = (ops.get("authEnv") or "MAIN").upper()
    env_cfg = config.get(env_name, {})
    return env_cfg.get("apiBaseUrl")


def authenticate_unlock(
    config: dict[str, Any],
    username: str,
    password: str,
) -> dict[str, Any] | None:
    bootstrap = try_bootstrap_login(config, username, password)
    if bootstrap:
        return bootstrap

    api_base = resolve_auth_backend(config)
    if not api_base:
        return None
    return validate_django_login(api_base, username, password)


def auth_status_from_session(
    session: dict[str, Any] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    ops = get_ops_console_settings(config)
    start_locked = bool(ops.get("startLocked", True))
    bootstrap = ops.get("bootstrapAuth") or {}

    if not session:
        return {
            "authenticated": False,
            "locked": start_locked,
            "user": None,
            "authSource": None,
            "idleLockMinutes": int(ops.get("idleLockMinutes", 15)),
            "bootstrapEnabled": bool(bootstrap.get("enabled")),
        }

    locked = bool(session.get("locked", True))
    return {
        "authenticated": True,
        "locked": locked,
        "user": {
            "username": session.get("username"),
            "displayName": session.get("displayName"),
        },
        "authSource": session.get("authSource"),
        "idleLockMinutes": int(ops.get("idleLockMinutes", 15)),
        "bootstrapEnabled": bool(bootstrap.get("enabled")),
    }


def is_api_protected(path: str) -> bool:
    if path in AUTH_PUBLIC_PATHS:
        return False
    return any(path.startswith(prefix) for prefix in PROTECTED_API_PREFIXES)


def load_local_env() -> None:
    env_path = OPS_ROOT / ".env.local"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_machine_config() -> dict[str, Any]:
    candidates = [
        DEFAULT_BASE_DIR / "machine.config.json",
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "PPLID" / "machine.config.json",
    ]
    for path in candidates:
        if path.is_file():
            with path.open(encoding="utf-8-sig") as handle:
                return json.load(handle)
    return {"baseDir": str(DEFAULT_BASE_DIR)}


def resolve_config_paths(config: dict[str, Any]) -> dict[str, Any]:
    machine = load_machine_config()
    base_dir = Path(machine.get("baseDir") or DEFAULT_BASE_DIR)
    repos_dir = base_dir / "repos"
    log_dir = base_dir / "logs"

    if machine.get("lanIp") and not config.get("lanIp"):
        config["lanIp"] = machine["lanIp"]

    config["logDir"] = str(log_dir)
    config["statusFile"] = str(log_dir / "deploy-status.json")

    for env_name in ENV_ORDER:
        env_cfg = config.get(env_name)
        if not env_cfg:
            continue
        if not env_cfg.get("repoDir"):
            repo_name = env_cfg.get("repoName") or f"PPLID_{env_name}"
            env_cfg["repoDir"] = str(repos_dir / repo_name)

    return config


def load_config(config_path: Path) -> dict[str, Any]:
    with config_path.open(encoding="utf-8") as handle:
        config = json.load(handle)
    return resolve_config_paths(config)


def load_status(status_path: Path) -> dict[str, Any]:
    if not status_path.exists():
        return {}
    try:
        with status_path.open(encoding="utf-8-sig") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return {}


def fix_mojibake(text: str | None) -> str:
    if not text or not isinstance(text, str):
        return text or ""
    if "Ã" not in text and "â" not in text and "�" not in text:
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
        if repaired and repaired != text:
            return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return text


def normalize_text_fields(data: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in data and isinstance(data[key], str):
            data[key] = fix_mojibake(data[key])


def normalize_overview_text(status: dict[str, Any], environments: dict[str, Any]) -> None:
    for env_data in environments.values():
        normalize_text_fields(env_data, ("gitCommitSubject", "gitCommitAuthor", "lastDeployMessage"))
        for commit_key in ("currentCommit", "deployCommit"):
            commit = env_data.get(commit_key)
            if isinstance(commit, dict):
                normalize_text_fields(commit, ("subject", "author"))
    events = status.get("events") or []
    for evt in events:
        if isinstance(evt, dict):
            normalize_text_fields(evt, ("message", "subject", "author"))
    status["events"] = events


def fetch_health(url: str, timeout: float = 5.0) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            data = json.loads(body) if body else {}
            return {
                "reachable": True,
                "httpStatus": response.status,
                "status": data.get("status"),
                "database": data.get("database"),
                "version": data.get("version"),
                "components": data.get("components") or {},
                "error": None,
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        return {
            "reachable": True,
            "httpStatus": exc.code,
            "status": "unhealthy",
            "database": "error",
            "version": None,
            "error": detail or str(exc),
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:  # noqa: BLE001 — resposta de overview para operadores
        return {
            "reachable": False,
            "httpStatus": None,
            "status": "offline",
            "database": None,
            "version": None,
            "error": str(exc),
            "checkedAt": datetime.now(timezone.utc).isoformat(),
        }


def load_deploy_state(base_dir: Path, env_name: str) -> dict[str, Any]:
    path = base_dir / "deploy" / env_name / "deploy-state.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def mirror_repo_dir(base_dir: Path, env_name: str) -> Path:
    return base_dir / "deploy" / env_name / "mirror"


def load_current_release_meta(base_dir: Path, env_name: str) -> dict[str, Any]:
    path = base_dir / "deploy" / env_name / "current" / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_release_sha(
    base_dir: Path,
    env_name: str,
    deploy_state: dict[str, Any],
    stored: dict[str, Any],
) -> tuple[str | None, str | None]:
    meta = load_current_release_meta(base_dir, env_name)
    sha = str(meta.get("sha") or "").strip() or None
    sha_full = str(meta.get("shaFull") or "").strip() or None
    if not sha:
        sha = str(deploy_state.get("activeSha") or "").strip() or None
    if not sha:
        sha = str(stored.get("deployedSha") or "").strip() or None
    if not sha_full:
        sha_full = str(stored.get("deployedShaFull") or "").strip() or None
    return sha, sha_full


def git_dir_for_env(base_dir: Path, env_name: str, repo_dir: Path) -> Path:
    mirror = mirror_repo_dir(base_dir, env_name)
    if mirror.is_dir():
        return mirror
    return repo_dir


def resolve_deploy_pending(
    stored: dict[str, Any],
    deploy_state: dict[str, Any],
    *,
    pipeline_status: str,
    deployed_sha: str | None,
    release_sha: str | None,
) -> bool:
    if pipeline_status in ("building", "validating", "promoting", "watching"):
        return True

    target_sha = str(deploy_state.get("targetSha") or "").strip() or None
    if target_sha and deployed_sha and not shas_match(target_sha, deployed_sha):
        return True

    if pipeline_status == "idle":
        if release_sha and deployed_sha:
            return not shas_match(release_sha, deployed_sha)
        if release_sha and not deployed_sha:
            return True
        return bool(stored.get("updatePending"))

    if pipeline_status == "failed":
        return bool(stored.get("updatePending"))

    return bool(stored.get("updatePending"))


def resolve_display_phase(
    stored: dict[str, Any],
    runtime: dict[str, Any],
    *,
    deploy_pending: bool,
    pipeline_status: str | None = None,
) -> str:
    stored_phase = stored.get("phase") or "idle"
    pipe = (pipeline_status or stored.get("pipelineStatus") or "").lower()

    if pipe in ("building", "validating", "promoting", "watching"):
        return "deploying"
    if pipe == "failed":
        return "failed"
    if pipe == "rolled_back":
        return "rolled_back"

    last_deploy = stored.get("lastDeploy") or {}
    last_result = last_deploy.get("result") or stored.get("lastDeployResult")

    if stored_phase == "deploying":
        return "deploying"
    if deploy_pending:
        return "deploy_pending"
    if not runtime.get("reachable"):
        return "offline"
    if runtime.get("database") != "ok" or runtime.get("status") not in ("healthy", "degraded"):
        return "unhealthy"
    if stored_phase == "failed" or last_result == "failed":
        return "failed"
    if last_result == "warning" or runtime.get("status") == "degraded":
        return "degraded"
    return "online"


def tail_log_file(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return content[-lines:] if lines > 0 else content


def github_commit_url(repo_url: str | None, sha_full: str | None) -> str | None:
    if not repo_url or not sha_full:
        return None
    base = repo_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    return f"{base}/commit/{sha_full}"


def shas_match(a: str | None, b: str | None) -> bool:
    if not a or not b or a == "unknown" or b == "unknown":
        return False
    a = a.strip().lower()
    b = b.strip().lower()
    return a == b or a.startswith(b) or b.startswith(a)


def load_deployed_sha(log_dir: Path, env_name: str, stored: dict[str, Any]) -> str | None:
    if stored.get("deployedSha"):
        return str(stored["deployedSha"])

    if stored.get("lastDeployResult") == "success" and stored.get("gitSha"):
        return str(stored["gitSha"])

    deployed_file = log_dir / f"PPLID_{env_name}.deployed.json"
    if not deployed_file.is_file():
        return None
    try:
        with deployed_file.open(encoding="utf-8") as handle:
            data = json.load(handle)
        sha = str(data.get("sha") or "").strip()
        return sha or None
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return None


def build_availability(
    runtime: dict[str, Any],
    env_cfg: dict[str, Any] | None = None,
    db_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if env_cfg:
        extended = server_ops.build_availability_extended(runtime, env_cfg, db_metrics)
        return extended.get("components") or {}
    reachable = bool(runtime.get("reachable"))
    return {
        "backend": reachable and runtime.get("status") in ("healthy", "degraded"),
        "frontend": reachable,
        "database": reachable and runtime.get("database") == "ok",
    }


def build_commit_enrichment(
    stored: dict[str, Any],
    runtime: dict[str, Any],
    repo_url: str | None,
    *,
    release_sha: str | None,
    release_sha_full: str | None,
    release_details: dict[str, Any],
    deployed_sha: str | None,
    deploy_pending: bool,
    deployed_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    disk_sha = release_sha or stored.get("deployedSha") or stored.get("gitSha")
    deployed = deployed_sha or stored.get("deployedSha")
    runtime_sha = runtime.get("version") if runtime.get("reachable") else None
    sha_full = release_sha_full or stored.get("deployedShaFull") or stored.get("gitShaFull")

    deploy_info = deployed_details or {}
    deploy_sha_full = deploy_info.get("shaFull") or stored.get("deployedShaFull") or sha_full

    disk_commit = {
        "sha": disk_sha or "unknown",
        "shaFull": release_details.get("shaFull") or release_sha_full or stored.get("deployedShaFull"),
        "subject": release_details.get("subject") or stored.get("gitCommitSubject") or "",
        "author": release_details.get("author") or stored.get("gitCommitAuthor") or "",
        "committedAt": release_details.get("committedAt") or stored.get("gitCommitAt"),
    }
    deploy_commit = {
        "sha": deployed or "unknown",
        "shaFull": deploy_sha_full,
        "subject": deploy_info.get("subject") or stored.get("gitCommitSubject") or "",
        "author": deploy_info.get("author") or stored.get("gitCommitAuthor") or "",
        "committedAt": stored.get("deployedAt") or deploy_info.get("committedAt") or stored.get("gitCommitAt"),
    }

    sha_in_sync = True
    if disk_sha and deployed:
        sha_in_sync = shas_match(disk_sha, deployed)

    return {
        "currentCommit": disk_commit,
        "deployCommit": deploy_commit,
        "shaInSync": sha_in_sync,
        "githubCommitUrl": github_commit_url(
            repo_url,
            deploy_info.get("shaFull") or release_details.get("shaFull") or sha_full or stored.get("gitShaFull"),
        ),
        "updatePending": bool(stored.get("updatePending")),
        "originSha": stored.get("originSha"),
        "originShaShort": (stored.get("originSha") or "")[:7] or None,
        "deployedSha": deployed,
        "repoSha": disk_sha,
        "deployPending": deploy_pending,
        "runtimeVersion": runtime_sha,
    }


def run_git(repo_dir: Path, *args: str, timeout: float = 5.0) -> str | None:
    if not repo_dir.is_dir():
        return None
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def git_commit_details(repo_dir: Path, revision: str = "HEAD") -> dict[str, Any]:
    empty = {
        "sha": "unknown",
        "shaFull": None,
        "subject": "",
        "author": "",
        "committedAt": None,
    }
    formatted = run_git(repo_dir, "log", "-1", revision, "--format=%H|%h|%s|%an|%ai")
    if not formatted:
        return empty
    parts = formatted.split("|", 4)
    if len(parts) < 5:
        return empty
    return {
        "shaFull": parts[0],
        "sha": parts[1],
        "subject": fix_mojibake(parts[2]),
        "author": fix_mojibake(parts[3]),
        "committedAt": parts[4],
    }


def extract_deploy_log_for_sha(path: Path, sha: str) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines:
        return []
    sha_key = (sha or "").strip().lower()
    if not sha_key or sha_key == "unknown":
        return lines[-80:]

    for i in range(len(lines) - 1, -1, -1):
        if "DEPLOY INICIADO" not in lines[i]:
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            if "DEPLOY CONCLUIDO" in lines[j] or "DEPLOY FALHOU" in lines[j]:
                end = j + 1
                break
        segment = lines[i:end]
        if sha_key in "\n".join(segment).lower():
            return segment
    return []


def extract_sync_log_excerpt(path: Path, sha: str, max_lines: int = 40) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    sha_key = (sha or "").strip().lower()
    if sha_key and sha_key != "unknown":
        matched = [line for line in lines if sha_key in line.lower()]
        if matched:
            return matched[-max_lines:]
    return lines[-max_lines:]


_COMMIT_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_COMMIT_CACHE_TTL_SEC = 300
_GIT_DETAILS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def cached_git_commit_details(
    env_name: str,
    git_dir: Path,
    repo_dir: Path,
    revision: str,
) -> dict[str, Any]:
    rev_key = (revision or "HEAD").strip().lower()
    cache_key = f"{env_name}:{rev_key}"
    now = time.time()
    cached = _GIT_DETAILS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _COMMIT_CACHE_TTL_SEC:
        return cached[1]

    details = (
        git_commit_details(git_dir, revision)
        if git_dir.is_dir()
        else git_commit_details(repo_dir, revision)
        if repo_dir.is_dir()
        else {}
    )
    _GIT_DETAILS_CACHE[cache_key] = (now, details)
    return details


def _fetch_runtime_by_env(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    health_jobs: dict[str, str] = {}
    for env_name in ENV_ORDER:
        env_cfg = config.get(env_name, {})
        backend_port = env_cfg.get("backendPort")
        if backend_port is None:
            continue
        health_jobs[env_name] = f"http://127.0.0.1:{backend_port}/api/v1/health/"

    runtime_by_env: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(fetch_health, url): env_name
            for env_name, url in health_jobs.items()
        }
        for future in as_completed(futures):
            env_name = futures[future]
            runtime_by_env[env_name] = future.result()
    return runtime_by_env


def build_commit_payload(
    config: dict[str, Any],
    env_name: str,
    sha_query: str | None = None,
) -> dict[str, Any]:
    env_cfg = config.get(env_name, {})
    repo_dir = Path(env_cfg.get("repoDir", ""))
    repo_url = config.get("repoUrl")
    log_dir = Path(config.get("logDir", ""))
    repo_name = env_cfg.get("repoName", f"PPLID_{env_name}")
    base_dir = log_dir.parent
    git_dir = git_dir_for_env(base_dir, env_name, repo_dir)

    revision = sha_query or "HEAD"
    cache_key = f"{env_name}:{revision.strip().lower()}"
    now = time.time()
    cached = _COMMIT_CACHE.get(cache_key)
    if cached and (now - cached[0]) < _COMMIT_CACHE_TTL_SEC:
        return cached[1]

    details = git_commit_details(git_dir, revision)
    if details.get("sha") == "unknown" and git_dir != repo_dir:
        details = git_commit_details(repo_dir, revision)
    sha = details.get("sha") or "unknown"
    sha_full = details.get("shaFull")

    deploy_log = log_dir / f"{repo_name}.deploy.log"
    sync_log = log_dir / f"{repo_name}.log"

    git_log = run_git(git_dir, "log", "-1", revision, "--format=medium") or ""
    git_show = run_git(git_dir, "show", "--stat", "--oneline", revision) or ""
    if not git_log and git_dir != repo_dir:
        git_log = run_git(repo_dir, "log", "-1", revision, "--format=medium") or ""
        git_show = run_git(repo_dir, "show", "--stat", "--oneline", revision) or ""

    payload = {
        "environment": env_name,
        "sha": sha,
        "shaFull": sha_full,
        "subject": details.get("subject") or "",
        "author": details.get("author") or "",
        "committedAt": details.get("committedAt"),
        "githubUrl": github_commit_url(repo_url, sha_full),
        "gitLog": git_log,
        "gitShowStat": git_show,
        "deployLogExcerpt": extract_deploy_log_for_sha(deploy_log, sha),
        "syncLogExcerpt": extract_sync_log_excerpt(sync_log, sha),
    }
    _COMMIT_CACHE[cache_key] = (now, payload)
    return payload


def build_overview(config: dict[str, Any], *, lite: bool = False) -> dict[str, Any]:
    status_path = Path(config.get("statusFile") or Path(config["logDir"]) / "deploy-status.json")
    status = load_status(status_path)
    lan_ip = config.get("lanIp") or "127.0.0.1"

    environments: dict[str, Any] = {}
    runtime_by_env = _fetch_runtime_by_env(config)
    health_jobs: dict[str, str] = {}
    for env_name in ENV_ORDER:
        env_cfg = config.get(env_name, {})
        backend_port = env_cfg.get("backendPort")
        if backend_port is not None:
            health_jobs[env_name] = f"http://127.0.0.1:{backend_port}/api/v1/health/"

    stored_envs = status.get("environments") or {}
    repo_url = config.get("repoUrl")
    base_dir = Path(config.get("logDir", str(DEFAULT_BASE_DIR / "logs"))).parent

    for env_name in ENV_ORDER:
        env_cfg = config.get(env_name, {})
        if not env_cfg:
            continue

        stored = stored_envs.get(env_name, {})
        runtime = runtime_by_env.get(env_name, fetch_health(health_jobs.get(env_name, "")))
        stored_phase = stored.get("phase") or "idle"

        repo_dir = Path(env_cfg.get("repoDir", ""))
        log_dir = Path(config.get("logDir", ""))
        deploy_state = load_deploy_state(base_dir, env_name)
        pipeline_status = str(deploy_state.get("status") or "idle")

        if lite:
            display_phase = resolve_display_phase(
                stored,
                runtime,
                deploy_pending=resolve_deploy_pending(
                    stored,
                    deploy_state,
                    pipeline_status=pipeline_status,
                    deployed_sha=deploy_state.get("activeSha") or load_deployed_sha(log_dir, env_name, stored),
                    release_sha=deploy_state.get("activeSha"),
                ),
                pipeline_status=pipeline_status,
            )
            frontend_port = env_cfg.get("frontendPort")
            links = stored.get("links") or {
                "frontend": f"http://{lan_ip}:{frontend_port}",
                "api": f"http://{lan_ip}:{env_cfg['backendPort']}/api/v1/",
                "health": f"http://{lan_ip}:{env_cfg['backendPort']}/api/v1/health/",
            }
            avail_extended = server_ops.build_availability_extended(runtime, env_cfg, None)
            environments[env_name] = {
                "phase": stored_phase,
                "displayPhase": display_phase,
                "pipelineStatus": pipeline_status,
                "deployState": {
                    "status": deploy_state.get("status"),
                    "runId": deploy_state.get("runId"),
                    "startedAt": deploy_state.get("startedAt"),
                    "finishedAt": deploy_state.get("finishedAt"),
                    "lastError": deploy_state.get("lastError"),
                    "activeSha": deploy_state.get("activeSha"),
                    "targetSha": deploy_state.get("targetSha"),
                    "blockedSha": deploy_state.get("blockedSha"),
                    "blockedReason": deploy_state.get("blockedReason"),
                    "blockedAt": deploy_state.get("blockedAt"),
                },
                "branch": stored.get("branch") or env_cfg.get("branch"),
                "links": links,
                "runtime": {
                    "reachable": runtime.get("reachable"),
                    "status": runtime.get("status"),
                    "database": runtime.get("database"),
                    "version": runtime.get("version"),
                },
                "availabilityAggregate": avail_extended.get("aggregate"),
                "services": server_ops.build_services_from_availability(
                    env_name, env_cfg, avail_extended, None
                ),
                "lastDeployStartedAt": stored.get("lastDeployStartedAt"),
                "lastDeployFinishedAt": stored.get("lastDeployFinishedAt"),
                "lastDeployMessage": stored.get("lastDeployMessage"),
                "gitSha": stored.get("gitSha"),
                "deployedSha": stored.get("deployedSha") or deploy_state.get("activeSha"),
                "deploySummary": server_ops.build_deploy_summary(base_dir, env_name, deploy_state, stored),
            }
            continue

        release_sha, release_sha_full = resolve_release_sha(
            base_dir,
            env_name,
            deploy_state,
            stored,
        )
        deployed_sha = deploy_state.get("activeSha") or load_deployed_sha(log_dir, env_name, stored)
        git_dir = git_dir_for_env(base_dir, env_name, repo_dir)
        release_revision = release_sha_full or release_sha or "HEAD"
        release_details = cached_git_commit_details(env_name, git_dir, repo_dir, release_revision)

        deployed_revision = (
            stored.get("deployedShaFull")
            or deploy_state.get("activeSha")
            or deployed_sha
            or "HEAD"
        )
        deployed_details = cached_git_commit_details(env_name, git_dir, repo_dir, str(deployed_revision))

        deploy_pending = resolve_deploy_pending(
            stored,
            deploy_state,
            pipeline_status=pipeline_status,
            deployed_sha=deployed_sha,
            release_sha=release_sha,
        )

        display_phase = resolve_display_phase(
            stored,
            runtime,
            deploy_pending=deploy_pending,
            pipeline_status=pipeline_status,
        )

        frontend_port = env_cfg.get("frontendPort")
        links = stored.get("links") or {
            "frontend": f"http://{lan_ip}:{frontend_port}",
            "api": f"http://{lan_ip}:{env_cfg['backendPort']}/api/v1/",
            "health": f"http://{lan_ip}:{env_cfg['backendPort']}/api/v1/health/",
        }

        commit_meta = build_commit_enrichment(
            stored,
            runtime,
            repo_url,
            release_sha=release_sha,
            release_sha_full=release_sha_full,
            release_details=release_details,
            deployed_sha=deployed_sha,
            deploy_pending=deploy_pending,
            deployed_details=deployed_details,
        )

        deploy_extra: dict[str, Any] = {}
        run_id = str(deploy_state.get("runId") or "").strip() or None
        if run_id and pipeline_status in ("building", "validating", "promoting"):
            target_sha_progress = str(deploy_state.get("targetSha") or stored.get("gitSha") or "").strip()
            deploy_extra["deployProgress"] = server_ops.load_deploy_progress(
                base_dir,
                env_name,
                run_id,
                pipeline_status=pipeline_status,
                target_sha=target_sha_progress,
            )
        if pipeline_status in ("building", "validating", "promoting"):
            target_sha = str(deploy_state.get("targetSha") or stored.get("gitSha") or "").strip()
            if target_sha:
                target_revision = stored.get("gitShaFull") or target_sha
                target_details = (
                    cached_git_commit_details(env_name, git_dir, repo_dir, str(target_revision))
                    if git_dir.is_dir() or repo_dir.is_dir()
                    else {}
                )
                deploy_extra["targetCommit"] = {
                    "sha": target_details.get("sha") or target_sha,
                    "shaFull": target_details.get("shaFull") or stored.get("gitShaFull"),
                    "subject": target_details.get("subject") or stored.get("gitCommitSubject") or "",
                    "author": target_details.get("author") or stored.get("gitCommitAuthor") or "",
                    "committedAt": target_details.get("committedAt") or stored.get("gitCommitAt"),
                }
                target_url_sha = deploy_extra["targetCommit"].get("shaFull") or target_sha
                target_url = github_commit_url(repo_url, target_url_sha)
                if target_url:
                    deploy_extra["githubCommitUrl"] = target_url

        db_metrics = None
        try:
            db_metrics = server_ops.fetch_database_metrics(
                config,
                env_name,
                backend_reachable=bool(runtime.get("reachable")),
            )
        except Exception:  # noqa: BLE001
            db_metrics = None

        avail_extended = server_ops.build_availability_extended(runtime, env_cfg, db_metrics)
        availability = avail_extended.get("components") or {}
        services = server_ops.build_services_from_availability(
            env_name, env_cfg, avail_extended, db_metrics
        )

        environments[env_name] = {
            **stored,
            "phase": stored_phase,
            "displayPhase": display_phase,
            "pipelineStatus": pipeline_status,
            "deployState": deploy_state,
            "lastGoodSha": deploy_state.get("lastGoodSha"),
            "previousSha": deploy_state.get("previousSha"),
            "activeSha": deployed_sha,
            "branch": stored.get("branch") or env_cfg.get("branch"),
            "repoName": env_cfg.get("repoName"),
            "links": links,
            "runtime": runtime,
            "availability": availability,
            "availabilityAggregate": avail_extended.get("aggregate"),
            "services": services,
            "database": db_metrics,
            **commit_meta,
            **deploy_extra,
        }

    normalize_overview_text(status, environments)

    return {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "lite": lite,
        "server": status.get("server") or {"lanIp": lan_ip, "hostname": socket.gethostname()},
        "opsConsolePort": config.get("opsConsolePort", 5190),
        "repoUrl": repo_url,
        "logDir": config.get("logDir", ""),
        "statusFile": str(status_path),
        "environments": environments,
        "events": status.get("events") or [],
    }


class OpsConsoleHandler(BaseHTTPRequestHandler):
    config_path: Path = DEFAULT_CONFIG
    config: dict[str, Any] = {}

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    def _get_cookie(self, name: str) -> str | None:
        cookie_header = self.headers.get("Cookie", "")
        prefix = f"{name}="
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(prefix):
                return part[len(prefix) :]
        return None

    def _set_session_cookie(self, payload: dict[str, Any]) -> None:
        token = sign_session_payload(payload)
        max_age = session_max_age_seconds(self.config)
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}",
        )

    def _clear_session_cookie(self) -> None:
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0",
        )

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    def _current_session(self) -> dict[str, Any] | None:
        return parse_session_cookie(self._get_cookie(SESSION_COOKIE_NAME))

    def _require_unlocked_session(self) -> dict[str, Any] | None:
        session = self._current_session()
        if not session or session.get("locked"):
            return None
        return session

    def _send_json(
        self,
        payload: Any,
        status: int = 200,
        *,
        set_session: dict[str, Any] | None = None,
        clear_session: bool = False,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if set_session is not None:
            self._set_session_cookie(set_session)
        if clear_session:
            self._clear_session_cookie()
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, data: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _handle_auth_status(self) -> None:
        session = self._current_session()
        self._send_json(auth_status_from_session(session, self.config))

    def _handle_auth_unlock(self) -> None:
        body = self._read_json_body()
        session = self._current_session()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""

        if not username and session:
            username = (session.get("username") or "").strip()

        if not username or not password:
            self._send_json({"error": "Usuario e senha obrigatorios"}, status=400)
            return

        auth = authenticate_unlock(self.config, username, password)
        if not auth:
            self._send_json({"error": "Usuario ou senha invalidos"}, status=401)
            return

        payload = build_session_payload(
            auth["username"],
            auth["displayName"],
            auth["authSource"],
            locked=False,
            config=self.config,
        )
        status_payload = auth_status_from_session(payload, self.config)
        self._send_json(status_payload, set_session=payload)

    def _handle_auth_lock(self) -> None:
        session = self._current_session()
        if not session:
            self._send_json({"error": "Nao autenticado"}, status=401)
            return

        payload = build_session_payload(
            session.get("username", ""),
            session.get("displayName", ""),
            session.get("authSource", "django"),
            locked=True,
            config=self.config,
        )
        self._send_json(auth_status_from_session(payload, self.config), set_session=payload)

    def _session_username(self) -> str:
        session = self._require_unlocked_session()
        return (session or {}).get("username") or "unknown"

    def _handle_action_rollback(self, env_name: str, body: dict[str, Any]) -> None:
        result = server_ops.action_rollback(
            self.config,
            env_name,
            target_sha=str(body.get("sha") or ""),
            reason=str(body.get("reason") or "console"),
        )
        server_ops.audit_log(
            self.config,
            self._session_username(),
            "rollback",
            f"env={env_name} sha={body.get('sha', '')} ok={result.get('ok')}",
        )
        status = 200 if result.get("ok") else 500
        self._send_json(result, status=status)

    def _handle_action_redeploy(self, env_name: str, body: dict[str, Any]) -> None:
        result = server_ops.action_redeploy(
            self.config,
            env_name,
            target_sha=str(body.get("sha") or ""),
            async_mode=bool(body.get("async")),
        )
        server_ops.audit_log(
            self.config,
            self._session_username(),
            "redeploy",
            f"env={env_name} sha={body.get('sha', '')} ok={result.get('ok')}",
        )
        status = 200 if result.get("ok") else 500
        self._send_json(result, status=status)

    def _handle_action_clear_block(self, env_name: str, body: dict[str, Any]) -> None:
        result = server_ops.action_clear_block_and_redeploy(self.config, env_name)
        server_ops.audit_log(
            self.config,
            self._session_username(),
            "clear-block",
            f"env={env_name} ok={result.get('ok')} cleared={result.get('clearedBlockedSha', '')}",
        )
        status = 200 if result.get("ok") else 500
        self._send_json(result, status=status)

    def _handle_action_restart(self, env_name: str, body: dict[str, Any]) -> None:
        service = str(body.get("service") or "all")
        result = server_ops.action_restart_service(self.config, env_name, service)
        server_ops.audit_log(
            self.config,
            self._session_username(),
            "restart",
            f"env={env_name} service={service} ok={result.get('ok')}",
        )
        status = 200 if result.get("ok") else 500
        self._send_json(result, status=status)

    def _handle_action_promote(self, body: dict[str, Any]) -> None:
        source = str(body.get("source") or "DEV").upper()
        target = str(body.get("target") or "HOM").upper()
        if source not in ENV_ORDER or target not in ENV_ORDER:
            self._send_json({"error": "Ambiente invalido"}, status=400)
            return
        result = server_ops.action_promote_cross_env(self.config, source, target)
        server_ops.audit_log(
            self.config,
            self._session_username(),
            "promote",
            f"source={source} target={target} ok={result.get('ok')}",
        )
        status = 200 if result.get("ok") else 500
        self._send_json(result, status=status)

    def _handle_database(self, method: str, path: str, body: dict[str, Any] | None = None) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        payload, status = server_db.handle_db_request(
            method,
            path,
            self.config,
            query,
            body or {},
        )
        if method in ("POST", "PATCH", "DELETE") and status < 400:
            action = "db_write"
            if method == "POST":
                action = "db_insert"
            elif method == "PATCH":
                action = "db_update"
            elif method == "DELETE":
                action = "db_delete"
            server_ops.audit_log(
                self.config,
                self._session_username(),
                action,
                f"path={path}",
            )
        self._send_json(payload, status=status)

    def _handle_env_apply(self, env_name: str) -> None:
        result = server_ops.apply_env_vars(self.config, env_name)
        server_ops.audit_log(
            self.config,
            self._session_username(),
            "env_apply",
            f"env={env_name} ok={result.get('ok')}",
        )
        status = 200 if result.get("ok") else 500
        self._send_json(result, status=status)

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._require_unlocked_session():
            self._send_json({"error": "Bloqueado ou nao autenticado"}, status=401)
            return
        if path.startswith("/api/v1/database/"):
            self._handle_database("DELETE", path, self._read_json_body())
            return
        self._send_json({"error": "Nao encontrado"}, status=404)

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._require_unlocked_session():
            self._send_json({"error": "Bloqueado ou nao autenticado"}, status=401)
            return
        if path.startswith("/api/v1/database/"):
            self._handle_database("PATCH", path, self._read_json_body())
            return
        self._send_json({"error": "Nao encontrado"}, status=404)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_PUT(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._require_unlocked_session():
            self._send_json({"error": "Bloqueado ou nao autenticado"}, status=401)
            return

        if path.startswith("/api/v1/env/"):
            env_name = path.removeprefix("/api/v1/env/").strip("/").upper()
            if env_name not in ENV_ORDER:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return
            body = self._read_json_body()
            if env_name == "MAIN":
                backend = body.get("backend") or {}
                if backend.get("DEBUG") == "False" and not body.get("confirmMain"):
                    self._send_json(
                        {"error": "Confirme alteracao em MAIN com confirmMain: true"},
                        status=400,
                    )
                    return
            try:
                result = server_ops.update_env_vars(self.config, env_name, body)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            server_ops.audit_log(
                self.config,
                self._session_username(),
                "env_update",
                f"env={env_name}",
            )
            self._send_json(result)
            return

        if path == "/api/v1/monitoring/config":
            body = self._read_json_body()
            base_dir = server_ops.get_base_dir(self.config)
            cfg_path = base_dir / "ops" / "data" / "monitoring-config.json"
            try:
                cfg_path.parent.mkdir(parents=True, exist_ok=True)
                cfg_path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            except OSError as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            server_ops.audit_log(
                self.config,
                self._session_username(),
                "monitoring_config_update",
                "",
            )
            self._send_json({"ok": True})
            return

        self._send_json({"error": "Nao encontrado"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/v1/auth/unlock":
            self._handle_auth_unlock()
            return
        if path == "/api/v1/auth/lock":
            self._handle_auth_lock()
            return

        if path.startswith("/api/v1/actions/"):
            if not self._require_unlocked_session():
                self._send_json({"error": "Bloqueado ou nao autenticado"}, status=401)
                return
            body = self._read_json_body()
            if path == "/api/v1/actions/promote":
                self._handle_action_promote(body)
                return
            for prefix, handler in (
                ("/api/v1/actions/rollback/", self._handle_action_rollback),
                ("/api/v1/actions/redeploy/", self._handle_action_redeploy),
                ("/api/v1/actions/clear-block/", self._handle_action_clear_block),
                ("/api/v1/actions/restart/", self._handle_action_restart),
            ):
                if path.startswith(prefix):
                    env_name = path.removeprefix(prefix).strip("/").upper()
                    if env_name not in ENV_ORDER:
                        self._send_json({"error": "Ambiente invalido"}, status=404)
                        return
                    handler(env_name, body)
                    return

        if path.startswith("/api/v1/env/") and path.endswith("/apply"):
            if not self._require_unlocked_session():
                self._send_json({"error": "Bloqueado ou nao autenticado"}, status=401)
                return
            env_name = path.removeprefix("/api/v1/env/").removesuffix("/apply").strip("/").upper()
            if env_name not in ENV_ORDER:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return
            self._handle_env_apply(env_name)
            return

        if path.startswith("/api/v1/database/"):
            if not self._require_unlocked_session():
                self._send_json({"error": "Bloqueado ou nao autenticado"}, status=401)
                return
            self._handle_database("POST", path, self._read_json_body())
            return

        self._send_json({"error": "Nao encontrado"}, status=404)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/v1/auth/status":
            self._handle_auth_status()
            return

        if is_api_protected(path) and not self._require_unlocked_session():
            self._send_json({"error": "Bloqueado ou nao autenticado"}, status=401)
            return

        if path == "/api/v1/overview":
            overview = build_overview(self.config)
            self._send_json(overview)
            return

        if path == "/api/v1/overview-lite":
            overview = build_overview(self.config, lite=True)
            self._send_json(overview)
            return

        if path.startswith("/api/v1/commits/"):
            env_name = path.removeprefix("/api/v1/commits/").strip("/").upper()
            if env_name not in ENV_ORDER:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return

            query = parse_qs(parsed.query)
            sha_query = query.get("sha", [None])[0]
            payload = build_commit_payload(self.config, env_name, sha_query)
            self._send_json(payload)
            return

        if path.startswith("/api/v1/logs/"):
            env_name = path.removeprefix("/api/v1/logs/").strip("/").upper()
            if env_name not in ENV_ORDER:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return

            query = parse_qs(parsed.query)
            line_count = int(query.get("lines", ["80"])[0])
            line_count = max(1, min(line_count, 500))

            env_cfg = self.config.get(env_name, {})
            repo_name = env_cfg.get("repoName", f"PPLID_{env_name}")
            log_dir = Path(self.config.get("logDir", ""))
            log_path = log_dir / f"{repo_name}.deploy.log"

            self._send_json(
                {
                    "environment": env_name,
                    "path": str(log_path),
                    "lines": tail_log_file(log_path, line_count),
                }
            )
            return

        if path.startswith("/api/v1/database/"):
            self._handle_database("GET", path)
            return

        if path.startswith("/api/v1/env/"):
            env_part = path.removeprefix("/api/v1/env/").strip("/")
            if env_part == "diff" or env_part.endswith("/diff"):
                self._send_json(server_ops.build_env_diff(self.config))
                return
            if "/reveal" in env_part:
                reveal_parts = env_part.split("/")
                env_name = reveal_parts[0].upper()
                if env_name not in ENV_ORDER:
                    self._send_json({"error": "Ambiente invalido"}, status=404)
                    return
                query = parse_qs(parsed.query)
                scope = (query.get("scope") or [""])[0]
                key = (query.get("key") or [""])[0]
                try:
                    result = server_ops.reveal_env_var(self.config, env_name, scope, key)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=400)
                    return
                self._send_json(result)
                return
            env_name = env_part.upper()
            if env_name not in ENV_ORDER:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return
            self._send_json(server_ops.build_env_payload(self.config, env_name))
            return

        if path == "/api/v1/monitoring/config":
            try:
                self._send_json(server_monitoring.build_monitoring_config(self.config))
            except Exception as exc:  # noqa: BLE001
                self._send_json({"error": str(exc)}, status=500)
            return

        if path.startswith("/api/v1/monitoring/"):
            self._handle_monitoring_get(path, parsed)
            return

        if path.startswith("/api/v1/runs/"):
            remainder = path.removeprefix("/api/v1/runs/").strip("/")
            parts = [p for p in remainder.split("/") if p]
            if not parts:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return
            env_name = parts[0].upper()
            if env_name not in ENV_ORDER:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return
            query = parse_qs(parsed.query)
            base_dir = server_ops.get_base_dir(self.config)

            if len(parts) >= 2 and parts[1].lower() == "download":
                run_id = parts[2] if len(parts) >= 3 else query.get("runId", [""])[0]
                if not run_id:
                    self._send_json({"error": "runId obrigatorio"}, status=400)
                    return
                payload = server_ops.build_run_logs_zip(base_dir, env_name, run_id)
                if payload is None:
                    self._send_json({"error": "Run nao encontrado"}, status=404)
                    return
                filename = f"pplid-{env_name.lower()}-{run_id}-logs.zip"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return

            run_id = query.get("runId", [""])[0]
            sha = query.get("sha", [""])[0]
            if not run_id and sha:
                run_id = server_ops.find_run_id_by_sha(base_dir, env_name, sha) or ""
            if not run_id:
                self._send_json({"error": "runId ou sha obrigatorio"}, status=400)
                return
            log_offsets = server_ops.parse_log_offsets(query.get("logOffset", [""])[0])
            self._send_json(server_ops.load_run_logs(base_dir, env_name, run_id, log_offsets=log_offsets or None))
            return

        static_path = self._resolve_static(path)
        if static_path and static_path.is_file():
            content_type, _ = mimetypes.guess_type(str(static_path))
            self._send_bytes(
                static_path.read_bytes(),
                content_type or "application/octet-stream",
            )
            return

        index = PUBLIC_DIR / "index.html"
        if path.startswith("/api/"):
            self._send_json({"error": "Nao encontrado"}, status=404)
            return

        if index.is_file():
            self._send_bytes(index.read_bytes(), "text/html; charset=utf-8")
            return

        self.send_error(404)

    def _handle_monitoring_get(self, path: str, parsed) -> None:
        remainder = path.removeprefix("/api/v1/monitoring/").strip("/")
        parts = [p for p in remainder.split("/") if p]
        query = parse_qs(parsed.query)
        try:
            if not parts:
                self._send_json({"error": "Rota invalida"}, status=400)
                return
            env_name = parts[0].upper()
            if env_name not in ENV_ORDER:
                self._send_json({"error": "Ambiente invalido"}, status=404)
                return
            sub = parts[1].lower() if len(parts) > 1 else "summary"
            if sub == "summary":
                hours = int(query.get("hours", ["24"])[0])
                self._send_json(
                    server_monitoring.build_monitoring_summary(self.config, env_name, window_hours=hours)
                )
                return
            if sub == "series":
                metric = (query.get("metric") or [""])[0]
                if not metric:
                    self._send_json({"error": "metric obrigatorio"}, status=400)
                    return
                hours = int(query.get("hours", ["168"])[0])
                self._send_json(
                    server_monitoring.build_monitoring_series(
                        self.config, env_name, metric, hours=hours
                    )
                )
                return
            if sub == "events":
                limit = int(query.get("limit", ["100"])[0])
                self._send_json(
                    server_monitoring.build_monitoring_events(
                        self.config, env_name, limit=limit
                    )
                )
                return
            if sub == "syncs":
                self._send_json(server_monitoring.build_monitoring_syncs(self.config, env_name))
                return
            if sub == "api-routes":
                window = (query.get("window") or ["24h"])[0]
                self._send_json(
                    server_monitoring.build_monitoring_api_routes(self.config, env_name, window=window)
                )
                return
            if sub == "deploy":
                self._send_json(server_monitoring.build_monitoring_deploy_stats(self.config, env_name))
                return
            self._send_json({"error": "Sub-rota invalida"}, status=404)
        except Exception as exc:  # noqa: BLE001
            self._send_json({"error": str(exc)}, status=500)

    def _resolve_static(self, url_path: str) -> Path | None:
        if url_path in ("/", ""):
            candidate = PUBLIC_DIR / "index.html"
            return candidate if candidate.is_file() else None

        relative = url_path.lstrip("/")
        candidate = (PUBLIC_DIR / relative).resolve()
        if not str(candidate).startswith(str(PUBLIC_DIR.resolve())):
            return None
        return candidate


def run_server(host: str, port: int, config_path: Path) -> None:
    if not config_path.is_file():
        raise FileNotFoundError(f"Config nao encontrada: {config_path}")

    config = load_config(config_path)
    OpsConsoleHandler.config = config
    OpsConsoleHandler.config_path = config_path

    server = ThreadingHTTPServer((host, port), OpsConsoleHandler)
    lan_ip = config.get("lanIp", host)
    print(f"Ops Console em http://{host}:{port}")
    print(f"Acesso LAN: http://{lan_ip}:{port}")
    server_monitoring.start_monitoring_collector(config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nEncerrando...")
        server.shutdown()


def main() -> None:
    load_local_env()
    config_path = Path(os.environ.get("OPS_CONFIG", DEFAULT_CONFIG))
    host = os.environ.get("OPS_HOST", "0.0.0.0")
    port = int(os.environ.get("OPS_PORT", "5190"))

    if len(sys.argv) > 1:
        config_path = Path(sys.argv[1])
    if len(sys.argv) > 2:
        port = int(sys.argv[2])

    if config_path.is_file():
        cfg = load_config(config_path)
        port = int(cfg.get("opsConsolePort", port))

    run_server(host, port, config_path)


if __name__ == "__main__":
    main()
