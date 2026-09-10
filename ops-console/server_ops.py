"""
Helpers operacionais do ops-console (actions, database, env vars).
"""
from __future__ import annotations

import json
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

ENV_ORDER = ("MAIN", "DEV", "HOM")
BUSY_STATUSES = frozenset({"building", "validating", "promoting", "watching"})
ENV_DISABLED_ERROR = "Ambiente desativado. Ative-o antes de executar esta acao."


def is_env_enabled(config: dict[str, Any], env_name: str) -> bool:
    """Missing enabled field defaults to True (backward compatible)."""
    env_cfg = config.get(env_name)
    if not isinstance(env_cfg, dict):
        return False
    enabled = env_cfg.get("enabled")
    if enabled is None:
        return True
    return bool(enabled)


def get_enabled_envs(config: dict[str, Any]) -> tuple[str, ...]:
    return tuple(name for name in ENV_ORDER if is_env_enabled(config, name))


def require_env_enabled(config: dict[str, Any], env_name: str) -> dict[str, Any] | None:
    """Return an error payload if env is disabled; otherwise None."""
    if is_env_enabled(config, env_name):
        return None
    return {"ok": False, "error": ENV_DISABLED_ERROR, "enabled": False, "environment": env_name}
SECRET_KEY_PATTERNS = re.compile(
    r"(SECRET|PASSWORD|TOKEN|KEY|CREDENTIAL|PRIVATE)",
    re.IGNORECASE,
)
MASK_VALUE = "••••••••"
DERIVED_FRONTEND_ENV_KEYS = (
    "VITE_DEV_SERVER_PORT",
    "VITE_BACKEND_PORT",
    "VITE_BACKEND_PROXY_TARGET",
)

_DB_METRICS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DB_METRICS_CACHE_TTL_SEC = 45
_MIGRATIONS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_MIGRATIONS_CACHE_TTL_SEC = 300
_MIGRATIONS_LOCKS: dict[str, threading.Lock] = {}
_MIGRATIONS_LOCKS_GUARD = threading.Lock()
_MIGRATIONS_INFLIGHT: dict[str, bool] = {}
_DEPLOY_PROGRESS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_DEPLOY_PROGRESS_CACHE_TTL_SEC = 2
_ORPHAN_BOT_LOCK = threading.Lock()


def fix_mojibake(text: str | None) -> str:
    if not text or not isinstance(text, str):
        return text or ""
    if "Ã" not in text and "â" not in text and "\ufffd" not in text:
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
        if repaired and repaired != text:
            return repaired
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return text


ENV_BRANCH = {"MAIN": "main", "DEV": "dev", "HOM": "hom"}


def _resolve_origin_branch_sha(base_dir: Path, env_name: str) -> tuple[str, str]:
    branch = ENV_BRANCH.get(env_name, "dev")
    mirror = base_dir / "deploy" / env_name / "mirror"
    if not mirror.is_dir():
        return "", ""
    ref = f"origin/{branch}"
    try:
        subprocess.run(
            ["git", "-C", str(mirror), "fetch", "origin"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        proc_full = subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", ref],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc_full.returncode != 0:
            return "", ""
        full = proc_full.stdout.strip()
        proc_short = subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", "--short", full],
            capture_output=True,
            text=True,
            timeout=15,
        )
        short = proc_short.stdout.strip() if proc_short.returncode == 0 else full[:7]
        return short, full
    except (OSError, subprocess.TimeoutExpired):
        return "", ""


def clear_deploy_blocked(base_dir: Path, env_name: str) -> dict[str, Any]:
    path = base_dir / "deploy" / env_name / "deploy-state.json"
    if not path.is_file():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return {}
        cleared = str(state.get("blockedSha") or "").strip()
        for key in ("blockedSha", "blockedAt", "blockedReason", "blockedRunId"):
            state[key] = None
        path.write_text(json.dumps(state, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")
        return {"clearedBlockedSha": cleared}
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_sha_in_mirror(
    base_dir: Path,
    env_name: str,
    sha: str,
    *,
    fetch: bool = True,
) -> tuple[str, str]:
    sha = sha.strip()
    if not sha:
        return "", ""
    mirror = base_dir / "deploy" / env_name / "mirror"
    if not mirror.is_dir():
        return sha, sha if len(sha) >= 40 else sha
    try:
        if fetch:
            subprocess.run(
                ["git", "-C", str(mirror), "fetch", "origin"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        proc = subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", sha],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if proc.returncode != 0:
            return sha, sha if len(sha) >= 40 else sha
        full = proc.stdout.strip()
        short_proc = subprocess.run(
            ["git", "-C", str(mirror), "rev-parse", "--short", full],
            capture_output=True,
            text=True,
            timeout=15,
        )
        short = short_proc.stdout.strip() if short_proc.returncode == 0 else full[:7]
        return short, full
    except (OSError, subprocess.TimeoutExpired):
        return sha, sha if len(sha) >= 40 else sha


def get_base_dir(config: dict[str, Any]) -> Path:
    log_dir = config.get("logDir") or "C:/PPLID/logs"
    return Path(log_dir).parent


def resolve_ops_lib_dir(config: dict[str, Any] | None = None) -> Path:
    """Resolve checkout lib/ops_store.py for both server (C:/PPLID/ops/lib) and local dev."""
    candidates: list[Path] = []
    if config:
        base_dir = get_base_dir(config)
        candidates.append(base_dir / "ops" / "lib")
        ops_console = config.get("opsConsoleDir")
        if ops_console:
            candidates.append(Path(str(ops_console)).resolve().parent / "lib")
    candidates.append(Path(__file__).resolve().parent.parent / "lib")
    seen: set[str] = set()
    for path in candidates:
        resolved = path.resolve()
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        if (resolved / "ops_store.py").is_file():
            return resolved
    return candidates[0].resolve()


def audit_log(config: dict[str, Any], username: str, action: str, detail: str = "") -> None:
    """Persist console audit events to SQLite (audit_events). File fallback for legacy only."""
    base_dir = get_base_dir(config)
    db_path = get_ops_store_db_path(base_dir) or (base_dir / "ops" / "data" / "ops-store.db")
    try:
        ops_lib = resolve_ops_lib_dir(config)
        if str(ops_lib) not in sys.path:
            sys.path.insert(0, str(ops_lib))
        import ops_store  # type: ignore

        ops_store.append_audit_event(
            username,
            action,
            detail,
            db_path=db_path if Path(db_path).parent.is_dir() else None,
        )
        return
    except Exception:
        pass
    # Legacy file fallback if SQLite unavailable
    log_dir = Path(config.get("logDir") or "C:/PPLID/logs")
    path = log_dir / "ops-console-audit.log"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] user={username} action={action} {detail}\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except OSError:
        pass


def load_deploy_state(base_dir: Path, env_name: str) -> dict[str, Any]:
    path = base_dir / "deploy" / env_name / "deploy-state.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def is_deploy_busy(base_dir: Path, env_name: str) -> bool:
    state = load_deploy_state(base_dir, env_name)
    return str(state.get("status") or "idle") in BUSY_STATUSES


def run_powershell(
    script: Path,
    args: list[str],
    *,
    timeout: int = 300,
    extra_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not script.is_file():
        return {"ok": False, "error": f"Script nao encontrado: {script}", "exitCode": -1}

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]
    env = None
    if extra_env:
        env = {**dict(__import__("os").environ), **extra_env}
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return {
            "ok": result.returncode == 0,
            "exitCode": result.returncode,
            "stdout": (result.stdout or "")[-4000:],
            "stderr": (result.stderr or "")[-2000:],
            "error": None if result.returncode == 0 else (result.stderr or result.stdout or "Falha")[-500:],
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "Timeout na execucao", "exitCode": -1}
    except OSError as exc:
        return {"ok": False, "error": str(exc), "exitCode": -1}


def run_powershell_async(
    script: Path,
    args: list[str],
    *,
    timeout: int = 2400,
    extra_env: dict[str, str] | None = None,
    on_complete: Any = None,
) -> dict[str, Any]:
    run_id = secrets.token_hex(8)

    def _worker() -> None:
        result = run_powershell(script, args, timeout=timeout, extra_env=extra_env)
        if on_complete:
            on_complete(result)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return {"accepted": True, "runId": run_id}


def _run_orphan_bot_tool(
    config: dict[str, Any], mode: str, *, log_name: str = "orphan-bots.log"
) -> dict[str, Any]:
    base_dir = get_base_dir(config)
    script = base_dir / "ops" / "lib" / "orphan_bot_cleanup.ps1"
    if not script.is_file():
        return {"ok": False, "mode": mode, "detected": 0, "stopped": 0, "failed": 1, "items": [], "error": "utilitário ausente"}
    state_path = base_dir / "ops" / "data" / "orphan-bots.json"
    args = ["-Mode", mode, "-BaseDir", str(base_dir), "-StatePath", str(state_path)]
    if log_name:
        args.extend(["-LogPath", str(base_dir / "logs" / log_name)])
    result = run_powershell(script, args, timeout=60)
    try:
        # The complete result may exceed run_powershell's diagnostic stdout tail.
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload["ok"] = bool(payload.get("ok")) and bool(result.get("ok"))
            if not result.get("ok") and result.get("error"):
                payload.setdefault("error", result.get("error"))
            return payload
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    try:
        lines = [line for line in str(result.get("stdout") or "").splitlines() if line.strip()]
        payload = json.loads(lines[-1]) if lines else {}
        if isinstance(payload, dict):
            payload["ok"] = bool(payload.get("ok")) and bool(result.get("ok"))
            return payload
    except (json.JSONDecodeError, TypeError):
        pass
    return {"ok": False, "mode": mode, "detected": 0, "stopped": 0, "failed": 1, "items": [], "error": result.get("error") or result.get("stderr") or "varredura sem JSON"}


def _public_orphan_bot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    public_items: list[dict[str, Any]] = []
    source_items = [] if payload.get("mode") == "scan" and not payload.get("ok") else payload.get("items") or []
    for raw in source_items:
        if not isinstance(raw, dict) or raw.get("classification") != "orphan":
            continue
        public_items.append(
            {
                key: raw.get(key)
                for key in (
                    "pid", "parentPid", "environment", "release", "activeRelease",
                    "mode", "creationDate", "classification", "reason", "result",
                )
            }
        )
    return {
        "ok": bool(payload.get("ok")),
        "mode": payload.get("mode"),
        "scannedAt": payload.get("scannedAt"),
        "detected": len(public_items),
        "stopped": int(payload.get("stopped") or 0),
        "failed": int(payload.get("failed") or 0),
        "items": public_items,
        **({"error": str(payload.get("error"))} if payload.get("error") else {}),
    }


def run_orphan_bot_scan(config: dict[str, Any]) -> dict[str, Any]:
    """Return a fresh, sanitized list of currently running orphan bots."""
    with _ORPHAN_BOT_LOCK:
        return _public_orphan_bot_payload(_run_orphan_bot_tool(config, "scan", log_name=""))


def run_orphan_bot_cleanup(config: dict[str, Any], *, log_name: str = "orphan-bots.log") -> dict[str, Any]:
    """Best-effort host-wide orphan cleanup shared by deploy/restart actions."""
    with _ORPHAN_BOT_LOCK:
        return _run_orphan_bot_tool(config, "cleanup", log_name=log_name)


def action_cleanup_orphan_bots(config: dict[str, Any]) -> dict[str, Any]:
    """Stop every revalidated orphan and return the post-cleanup live state."""
    with _ORPHAN_BOT_LOCK:
        cleanup = _run_orphan_bot_tool(config, "cleanup")
        live = _run_orphan_bot_tool(config, "scan", log_name="")
    public_live = _public_orphan_bot_payload(live)
    failed = int(cleanup.get("failed") or 0)
    return {
        **public_live,
        "ok": bool(cleanup.get("ok")) and bool(live.get("ok")) and failed == 0,
        "action": "cleanup-orphan-bots",
        "detectedBefore": int(cleanup.get("detected") or 0),
        "stopped": int(cleanup.get("stopped") or 0),
        "failed": failed,
        "attempts": _public_orphan_bot_payload(cleanup).get("items", []),
        **({"error": cleanup.get("error")} if cleanup.get("error") else {}),
    }


def action_rollback(
    config: dict[str, Any],
    env_name: str,
    *,
    target_sha: str = "",
    reason: str = "console",
) -> dict[str, Any]:
    disabled = require_env_enabled(config, env_name)
    if disabled:
        return disabled

    base_dir = get_base_dir(config)
    if is_deploy_busy(base_dir, env_name):
        return {"ok": False, "error": "Deploy em andamento neste ambiente."}

    run_id = f"console-{secrets.token_hex(6)}"
    script = base_dir / "ops" / "deploy" / "rollback_release.ps1"
    args = ["-Environment", env_name, "-RunId", run_id, "-Reason", reason]
    if target_sha:
        args.extend(["-TargetSha", target_sha.strip()])

    result = run_powershell(script, args, timeout=300)
    state = load_deploy_state(base_dir, env_name)
    return {
        **result,
        "activeSha": state.get("activeSha"),
        "lastGoodSha": state.get("lastGoodSha"),
        "runId": run_id,
    }


def action_cancel_deploy(
    config: dict[str, Any],
    env_name: str,
    *,
    requested_by: str = "console",
) -> dict[str, Any]:
    """Cancela deploy em curso: mata o pipeline, libera o ambiente para novo redeploy."""
    disabled = require_env_enabled(config, env_name)
    if disabled:
        return disabled

    base_dir = get_base_dir(config)
    state_before = load_deploy_state(base_dir, env_name)
    previous_run_id = str(state_before.get("runId") or "")
    was_busy = is_deploy_busy(base_dir, env_name)

    script = base_dir / "ops" / "deploy" / "cancel_deploy.ps1"
    args = ["-Environment", env_name, "-RequestedBy", requested_by or "console"]
    result = run_powershell(script, args, timeout=120)

    # Parse JSON da saida do script (ultima linha JSON)
    parsed: dict[str, Any] = {}
    stdout = str(result.get("stdout") or "")
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                break
            except json.JSONDecodeError:
                continue

    state = load_deploy_state(base_dir, env_name)
    still_busy = is_deploy_busy(base_dir, env_name)
    ok = bool(result.get("ok")) and not still_busy
    if parsed.get("ok") is False:
        ok = False

    message = (
        parsed.get("message")
        or result.get("error")
        or ("Deploy cancelado." if ok else "Falha ao cancelar deploy.")
    )
    return {
        "ok": ok,
        "action": "cancel",
        "environment": env_name,
        "previousRunId": parsed.get("previousRunId") or previous_run_id,
        "wasBusy": was_busy,
        "alreadyIdle": bool(parsed.get("alreadyIdle")) or not was_busy,
        "processKilled": bool(parsed.get("processKilled")),
        "message": message,
        "activeSha": state.get("activeSha"),
        "lastGoodSha": state.get("lastGoodSha"),
        "pipelineStatus": state.get("status"),
        "exitCode": result.get("exitCode"),
        "stderr": result.get("stderr"),
        "error": None if ok else (result.get("error") or message),
    }


def action_redeploy(
    config: dict[str, Any],
    env_name: str,
    *,
    target_sha: str = "",
    target_sha_full: str = "",
    trigger: str = "console-redeploy",
    async_mode: bool = False,
    promote_source: str = "",
) -> dict[str, Any]:
    disabled = require_env_enabled(config, env_name)
    if disabled:
        return disabled

    base_dir = get_base_dir(config)
    if is_deploy_busy(base_dir, env_name):
        return {"ok": False, "error": "Deploy em andamento neste ambiente."}

    env_cfg = config.get(env_name, {})
    worktree = read_env_git_worktree_status(
        base_dir,
        env_name,
        repo_dir=Path(env_cfg.get("repoDir", "")),
        use_cache=False,
    )
    if worktree.get("dirty"):
        return {
            "ok": False,
            "error": worktree.get("reason")
            or "Working tree com alteracoes locais. Resolva manualmente antes de atualizar.",
            "gitWorktree": worktree,
        }

    sha = target_sha.strip()
    sha_full = target_sha_full.strip() or sha
    if not sha:
        sha, sha_full = _resolve_origin_branch_sha(base_dir, env_name)
        if not sha:
            mirror = base_dir / "deploy" / env_name / "mirror"
            if mirror.is_dir():
                try:
                    proc = subprocess.run(
                        ["git", "-C", str(mirror), "rev-parse", "--short", "HEAD"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if proc.returncode == 0:
                        sha = proc.stdout.strip()
                        proc_full = subprocess.run(
                            ["git", "-C", str(mirror), "rev-parse", "HEAD"],
                            capture_output=True,
                            text=True,
                            timeout=15,
                        )
                        if proc_full.returncode == 0:
                            sha_full = proc_full.stdout.strip()
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if not sha:
            state = load_deploy_state(base_dir, env_name)
            sha = str(state.get("activeSha") or "").strip()
            sha_full = sha

    if sha:
        resolved_short, resolved_full = _resolve_sha_in_mirror(base_dir, env_name, sha)
        if len(resolved_full) >= 40:
            sha, sha_full = resolved_short, resolved_full
        elif len(sha_full) < 40:
            sha_full = resolved_full if len(resolved_full) >= 40 else sha_full

    if not sha:
        return {"ok": False, "error": "SHA alvo nao encontrado."}
    if len(sha_full) < 40:
        return {
            "ok": False,
            "error": (
                f"Commit {sha} nao encontrado no mirror de {env_name}. "
                "Verifique se o commit existe no remoto antes de promover."
            ),
        }

    run_id = f"console-{secrets.token_hex(6)}"
    script = base_dir / "ops" / "deploy" / "run_pipeline_locked.ps1"
    args = [
        "-Environment",
        env_name,
        "-TargetSha",
        sha,
        "-TargetShaFull",
        sha_full,
        "-RunId",
        run_id,
        "-Trigger",
        trigger,
    ]
    extra_env: dict[str, str] = {}
    if promote_source:
        extra_env["PPLID_PROMOTE_SOURCE"] = promote_source.strip().upper()

    if async_mode:
        def _on_complete(result: dict[str, Any]) -> None:
            if result.get("exitCode") == 2:
                # Mutex held — surface as busy without leaving a failed deploy state
                pass

        run_powershell_async(
            script,
            args,
            extra_env=extra_env or None,
            on_complete=_on_complete,
        )
        return {
            "ok": True,
            "accepted": True,
            "environment": env_name,
            "targetSha": sha,
            "runId": run_id,
        }

    result = run_powershell(script, args, timeout=2400, extra_env=extra_env or None)
    if result.get("exitCode") == 2:
        return {
            "ok": False,
            "error": "Deploy em andamento neste ambiente (mutex).",
            "exitCode": 2,
            "targetSha": sha,
            "runId": run_id,
        }
    state = load_deploy_state(base_dir, env_name)
    return {**result, "targetSha": sha, "activeSha": state.get("activeSha"), "runId": run_id}


def action_clear_block_and_redeploy(
    config: dict[str, Any],
    env_name: str,
) -> dict[str, Any]:
    disabled = require_env_enabled(config, env_name)
    if disabled:
        return disabled

    base_dir = get_base_dir(config)
    if is_deploy_busy(base_dir, env_name):
        return {"ok": False, "error": "Deploy em andamento neste ambiente."}

    cleared = clear_deploy_blocked(base_dir, env_name)
    result = action_redeploy(
        config,
        env_name,
        trigger="console-clear-block",
        async_mode=True,
    )
    if cleared.get("clearedBlockedSha"):
        result["clearedBlockedSha"] = cleared["clearedBlockedSha"]
    return result


def action_promote_cross_env(
    config: dict[str, Any],
    source_env: str,
    target_env: str,
) -> dict[str, Any]:
    for name in (source_env, target_env):
        disabled = require_env_enabled(config, name)
        if disabled:
            disabled["error"] = f"{name}: {ENV_DISABLED_ERROR}"
            return disabled

    base_dir = get_base_dir(config)
    if is_deploy_busy(base_dir, target_env):
        return {"ok": False, "error": f"Deploy em andamento em {target_env}."}

    source_state = load_deploy_state(base_dir, source_env)
    sha = str(source_state.get("activeSha") or "").strip()
    if not sha:
        return {"ok": False, "error": f"Nenhum SHA ativo em {source_env}."}

    short, full = _resolve_sha_in_mirror(base_dir, source_env, sha)
    if len(full) < 40:
        short, full = _resolve_sha_in_mirror(base_dir, target_env, sha)

    if len(full) < 40:
        return {
            "ok": False,
            "error": (
                f"Commit {sha} (ativo em {source_env}) nao encontrado no mirror de {target_env}. "
                "Faca fetch/merge no remoto ou aguarde sync antes de promover."
            ),
        }

    return action_redeploy(
        config,
        target_env,
        target_sha=short,
        target_sha_full=full,
        trigger="console-promote",
        async_mode=True,
        promote_source=source_env,
    )


def set_env_enabled(
    config: dict[str, Any],
    env_name: str,
    enabled: bool,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Persist ENV.enabled in env.config.json and update in-memory config."""
    if env_name not in ENV_ORDER:
        return {"ok": False, "error": "Ambiente invalido."}

    env_cfg = config.get(env_name)
    if not isinstance(env_cfg, dict):
        return {"ok": False, "error": "Ambiente invalido."}

    path = Path(config_path) if config_path else get_env_config_path(config)
    if not path.is_file():
        return {"ok": False, "error": f"env.config.json nao encontrado: {path}"}

    try:
        disk = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "error": f"Falha ao ler env.config.json: {exc}"}

    if env_name not in disk or not isinstance(disk[env_name], dict):
        return {"ok": False, "error": f"Bloco {env_name} ausente em env.config.json."}

    disk[env_name]["enabled"] = bool(enabled)
    try:
        path.write_text(json.dumps(disk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": f"Falha ao gravar env.config.json: {exc}"}

    env_cfg["enabled"] = bool(enabled)
    return {
        "ok": True,
        "environment": env_name,
        "enabled": bool(enabled),
        "path": str(path),
    }


def _can_disable_auth_env(config: dict[str, Any], env_name: str) -> dict[str, Any] | None:
    """Block disabling authEnv when bootstrap auth is off (avoids lockout)."""
    ops_console = config.get("opsConsole") or {}
    auth_env = str(ops_console.get("authEnv") or "MAIN").upper()
    if env_name != auth_env:
        return None
    bootstrap = ops_console.get("bootstrapAuth") or {}
    if bool(bootstrap.get("enabled")):
        return None
    return {
        "ok": False,
        "error": (
            f"Nao e possivel desativar {env_name}: e o authEnv do ops-console "
            "e bootstrapAuth esta desligado. Ative bootstrapAuth ou troque authEnv."
        ),
    }


def action_disable_environment(
    config: dict[str, Any],
    env_name: str,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    if env_name not in ENV_ORDER:
        return {"ok": False, "error": "Ambiente invalido."}

    auth_block = _can_disable_auth_env(config, env_name)
    if auth_block:
        return auth_block

    base_dir = get_base_dir(config)
    if is_deploy_busy(base_dir, env_name):
        return {"ok": False, "error": "Deploy em andamento neste ambiente."}

    if not is_env_enabled(config, env_name):
        # Still ensure processes are stopped.
        env_cfg = config.get(env_name, {})
        repo_dir = Path(env_cfg.get("repoDir", ""))
        stop_script = repo_dir / "scripts" / "deploy" / "stop_env.ps1"
        if stop_script.is_file():
            run_powershell(stop_script, ["-Environment", env_name], timeout=120)
        return {
            "ok": True,
            "action": "disable",
            "environment": env_name,
            "enabled": False,
            "alreadyDisabled": True,
            "message": f"Ambiente {env_name} ja estava desativado.",
        }

    env_cfg = config.get(env_name, {})
    repo_dir = Path(env_cfg.get("repoDir", ""))
    stop_script = repo_dir / "scripts" / "deploy" / "stop_env.ps1"
    stop = run_powershell(stop_script, ["-Environment", env_name], timeout=120)
    if not stop.get("ok"):
        return {
            "ok": False,
            "error": stop.get("error") or f"Falha ao parar {env_name}.",
            "exitCode": stop.get("exitCode"),
            "stderr": stop.get("stderr"),
        }

    persisted = set_env_enabled(config, env_name, False, config_path=config_path)
    if not persisted.get("ok"):
        return persisted

    return {
        "ok": True,
        "action": "disable",
        "environment": env_name,
        "enabled": False,
        "message": f"Ambiente {env_name} desativado. Watch/deploy/probes pausados.",
    }


def action_enable_environment(
    config: dict[str, Any],
    env_name: str,
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    if env_name not in ENV_ORDER:
        return {"ok": False, "error": "Ambiente invalido."}

    base_dir = get_base_dir(config)
    if is_deploy_busy(base_dir, env_name):
        return {"ok": False, "error": "Deploy em andamento neste ambiente."}

    persisted = set_env_enabled(config, env_name, True, config_path=config_path)
    if not persisted.get("ok"):
        return persisted

    env_cfg = config.get(env_name, {})
    repo_dir = Path(env_cfg.get("repoDir", ""))
    start_script = repo_dir / "scripts" / "deploy" / "start_env.ps1"
    start = run_powershell(start_script, ["-Environment", env_name], timeout=300)
    if not start.get("ok"):
        return {
            "ok": False,
            "error": start.get("error") or f"Flag ativada, mas falha ao iniciar {env_name}.",
            "enabled": True,
            "exitCode": start.get("exitCode"),
            "stderr": start.get("stderr"),
        }

    return {
        "ok": True,
        "action": "enable",
        "environment": env_name,
        "enabled": True,
        "message": f"Ambiente {env_name} ativado e iniciado.",
    }


def action_restart_service(
    config: dict[str, Any],
    env_name: str,
    service: str,
) -> dict[str, Any]:
    disabled = require_env_enabled(config, env_name)
    if disabled:
        return disabled

    base_dir = get_base_dir(config)
    if is_deploy_busy(base_dir, env_name):
        return {"ok": False, "error": "Deploy em andamento neste ambiente."}

    env_cfg = config.get(env_name, {})
    repo_dir = Path(env_cfg.get("repoDir", ""))
    deploy_script = repo_dir / "scripts" / "deploy"
    service = service.lower()
    if service not in ("backend", "frontend", "all"):
        return {"ok": False, "error": "Servico invalido (backend|frontend|all)."}

    cleanup = run_orphan_bot_cleanup(config, log_name=f"restart-{env_name.lower()}.log")

    if service == "all":
        stop = run_powershell(deploy_script / "stop_env.ps1", ["-Environment", env_name])
        if not stop["ok"]:
            return {**stop, "orphanBots": cleanup}
        time.sleep(2)
        started = run_powershell(deploy_script / "start_env.ps1", ["-Environment", env_name])
        return {**started, "orphanBots": cleanup}

    env_cfg = config.get(env_name, {})
    port = env_cfg.get("backendPort") if service == "backend" else env_cfg.get("frontendPort")
    if not port:
        return {"ok": False, "error": "Porta nao configurada."}

    ps_kill = (
        f"$p={port}; Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | "
        f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_kill],
        capture_output=True,
        timeout=30,
    )
    time.sleep(1)

    start_name = "start_backend.ps1" if service == "backend" else "start_frontend.ps1"
    start_script = deploy_script / start_name
    if start_script.is_file():
        started = run_powershell(start_script, ["-Environment", env_name])
    else:
        started = run_powershell(deploy_script / "start_env.ps1", ["-Environment", env_name])
    return {**started, "orphanBots": cleanup}


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def is_secret_key(key: str) -> bool:
    return bool(SECRET_KEY_PATTERNS.search(key))


def mask_env_vars(data: dict[str, str]) -> dict[str, dict[str, Any]]:
    masked: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        masked[key] = {
            "value": MASK_VALUE if is_secret_key(key) else value,
            "masked": is_secret_key(key),
        }
    return masked


def write_env_file(path: Path, updates: dict[str, str], existing: dict[str, str]) -> None:
    merged = dict(existing)
    for key, value in updates.items():
        if is_secret_key(key) and value == MASK_VALUE:
            continue
        merged[key] = value

    lines = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in merged:
                    lines.append(f"{key}={merged.pop(key)}")
                    continue
            lines.append(line)
    for key, value in merged.items():
        lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def remove_env_keys(path: Path, keys: list[str]) -> None:
    if not keys:
        return
    keys_set = {k.strip() for k in keys if k and k.strip()}
    if not keys_set or not path.is_file():
        return
    lines = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in keys_set:
                continue
        lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def get_shared_dir(config: dict[str, Any], env_name: str) -> Path:
    return get_base_dir(config) / "deploy" / env_name / "shared"


def get_env_paths(config: dict[str, Any], env_name: str) -> tuple[Path, Path]:
    shared = get_shared_dir(config, env_name)
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "media").mkdir(parents=True, exist_ok=True)
    ensure_shared_env_seeded(config, env_name)
    return shared / "backend.env", shared / "frontend.env"


def _copy_file_if_exists(src: Path, dest: Path) -> bool:
    if not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(src.read_text(encoding="utf-8-sig", errors="replace"), encoding="utf-8")
    return True


def _ensure_env_key(path: Path, key: str, value: str) -> None:
    existing = parse_env_file(path) if path.is_file() else {}
    if existing.get(key) == value:
        return
    write_env_file(path, {key: value}, existing)


def ensure_shared_env_seeded(config: dict[str, Any], env_name: str) -> None:
    shared = get_shared_dir(config, env_name)
    shared.mkdir(parents=True, exist_ok=True)
    media_dir = shared / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    backend_shared = shared / "backend.env"
    frontend_shared = shared / "frontend.env"
    base_dir = get_base_dir(config)
    current = base_dir / "deploy" / env_name / "current"
    repo_dir = Path(config.get(env_name, {}).get("repoDir", ""))

    if not backend_shared.is_file():
        seeded = False
        for src in (
            current / "backend" / ".env",
            repo_dir / "backend" / ".env",
            repo_dir / "backend" / ".env.example",
        ):
            if _copy_file_if_exists(src, backend_shared):
                seeded = True
                break
        if not seeded:
            backend_shared.write_text(
                "\n".join(
                    [
                        "SECRET_KEY=dev-secret-key-change-in-production",
                        "DEBUG=True",
                        "ALLOWED_HOSTS=localhost,127.0.0.1",
                        f"POSTGRES_DB=pplid_{env_name.lower()}",
                        "POSTGRES_USER=postgres",
                        "POSTGRES_PASSWORD=postgres",
                        "POSTGRES_HOST=localhost",
                        "POSTGRES_PORT=5432",
                        f"SESSION_COOKIE_NAME=pplid_{env_name.lower()}_sessionid",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
    _ensure_env_key(backend_shared, "MEDIA_ROOT", str(media_dir))

    if not frontend_shared.is_file():
        seeded = False
        for src in (current / "frontend" / ".env", repo_dir / "frontend" / ".env"):
            if _copy_file_if_exists(src, frontend_shared):
                seeded = True
                break
        if not seeded:
            env_cfg = config.get(env_name, {})
            fe_port = env_cfg.get("frontendPort") or 5173
            be_port = env_cfg.get("backendPort") or 8000
            frontend_shared.write_text(
                "\n".join(
                    [
                        "VITE_API_BASE_URL=",
                        f"VITE_DEV_SERVER_PORT={fe_port}",
                        f"VITE_BACKEND_PORT={be_port}",
                        f"VITE_BACKEND_PROXY_TARGET=http://localhost:{be_port}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

    # Garante VITE_* alinhadas as portas de infraestrutura.
    try:
        sync_derived_vite_env(config, env_name)
    except Exception:
        pass


def mirror_shared_env_to_current(config: dict[str, Any], env_name: str) -> None:
    backend_shared, frontend_shared = get_env_paths(config, env_name)
    current = get_base_dir(config) / "deploy" / env_name / "current"
    if not (current / "backend").is_dir():
        return
    _copy_file_if_exists(backend_shared, current / "backend" / ".env")
    if (current / "frontend").is_dir() or frontend_shared.is_file():
        _copy_file_if_exists(frontend_shared, current / "frontend" / ".env")


def build_env_payload(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    backend_path, frontend_path = get_env_paths(config, env_name)
    backend = parse_env_file(backend_path)
    frontend = parse_env_file(frontend_path)
    env_cfg = config.get(env_name, {})

    def file_meta(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"lastModified": None}
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            return {"lastModified": mtime.isoformat()}
        except OSError:
            return {"lastModified": None}

    return {
        "environment": env_name,
        "postgresDb": env_cfg.get("postgresDb") or backend.get("POSTGRES_DB"),
        "paths": {"backend": str(backend_path), "frontend": str(frontend_path)},
        "backendMeta": file_meta(backend_path),
        "frontendMeta": file_meta(frontend_path),
        "backend": mask_env_vars(backend),
        "frontend": mask_env_vars(frontend),
        "derivedFrontendKeys": list(DERIVED_FRONTEND_ENV_KEYS),
        "infra": {
            "backendPort": int(env_cfg.get("backendPort") or 0),
            "frontendPort": int(env_cfg.get("frontendPort") or 0),
        },
    }


def build_env_diff(config: dict[str, Any]) -> dict[str, Any]:
    diff: dict[str, Any] = {}
    all_keys: set[str] = set()
    per_env: dict[str, dict[str, str]] = {}
    for name in ENV_ORDER:
        backend_path, _ = get_env_paths(config, name)
        data = parse_env_file(backend_path)
        per_env[name] = {k: v for k, v in data.items() if not is_secret_key(k)}
        all_keys.update(per_env[name].keys())

    for key in sorted(all_keys):
        values = {env: per_env.get(env, {}).get(key) for env in ENV_ORDER}
        if len({v for v in values.values() if v is not None}) > 1:
            diff[key] = values
    return {"diff": diff}


def reveal_env_var(config: dict[str, Any], env_name: str, scope: str, key: str) -> dict[str, Any]:
    if scope not in ("backend", "frontend"):
        raise ValueError("Escopo invalido.")
    key = (key or "").strip()
    if not key:
        raise ValueError("Chave obrigatoria.")
    backend_path, frontend_path = get_env_paths(config, env_name)
    path = backend_path if scope == "backend" else frontend_path
    data = parse_env_file(path)
    if key not in data:
        raise ValueError(f"Variavel nao encontrada: {key}")
    return {
        "environment": env_name,
        "scope": scope,
        "key": key,
        "value": data[key],
        "masked": is_secret_key(key),
    }


def update_env_vars(
    config: dict[str, Any],
    env_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    disabled = require_env_enabled(config, env_name)
    if disabled:
        raise ValueError(disabled["error"])

    ensure_shared_env_seeded(config, env_name)
    backend_path, frontend_path = get_env_paths(config, env_name)
    backend_updates = payload.get("backend") or {}
    frontend_updates = dict(payload.get("frontend") or {})
    # Portas VITE_* sao derivadas de env.config.json — ignorar edicao manual.
    for key in DERIVED_FRONTEND_ENV_KEYS:
        frontend_updates.pop(key, None)
    remove = payload.get("remove") or {}
    backend_remove = remove.get("backend") or []
    frontend_remove = [
        k for k in (remove.get("frontend") or []) if k not in DERIVED_FRONTEND_ENV_KEYS
    ]

    if env_name == "MAIN":
        blocked_without_confirm = {"SECRET_KEY", "DEBUG"}
        if not payload.get("confirmMain"):
            for key in blocked_without_confirm:
                if key in backend_updates:
                    raise ValueError(f"Alteracao de {key} em MAIN requer confirmMain: true")

    if backend_remove:
        remove_env_keys(backend_path, backend_remove)
    if frontend_remove:
        remove_env_keys(frontend_path, frontend_remove)

    if backend_updates:
        write_env_file(backend_path, backend_updates, parse_env_file(backend_path))
    if frontend_updates:
        write_env_file(frontend_path, frontend_updates, parse_env_file(frontend_path))

    mirror_shared_env_to_current(config, env_name)

    return {
        "ok": True,
        "environment": env_name,
        "needsRestart": True,
        "paths": {"backend": str(backend_path), "frontend": str(frontend_path)},
    }


def apply_env_vars(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    return action_restart_service(config, env_name, "backend")


def get_env_config_path(config: dict[str, Any] | None = None) -> Path:
    """Caminho de ops/config/env.config.json (fonte de portas)."""
    if config:
        explicit = config.get("_configPath") or config.get("envConfigPath")
        if explicit:
            return Path(explicit)
        base = get_base_dir(config)
        candidate = base / "ops" / "config" / "env.config.json"
        if candidate.is_file():
            return candidate
    default = Path("C:/PPLID/ops/config/env.config.json")
    return default


def derived_vite_vars(backend_port: int, frontend_port: int) -> dict[str, str]:
    return {
        "VITE_DEV_SERVER_PORT": str(frontend_port),
        "VITE_BACKEND_PORT": str(backend_port),
        "VITE_BACKEND_PROXY_TARGET": f"http://localhost:{backend_port}",
    }


def build_infra_payload(config: dict[str, Any], env_name: str) -> dict[str, Any]:
    env_cfg = config.get(env_name) or {}
    backend_port = int(env_cfg.get("backendPort") or 0)
    frontend_port = int(env_cfg.get("frontendPort") or 0)
    return {
        "environment": env_name,
        "backendPort": backend_port,
        "frontendPort": frontend_port,
        "configPath": str(get_env_config_path(config)),
        "derivedVite": derived_vite_vars(backend_port, frontend_port),
        "hint": (
            "Estas portas controlam o start real (Django e vite preview --port). "
            "VITE_DEV_SERVER_PORT / VITE_BACKEND_* sao derivadas automaticamente."
        ),
    }


def _validate_port(value: Any, label: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} invalida.") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"{label} deve estar entre 1 e 65535.")
    return port


def _collect_used_ports(config: dict[str, Any], *, exclude_env: str | None = None) -> dict[int, str]:
    used: dict[int, str] = {}
    ops_port = config.get("opsConsolePort")
    if ops_port:
        used[int(ops_port)] = "opsConsole"
    for name in ENV_ORDER:
        if exclude_env and name == exclude_env:
            continue
        env_cfg = config.get(name) or {}
        for key, label in (("backendPort", "backend"), ("frontendPort", "frontend")):
            raw = env_cfg.get(key)
            if raw is None:
                continue
            used[int(raw)] = f"{name}.{label}"
    return used


def sync_derived_vite_env(config: dict[str, Any], env_name: str) -> None:
    """Atualiza VITE_* derivadas em shared/frontend.env e espelha para current."""
    env_cfg = config.get(env_name) or {}
    backend_port = int(env_cfg.get("backendPort") or 0)
    frontend_port = int(env_cfg.get("frontendPort") or 0)
    if not backend_port or not frontend_port:
        return
    shared = get_shared_dir(config, env_name)
    shared.mkdir(parents=True, exist_ok=True)
    (shared / "media").mkdir(parents=True, exist_ok=True)
    frontend_path = shared / "frontend.env"
    write_env_file(
        frontend_path,
        derived_vite_vars(backend_port, frontend_port),
        parse_env_file(frontend_path),
    )
    mirror_shared_env_to_current(config, env_name)


def update_infra_ports(
    config: dict[str, Any],
    env_name: str,
    payload: dict[str, Any],
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    disabled = require_env_enabled(config, env_name)
    if disabled:
        raise ValueError(disabled["error"])

    env_cfg = config.get(env_name)
    if not isinstance(env_cfg, dict):
        raise ValueError("Ambiente invalido.")

    old_backend = int(env_cfg.get("backendPort") or 0)
    old_frontend = int(env_cfg.get("frontendPort") or 0)
    backend_port = _validate_port(
        payload.get("backendPort", old_backend),
        "backendPort",
    )
    frontend_port = _validate_port(
        payload.get("frontendPort", old_frontend),
        "frontendPort",
    )
    if backend_port == frontend_port:
        raise ValueError("backendPort e frontendPort devem ser diferentes.")

    used = _collect_used_ports(config, exclude_env=env_name)
    for port, owner in ((backend_port, "backendPort"), (frontend_port, "frontendPort")):
        if port in used:
            raise ValueError(f"Porta {port} ({owner}) ja em uso por {used[port]}.")

    path = Path(config_path) if config_path else get_env_config_path(config)
    if not path.is_file():
        raise ValueError(f"env.config.json nao encontrado: {path}")

    disk = json.loads(path.read_text(encoding="utf-8"))
    if env_name not in disk or not isinstance(disk[env_name], dict):
        raise ValueError(f"Bloco {env_name} ausente em env.config.json.")
    disk[env_name]["backendPort"] = backend_port
    disk[env_name]["frontendPort"] = frontend_port
    path.write_text(json.dumps(disk, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    env_cfg["backendPort"] = backend_port
    env_cfg["frontendPort"] = frontend_port
    sync_derived_vite_env(config, env_name)

    return {
        "ok": True,
        "environment": env_name,
        "backendPort": backend_port,
        "frontendPort": frontend_port,
        "previous": {"backendPort": old_backend, "frontendPort": old_frontend},
        "derivedVite": derived_vite_vars(backend_port, frontend_port),
        "needsRestart": True,
        "changed": {
            "backend": backend_port != old_backend,
            "frontend": frontend_port != old_frontend,
        },
    }


def apply_infra_ports(
    config: dict[str, Any],
    env_name: str,
    *,
    previous: dict[str, Any] | None = None,
    restart: str = "changed",
) -> dict[str, Any]:
    """Reinicia servicos apos troca de portas.

    restart: 'changed' | 'backend' | 'frontend' | 'all'
    """
    disabled = require_env_enabled(config, env_name)
    if disabled:
        return disabled

    env_cfg = config.get(env_name) or {}
    prev = previous or {}
    old_backend = int(prev.get("backendPort") or env_cfg.get("backendPort") or 0)
    old_frontend = int(prev.get("frontendPort") or env_cfg.get("frontendPort") or 0)
    new_backend = int(env_cfg.get("backendPort") or 0)
    new_frontend = int(env_cfg.get("frontendPort") or 0)

    backend_changed = old_backend != new_backend
    frontend_changed = old_frontend != new_frontend

    if restart == "all":
        do_backend = do_frontend = True
    elif restart == "backend":
        do_backend, do_frontend = True, False
    elif restart == "frontend":
        do_backend, do_frontend = False, True
    else:
        do_backend = backend_changed
        do_frontend = frontend_changed
        if not do_backend and not do_frontend:
            do_frontend = True

    results: dict[str, Any] = {"ok": True, "backend": None, "frontend": None}

    def _kill_port(port: int) -> None:
        if not port:
            return
        ps_kill = (
            f"$p={port}; Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | "
            f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_kill],
            capture_output=True,
            timeout=30,
        )

    repo_dir = Path(env_cfg.get("repoDir", ""))
    deploy_script = repo_dir / "scripts" / "deploy"

    if do_backend:
        _kill_port(old_backend)
        _kill_port(new_backend)
        time.sleep(1)
        start_backend = deploy_script / "start_backend.ps1"
        if start_backend.is_file():
            results["backend"] = run_powershell(start_backend, ["-Environment", env_name])
        else:
            results["backend"] = action_restart_service(config, env_name, "backend")

    if do_frontend:
        _kill_port(old_frontend)
        _kill_port(new_frontend)
        time.sleep(1)
        start_frontend = deploy_script / "start_frontend.ps1"
        if start_frontend.is_file():
            results["frontend"] = run_powershell(start_frontend, ["-Environment", env_name])
        else:
            results["frontend"] = action_restart_service(config, env_name, "frontend")

    for key in ("backend", "frontend"):
        part = results.get(key)
        if isinstance(part, dict) and not part.get("ok", True):
            results["ok"] = False
            results["error"] = part.get("error") or f"Falha ao reiniciar {key}."
            break

    return results


def _pg_connect_params(env_path: Path) -> dict[str, str] | None:
    env = parse_env_file(env_path)
    host = env.get("POSTGRES_HOST", "localhost")
    port = env.get("POSTGRES_PORT", "5432")
    db = env.get("POSTGRES_DB", "")
    user = env.get("POSTGRES_USER", "postgres")
    password = env.get("POSTGRES_PASSWORD", "")
    if not db:
        return None
    return {"host": host, "port": port, "dbname": db, "user": user, "password": password}


def _migrations_lock_for(env_name: str) -> threading.Lock:
    with _MIGRATIONS_LOCKS_GUARD:
        lock = _MIGRATIONS_LOCKS.get(env_name)
        if lock is None:
            lock = threading.Lock()
            _MIGRATIONS_LOCKS[env_name] = lock
        return lock


def fetch_migrations_status(
    config: dict[str, Any],
    env_name: str,
    *,
    force: bool = False,
    use_cache: bool = True,
) -> dict[str, Any]:
    """
    Explicit showmigrations — cached >=5 min, single-flight per env.
    Not called from health, overview-lite, or the periodic collector.
    """
    now = time.time()
    if use_cache and not force:
        cached = _MIGRATIONS_CACHE.get(env_name)
        if cached and (now - cached[0]) < _MIGRATIONS_CACHE_TTL_SEC:
            result = dict(cached[1])
            result["fromCache"] = True
            result["stale"] = False
            return result

    lock = _migrations_lock_for(env_name)
    if not lock.acquire(blocking=False):
        cached = _MIGRATIONS_CACHE.get(env_name)
        if cached:
            result = dict(cached[1])
            result["fromCache"] = True
            result["stale"] = True
            result["inflight"] = True
            return result
        return {
            "ok": False,
            "pending": None,
            "output": "",
            "inflight": True,
            "fromCache": False,
            "error": "migrations probe in flight",
        }

    try:
        if use_cache and not force:
            cached = _MIGRATIONS_CACHE.get(env_name)
            if cached and (time.time() - cached[0]) < _MIGRATIONS_CACHE_TTL_SEC:
                result = dict(cached[1])
                result["fromCache"] = True
                return result

        _MIGRATIONS_INFLIGHT[env_name] = True
        base_dir = get_base_dir(config)
        current = base_dir / "deploy" / env_name / "current" / "backend"
        venv_python = current / ".venv" / "Scripts" / "python.exe"
        if not venv_python.is_file():
            result = {
                "ok": False,
                "pending": None,
                "output": "venv python not found",
                "fromCache": False,
                "inflight": False,
            }
            _MIGRATIONS_CACHE[env_name] = (time.time(), result)
            return result

        try:
            proc = subprocess.run(
                [str(venv_python), "manage.py", "showmigrations", "--plan"],
                cwd=str(current),
                capture_output=True,
                text=True,
                timeout=60,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            pending = sum(1 for line in output.splitlines() if "[ ]" in line)
            result = {
                "ok": proc.returncode == 0,
                "pending": pending,
                "output": output[-3000:],
                "fromCache": False,
                "inflight": False,
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = {
                "ok": False,
                "pending": None,
                "output": str(exc),
                "fromCache": False,
                "inflight": False,
                "checkedAt": datetime.now(timezone.utc).isoformat(),
            }
        _MIGRATIONS_CACHE[env_name] = (time.time(), result)
        return result
    finally:
        _MIGRATIONS_INFLIGHT[env_name] = False
        lock.release()


def fetch_database_metrics(
    config: dict[str, Any],
    env_name: str,
    *,
    backend_reachable: bool | None = None,
    use_cache: bool = True,
    include_migrations: bool = False,
) -> dict[str, Any]:
    cache_key = env_name
    now = time.time()
    if use_cache:
        cached = _DB_METRICS_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _DB_METRICS_CACHE_TTL_SEC:
            result = dict(cached[1])
            if include_migrations and not (result.get("migrations") or {}).get("checkedAt"):
                result["migrations"] = fetch_migrations_status(config, env_name, use_cache=True)
            return result

    backend_path, _ = get_env_paths(config, env_name)
    params = _pg_connect_params(backend_path)
    env_cfg = config.get(env_name, {})
    if not params:
        result = {
            "environment": env_name,
            "ok": False,
            "error": "Credenciais Postgres nao encontradas.",
            "database": env_cfg.get("postgresDb"),
        }
        if use_cache:
            _DB_METRICS_CACHE[cache_key] = (now, result)
        return result

    dbname = params["dbname"]
    result: dict[str, Any] = {
        "environment": env_name,
        "ok": True,
        "database": dbname,
        "connections": {},
        "sizeBytes": None,
        "sizeHuman": None,
        "blockingLocks": [],
        "slowQueries": [],
        "migrations": {"ok": False, "pending": None, "output": ""},
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }

    try:
        from server_db import collect_pg_metrics

        pg_metrics = collect_pg_metrics(config, env_name)
        result.update(pg_metrics)
    except Exception:
        pass

    if include_migrations and backend_reachable is not False:
        result["migrations"] = fetch_migrations_status(config, env_name, use_cache=use_cache)
    else:
        # Surface cached migrations if present, without spawning subprocess.
        cached_mig = _MIGRATIONS_CACHE.get(env_name)
        if cached_mig:
            mig = dict(cached_mig[1])
            mig["fromCache"] = True
            result["migrations"] = mig

    if use_cache:
        _DB_METRICS_CACHE[cache_key] = (time.time(), result)
    return result


def _human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def probe_frontend(port: int, timeout: float = 3.0) -> dict[str, Any]:
    url = f"http://127.0.0.1:{port}/"
    try:
        request = urllib.request.Request(url)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"ok": response.status < 500, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"ok": exc.code < 500, "status": exc.code}
    except Exception:  # noqa: BLE001
        return {"ok": False, "status": None}


def probe_port_listening(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def build_availability_extended(
    runtime: dict[str, Any],
    env_cfg: dict[str, Any],
    db_metrics: dict[str, Any] | None = None,
    *,
    probe_io: bool = True,
) -> dict[str, Any]:
    """
    Build availability aggregate.

    probe_io=False: use only runtime snapshot fields (overview-lite hot path).
    Never mark offline solely because health timed out while the port is up.
    """
    backend_port = int(env_cfg.get("backendPort") or 0)
    frontend_port = int(env_cfg.get("frontendPort") or 0)
    reachable = bool(runtime.get("reachable"))
    avail_class = str(runtime.get("availabilityClass") or "")
    timed_out = bool(runtime.get("timedOut"))
    consecutive = int(runtime.get("consecutiveFailures") or 0)

    backend_port_up = runtime.get("backendPortUp")
    frontend_port_up = runtime.get("frontendPortUp")

    if probe_io:
        if backend_port_up is None and backend_port:
            backend_port_up = probe_port_listening(backend_port)
        if frontend_port and frontend_port_up is None:
            frontend_probe = probe_frontend(frontend_port)
            frontend_ok = bool(frontend_probe.get("ok"))
        elif frontend_port_up is not None:
            frontend_ok = bool(frontend_port_up)
        else:
            frontend_ok = False
    else:
        frontend_ok = bool(frontend_port_up) if frontend_port_up is not None else False
        if backend_port_up is None:
            backend_port_up = reachable  # best effort from snapshot

    backend_ok = bool(backend_port_up) and (
        reachable and runtime.get("status") in ("healthy", "degraded")
        or avail_class in ("stale", "saturated", "degraded")
    )
    # Port up + health timeout => saturated/degraded, not offline.
    if timed_out and backend_port_up:
        backend_ok = True

    db_ok = reachable and runtime.get("database") == "ok"
    if not db_ok and avail_class in ("stale", "saturated") and runtime.get("database") == "ok":
        db_ok = True
    if not db_ok:
        if db_metrics and db_metrics.get("ok"):
            db_ok = True
        elif probe_io:
            pg_port = int(env_cfg.get("postgresPort") or env_cfg.get("POSTGRES_PORT") or 5432)
            db_ok = probe_port_listening(pg_port)

    components = runtime.get("components") or {}
    version = runtime.get("version")
    version_ok = bool(version and version != "unknown")

    availability = {
        "backend": bool(backend_ok),
        "frontend": frontend_ok,
        "database": bool(db_ok),
        "version": version_ok,
        "falhas": components.get("falhas", "skip"),
        "availabilityClass": avail_class or None,
        "backendPortUp": backend_port_up,
        "frontendPortUp": frontend_port_up if frontend_port_up is not None else frontend_ok,
        "stale": bool(runtime.get("stale")),
        "ageMs": runtime.get("ageMs"),
        "consecutiveFailures": consecutive,
    }

    if db_metrics and db_metrics.get("ok"):
        conns = db_metrics.get("connections") or {}
        availability["dbConnections"] = conns.get("total", 0)

    if avail_class == "offline" or (backend_port_up is False and consecutive >= 3 and not reachable):
        aggregate = "offline"
    elif avail_class == "saturated" or (timed_out and backend_port_up):
        aggregate = "saturated"
    elif avail_class == "stale" or runtime.get("stale"):
        aggregate = "stale"
    else:
        healthy_count = sum(1 for k in ("backend", "frontend", "database") if availability.get(k))
        if healthy_count == 3 and version_ok:
            aggregate = "healthy"
        elif healthy_count == 0 or not availability.get("backend"):
            aggregate = "unhealthy"
        else:
            aggregate = "degraded"

    return {"aggregate": aggregate, "components": availability}


def build_services_from_availability(
    env_name: str,
    env_cfg: dict[str, Any],
    avail_extended: dict[str, Any],
    db_metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    components = avail_extended.get("components") or {}
    services = [
        {
            "id": "backend",
            "name": "Backend",
            "port": env_cfg.get("backendPort"),
            "status": "ok" if components.get("backend") else "fail",
        },
        {
            "id": "frontend",
            "name": "Frontend",
            "port": env_cfg.get("frontendPort"),
            "status": "ok" if components.get("frontend") else "fail",
        },
        {
            "id": "postgres",
            "name": "Postgres",
            "database": env_cfg.get("postgresDb"),
            "status": "ok" if components.get("database") else "fail",
            "connections": (db_metrics or {}).get("connections", {}).get("total"),
            "sizeHuman": (db_metrics or {}).get("sizeHuman"),
        },
    ]
    return services


def build_services(
    env_name: str,
    env_cfg: dict[str, Any],
    runtime: dict[str, Any],
    db_metrics: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    avail = build_availability_extended(runtime, env_cfg, db_metrics)
    return build_services_from_availability(env_name, env_cfg, avail, db_metrics)


LOG_REDACT_PATTERNS = [
    re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|authorization)\s*[=:]\s*\S+"),
    re.compile(r"(?i)Bearer\s+\S+"),
    re.compile(r"(?i)postgresql://[^\s]+"),
    re.compile(r"(?i)mysql://[^\s]+"),
    re.compile(r"(?i)SECRET_KEY\s*=\s*\S+"),
    re.compile(r"(?i)DATABASE_URL\s*=\s*\S+"),
]

LOG_LINE_PATTERN = re.compile(
    r"^\[([\d\-: ]+)\]\s*\[(INFO|OK|WARN|ERROR|SUCCESS)\]\s*(.+)$"
)


def redact_log_line(text: str) -> str:
    out = str(text or "")
    for pattern in LOG_REDACT_PATTERNS:
        out = pattern.sub(lambda m: re.sub(r"=\s*\S+$", "=***", m.group(0)), out)
    return out


def parse_log_line(line: str) -> dict[str, Any]:
    cleaned = redact_log_line(line)
    match = LOG_LINE_PATTERN.match(cleaned.strip())
    if match:
        return {
            "time": match.group(1).strip(),
            "level": match.group(2),
            "text": fix_mojibake(match.group(3).strip()),
            "raw": cleaned,
        }
    legacy = re.match(r"^\[([\d\-: ]+)\]\s*(.+)$", cleaned.strip())
    if legacy:
        return {
            "time": legacy.group(1).strip(),
            "level": "INFO",
            "text": fix_mojibake(legacy.group(2).strip()),
            "raw": cleaned,
        }
    return {"time": None, "level": "INFO", "text": fix_mojibake(cleaned), "raw": cleaned}


def _read_json_file(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, (dict, list)) else None
    except (json.JSONDecodeError, OSError):
        return None


def load_run_steps(base_dir: Path, env_name: str, run_id: str) -> list[dict[str, Any]]:
    path = base_dir / "deploy" / env_name / "logs" / "runs" / run_id / "steps.json"
    data = _read_json_file(path)
    if not isinstance(data, list):
        return []
    steps: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        step = dict(item)
        if step.get("label"):
            step["label"] = fix_mojibake(str(step["label"]))
        if step.get("error"):
            step["error"] = fix_mojibake(str(step["error"]))
        steps.append(step)
    return steps


def _runs_index_path(base_dir: Path, env_name: str) -> Path:
    return base_dir / "deploy" / env_name / "logs" / "runs-index.json"


def update_runs_index(
    base_dir: Path,
    env_name: str,
    *,
    run_id: str,
    to_sha: str = "",
    result: str = "",
    finished_at: str = "",
) -> None:
    path = _runs_index_path(base_dir, env_name)
    index: dict[str, Any] = {}
    existing = _read_json_file(path)
    if isinstance(existing, dict):
        index = dict(existing)
    entries = index.get("entries")
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if isinstance(e, dict) and e.get("runId") != run_id]
    if to_sha:
        entries.insert(
            0,
            {
                "runId": run_id,
                "toSha": to_sha,
                "result": result,
                "finishedAt": finished_at,
            },
        )
    entries = entries[:200]
    index["entries"] = entries
    index["updatedAt"] = datetime.now(timezone.utc).isoformat()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_run_summary(base_dir: Path, env_name: str, run_id: str) -> dict[str, Any]:
    path = base_dir / "deploy" / env_name / "logs" / "runs" / run_id / "run-summary.json"
    data = _read_json_file(path)
    return data if isinstance(data, dict) else {}


def find_run_id_by_sha(base_dir: Path, env_name: str, sha: str) -> str | None:
    if not sha or sha in ("—", "unknown"):
        return None
    needle = sha.strip().lower()
    matches: list[tuple[str, dict[str, Any]]] = []
    index_path = _runs_index_path(base_dir, env_name)
    index = _read_json_file(index_path)
    if isinstance(index, dict):
        entries = index.get("entries") or []
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                to_sha = str(entry.get("toSha") or "").lower()
                if to_sha and (
                    to_sha == needle
                    or to_sha.startswith(needle)
                    or needle.startswith(to_sha)
                ):
                    run_id = str(entry.get("runId") or "")
                    if run_id:
                        summary = load_run_summary(base_dir, env_name, run_id)
                        matches.append((run_id, summary))
    runs_dir = base_dir / "deploy" / env_name / "logs" / "runs"
    if runs_dir.is_dir():
        for run_dir in sorted(runs_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            run_id = run_dir.name
            if any(m[0] == run_id for m in matches):
                continue
            summary = load_run_summary(base_dir, env_name, run_id)
            to_sha = str(summary.get("toSha") or "").lower()
            if to_sha and (to_sha == needle or to_sha.startswith(needle) or needle.startswith(to_sha)):
                matches.append((run_id, summary))
                continue
            manifest_path = run_dir / "manifest.json"
            if manifest_path.is_file():
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    target = str(manifest.get("targetSha") or "").lower()
                    if target and (target == needle or target.startswith(needle) or needle.startswith(target)):
                        matches.append((run_id, summary))
                except (json.JSONDecodeError, OSError):
                    pass
    if not matches:
        return None
    success = [m for m in matches if str(m[1].get("result") or "").lower() == "success"]
    pool = success or matches
    pool.sort(key=lambda item: str(item[1].get("finishedAt") or item[1].get("startedAt") or item[0]))
    return pool[0][0]


def find_previous_failed_run(
    base_dir: Path,
    env_name: str,
    target_sha: str,
    exclude_run_id: str = "",
) -> dict[str, Any] | None:
    if not target_sha:
        return None
    runs_dir = base_dir / "deploy" / env_name / "logs" / "runs"
    if not runs_dir.is_dir():
        return None
    needle = target_sha.lower()[:7]
    for run_dir in sorted(runs_dir.iterdir(), reverse=True):
        if not run_dir.is_dir() or run_dir.name == exclude_run_id:
            continue
        summary = _read_json_file(run_dir / "run-summary.json") or {}
        if summary.get("result") != "failed":
            continue
        to_sha = str(summary.get("toSha") or "").lower()
        if not to_sha:
            continue
        if to_sha.startswith(needle) or needle.startswith(to_sha[:7]):
            return {
                "runId": run_dir.name,
                "failedStep": summary.get("failedStep"),
                "lastError": redact_log_line(str(summary.get("lastError") or "")),
                "finishedAt": summary.get("finishedAt"),
            }
    return None


PHASE_LOG_FILES = {
    "building": "build.log",
    "validating": "validate.log",
    "promoting": "promote.log",
}


def _read_tail_lines(path: Path, limit: int = 200, chunk_size: int = 65536) -> list[str]:
    if not path.is_file():
        return []
    try:
        size = path.stat().st_size
        if size <= 0:
            return []
        with path.open("rb") as handle:
            read_size = min(size, max(chunk_size, limit * 256))
            handle.seek(max(0, size - read_size))
            data = handle.read()
        text = data.decode("utf-8", errors="replace")
        lines = text.splitlines()
        if read_size < size and lines:
            lines = lines[1:]
        return lines[-limit:] if limit > 0 else lines
    except OSError:
        return []


def _tail_log_preview(run_dir: Path, log_name: str, limit: int = 8) -> dict[str, Any]:
    path = run_dir / log_name
    if not path.is_file():
        return {"file": log_name, "parsed": [], "lines": []}
    tail = [redact_log_line(line) for line in _read_tail_lines(path, 200)]
    parsed = [parse_log_line(line) for line in tail]
    return {
        "file": log_name,
        "parsed": parsed[-limit:],
        "lines": tail[-limit:],
    }


def load_deploy_progress(
    base_dir: Path,
    env_name: str,
    run_id: str,
    *,
    pipeline_status: str = "",
    target_sha: str = "",
    use_cache: bool = True,
) -> dict[str, Any]:
    cache_key = f"{env_name}:{run_id}:{pipeline_status}"
    now = time.time()
    if use_cache:
        cached = _DEPLOY_PROGRESS_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _DEPLOY_PROGRESS_CACHE_TTL_SEC:
            return cached[1]

    steps = load_run_steps(base_dir, env_name, run_id)
    summary = load_run_summary(base_dir, env_name, run_id)
    current = next((s for s in steps if s.get("status") == "running"), None)
    if not current:
        current = next(
            (s for s in reversed(steps) if s.get("status") in ("success", "warning", "error")),
            None,
        )
    last_error = None
    for step in steps:
        if step.get("status") == "error" and step.get("error"):
            last_error = {
                "step": step.get("id"),
                "stepLabel": step.get("label"),
                "message": redact_log_line(str(step.get("error"))),
                "at": step.get("finishedAt"),
            }
            break
    if not last_error and summary.get("lastError"):
        last_error = {
            "step": summary.get("failedStep"),
            "stepLabel": summary.get("failedStep"),
            "message": fix_mojibake(redact_log_line(str(summary.get("lastError")))),
            "at": summary.get("finishedAt"),
        }

    run_dir = base_dir / "deploy" / env_name / "logs" / "runs" / run_id
    phase_key = pipeline_status or str((current or {}).get("phase") or "")
    active_log = PHASE_LOG_FILES.get(phase_key, "pipeline.log")
    if phase_key == "failed" and last_error and last_error.get("step"):
        step_id = str(last_error["step"])
        if step_id.startswith("build") or step_id in ("git_fetch", "deps_backend", "build_backend", "build_frontend"):
            active_log = "build.log"
        elif step_id.startswith("valid"):
            active_log = "validate.log"
        elif step_id in ("restart_services", "publish_done", "health_check") or "promot" in step_id:
            active_log = "promote.log"

    log_preview = _tail_log_preview(run_dir, active_log) if run_dir.is_dir() else {"file": active_log, "parsed": [], "lines": []}
    previous_failed = find_previous_failed_run(base_dir, env_name, target_sha or str(summary.get("toSha") or ""), run_id)

    payload = {
        "steps": steps,
        "currentStep": current,
        "lastError": last_error,
        "summary": summary,
        "activeLogKey": active_log,
        "logPreview": log_preview,
        "previousFailedRun": previous_failed,
    }
    if use_cache:
        _DEPLOY_PROGRESS_CACHE[cache_key] = (time.time(), payload)
    return payload


RUN_LOG_NAMES = ("pipeline.log", "build.log", "validate.log", "promote.log", "rollback.log")

STEP_LABELS = {
    "prepare": "Preparacao",
    "git_fetch": "Checkout do codigo",
    "deps_backend": "Instalacao de dependencias",
    "build_backend": "Build backend",
    "build_frontend": "Build frontend",
    "validate": "Validacoes",
    "restart_services": "Publicacao",
    "health_check": "Health check",
    "publish_done": "Pos-deploy",
}


def get_ops_store_db_path(base_dir: Path) -> Path | None:
    cfg_path = base_dir / "machine.config.json"
    if not cfg_path.is_file():
        return None
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        store = raw.get("opsStore") or {}
        db_raw = store.get("path")
        if not db_raw:
            return None
        return Path(str(db_raw))
    except (json.JSONDecodeError, OSError):
        return None


def _read_lines_from_offset(path: Path, offset: int = 0, limit: int = 500) -> tuple[list[str], int]:
    if not path.is_file():
        return [], offset
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        total = len(all_lines)
        start = max(0, min(offset, total))
        chunk = all_lines[start : start + limit]
        return chunk, total
    except OSError:
        return [], offset


def _format_deploy_log_display_ts(logged_at: str) -> str:
    """Normalize SQLite ISO timestamps to file-log style for UI parsers."""
    raw = str(logged_at or "").strip()
    if not raw:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        normalized = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is not None:
            dt = dt.astimezone()
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        if re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", raw):
            return raw[:19]
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _load_log_chunk_from_sqlite(
    base_dir: Path,
    env_name: str,
    run_id: str,
    log_name: str,
    since_id: int,
    limit: int,
) -> dict[str, Any]:
    db_path = get_ops_store_db_path(base_dir)
    if not db_path:
        return {"lines": [], "parsed": [], "nextOffset": since_id}
    ops_root = resolve_ops_lib_dir({"logDir": str(base_dir / "logs")})
    if str(ops_root) not in sys.path:
        sys.path.insert(0, str(ops_root))
    try:
        import ops_store  # type: ignore

        rows = ops_store.tail_deploy_logs(
            env_name, run_id, log_name, since_id=since_id, limit=limit, db_path=db_path
        )
    except Exception:
        return {"lines": [], "parsed": [], "nextOffset": since_id}

    lines: list[str] = []
    parsed: list[dict[str, Any]] = []
    next_offset = since_id
    for row in rows:
        next_offset = max(next_offset, int(row.get("id") or 0))
        display_ts = _format_deploy_log_display_ts(str(row.get("logged_at") or ""))
        text = f"[{display_ts}] [{row.get('level', 'INFO')}] {row.get('message', '')}"
        lines.append(redact_log_line(text))
        parsed.append(parse_log_line(lines[-1]))
    return {"lines": lines, "parsed": parsed, "nextOffset": next_offset}


def load_run_log_chunk(
    base_dir: Path,
    env_name: str,
    run_id: str,
    log_name: str,
    *,
    file_offset: int = 0,
    sqlite_offset: int = 0,
    offset: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    if offset is not None:
        file_offset = offset
        sqlite_offset = 0

    run_dir = base_dir / "deploy" / env_name / "logs" / "runs" / run_id
    path = run_dir / log_name
    file_lines, file_total = _read_lines_from_offset(path, file_offset, limit)
    sqlite_chunk = _load_log_chunk_from_sqlite(
        base_dir, env_name, run_id, log_name, sqlite_offset, limit
    )

    safe_file = [redact_log_line(line) for line in file_lines]
    file_parsed = [parse_log_line(line) for line in safe_file]
    sqlite_lines = sqlite_chunk.get("lines") or []
    sqlite_parsed = sqlite_chunk.get("parsed") or []
    next_sqlite = int(sqlite_chunk.get("nextOffset") or sqlite_offset)

    merged_lines = safe_file + sqlite_lines
    merged_parsed = file_parsed + sqlite_parsed

    if not merged_lines:
        return {
            "file": log_name,
            "lines": [],
            "parsed": [],
            "nextFileOffset": file_total if path.is_file() else file_offset,
            "nextSqliteOffset": next_sqlite,
            "nextOffset": file_total,
            "source": "none",
        }

    source = "merged" if safe_file and sqlite_lines else ("file" if safe_file else "sqlite")
    return {
        "file": log_name,
        "lines": merged_lines,
        "parsed": merged_parsed,
        "nextFileOffset": file_total,
        "nextSqliteOffset": next_sqlite,
        "nextOffset": file_total,
        "source": source,
    }


def _normalize_log_offset_value(raw: str | int | dict[str, int]) -> dict[str, int]:
    if isinstance(raw, dict):
        return {"file": int(raw.get("file", 0)), "sqlite": int(raw.get("sqlite", 0))}
    if isinstance(raw, int):
        return {"file": raw, "sqlite": 0}
    text = str(raw).strip()
    if not text:
        return {"file": 0, "sqlite": 0}
    if "/s" in text:
        file_part, _, sqlite_part = text.partition("/s")
        if file_part.startswith("f"):
            file_off = int(file_part[1:] or "0")
        else:
            file_off = int(file_part or "0")
        return {"file": file_off, "sqlite": int(sqlite_part or "0")}
    if text.startswith("f"):
        return {"file": int(text[1:] or "0"), "sqlite": 0}
    return {"file": int(text), "sqlite": 0}


def parse_log_offsets(raw: str | dict | None) -> dict[str, dict[str, int]]:
    if isinstance(raw, dict):
        return {str(k): _normalize_log_offset_value(v) for k, v in raw.items()}
    offsets: dict[str, dict[str, int]] = {}
    if not raw:
        return offsets
    for part in str(raw).split(","):
        piece = part.strip()
        if not piece or ":" not in piece:
            continue
        name, _, rest = piece.partition(":")
        name = name.strip()
        if not name:
            continue
        offsets[name] = _normalize_log_offset_value(rest)
    return offsets


def build_failure_payload(
    summary: dict[str, Any],
    steps: list[dict[str, Any]],
    progress: dict[str, Any],
) -> dict[str, Any] | None:
    if str(summary.get("result") or "") != "failed" and not progress.get("lastError"):
        return None

    last_error = progress.get("lastError") or {}
    step_id = str(last_error.get("step") or summary.get("failedStep") or "")
    step_label = str(last_error.get("stepLabel") or "")
    if not step_label or step_label == step_id:
        for step in steps:
            if str(step.get("id")) == step_id:
                step_label = str(step.get("label") or STEP_LABELS.get(step_id, step_id))
                break
        if not step_label:
            step_label = STEP_LABELS.get(step_id, step_id or "—")

    error_detail = summary.get("errorDetail")
    if isinstance(error_detail, dict):
        detail = dict(error_detail)
    else:
        detail = {}

    message = fix_mojibake(
        redact_log_line(str(last_error.get("message") or summary.get("lastError") or detail.get("message") or ""))
    )
    if not message:
        return None

    return {
        "step": step_id,
        "stepLabel": step_label,
        "message": message,
        "rootCause": detail.get("rootCause"),
        "package": detail.get("package"),
        "versions": detail.get("versions") or [],
        "command": detail.get("command"),
        "recommendation": detail.get("recommendation"),
        "at": last_error.get("at") or summary.get("finishedAt"),
    }


def compute_deploy_progress_pct(steps: list[dict[str, Any]]) -> int:
    if not steps:
        return 0
    weights = {
        "prepare": 5,
        "git_fetch": 10,
        "deps_backend": 25,
        "build_backend": 15,
        "build_frontend": 15,
        "validate": 15,
        "restart_services": 8,
        "health_check": 5,
        "publish_done": 2,
    }
    total_weight = sum(weights.values())
    earned = 0
    for step in steps:
        sid = str(step.get("id") or "")
        w = weights.get(sid, 5)
        status = str(step.get("status") or "")
        if status in ("success", "warning"):
            earned += w
        elif status == "running":
            earned += w // 2
    return max(0, min(100, int(round(earned * 100 / total_weight))))


def build_deploy_summary(
    base_dir: Path,
    env_name: str,
    deploy_state: dict[str, Any],
    stored: dict[str, Any],
) -> dict[str, Any] | None:
    status = str(deploy_state.get("status") or "idle")
    run_id = str(deploy_state.get("runId") or "")
    if status not in BUSY_STATUSES and status != "failed":
        return None

    branch = str(stored.get("branch") or ENV_BRANCH.get(env_name, "dev"))
    target_sha = str(deploy_state.get("targetSha") or stored.get("gitSha") or "—")
    active_sha = str(deploy_state.get("activeSha") or stored.get("deployedSha") or "—")

    phase_label = {
        "building": "build",
        "validating": "validacao",
        "promoting": "publicacao",
        "watching": "monitoramento",
        "failed": "falha",
    }.get(status, status)

    message = f"Detectada atualizacao na branch {branch}. Iniciando {phase_label} e deploy..."
    if status == "building":
        message = f"Detectada nova atualizacao na branch {branch}. Iniciando processo de build e deploy..."
    elif status == "validating":
        message = f"Build concluido para {target_sha}. Executando validacoes..."
    elif status == "promoting":
        message = f"Validacao concluida. Publicando commit {target_sha}..."
    elif status == "failed":
        message = f"Deploy falhou para commit {target_sha}."

    progress_pct = 0
    current_step_label = ""
    if run_id:
        steps = load_run_steps(base_dir, env_name, run_id)
        progress_pct = compute_deploy_progress_pct(steps)
        current = next((s for s in steps if s.get("status") == "running"), None)
        if current:
            current_step_label = str(current.get("label") or STEP_LABELS.get(str(current.get("id")), ""))

    return {
        "message": message,
        "phase": status,
        "branch": branch,
        "fromSha": active_sha,
        "toSha": target_sha,
        "runId": run_id,
        "progressPct": progress_pct,
        "currentStepLabel": current_step_label,
    }


def load_run_logs(
    base_dir: Path,
    env_name: str,
    run_id: str,
    *,
    log_offsets: dict[str, int] | None = None,
) -> dict[str, Any]:
    run_dir = base_dir / "deploy" / env_name / "logs" / "runs" / run_id
    if not run_dir.is_dir():
        return {"environment": env_name, "runId": run_id, "found": False, "logs": {}}

    offsets = parse_log_offsets(log_offsets)
    logs: dict[str, Any] = {}
    has_offsets = bool(log_offsets)
    for name in RUN_LOG_NAMES:
        off = offsets.get(name, {"file": 0, "sqlite": 0})
        poll_limit = 500 if name == "build.log" and has_offsets else (300 if has_offsets else 200)
        chunk = load_run_log_chunk(
            base_dir,
            env_name,
            run_id,
            name,
            file_offset=int(off.get("file", 0)),
            sqlite_offset=int(off.get("sqlite", 0)),
            limit=poll_limit,
        )
        if chunk.get("lines"):
            logs[name] = chunk

    manifest = _read_json_file(run_dir / "manifest.json") or {}
    steps = load_run_steps(base_dir, env_name, run_id)
    summary = load_run_summary(base_dir, env_name, run_id)
    progress = load_deploy_progress(
        base_dir,
        env_name,
        run_id,
        pipeline_status=str(summary.get("result") or ""),
        target_sha=str(summary.get("toSha") or ""),
        use_cache=False,
    )

    warnings: list[Any] = []
    warn_path = run_dir / "validate-warnings.json"
    warn_data = _read_json_file(warn_path)
    if isinstance(warn_data, list):
        warnings = warn_data

    failure = build_failure_payload(summary, steps, progress)
    if progress.get("lastError") and isinstance(progress["lastError"], dict):
        le = progress["lastError"]
        if not le.get("stepLabel") or le.get("stepLabel") == le.get("step"):
            sid = str(le.get("step") or "")
            le["stepLabel"] = STEP_LABELS.get(sid, le.get("stepLabel") or sid)

    return {
        "environment": env_name,
        "runId": run_id,
        "found": True,
        "manifest": manifest if isinstance(manifest, dict) else {},
        "steps": steps,
        "summary": summary,
        "warnings": warnings,
        "lastError": progress.get("lastError"),
        "currentStep": progress.get("currentStep"),
        "logs": logs,
        "failure": failure,
        "progressPct": compute_deploy_progress_pct(steps),
    }


def build_run_logs_zip(base_dir: Path, env_name: str, run_id: str) -> bytes | None:
    run_dir = base_dir / "deploy" / env_name / "logs" / "runs" / run_id
    if not run_dir.is_dir():
        return None
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in (*RUN_LOG_NAMES, "steps.json", "run-summary.json", "manifest.json"):
            path = run_dir / name
            if path.is_file():
                zf.write(path, arcname=name)
    return buffer.getvalue()


_CONSOLE_UPDATE_LOCK_TTL_SEC = 120


def resolve_ops_repo_dir(config: dict[str, Any]) -> Path:
    base_dir = get_base_dir(config)
    installed = base_dir / "ops"
    if (installed / "lib" / "paths.ps1").is_file():
        return installed
    checkout = Path(__file__).resolve().parent.parent
    if (checkout / "lib" / "paths.ps1").is_file():
        return checkout
    return installed


def _console_update_script(config: dict[str, Any]) -> Path:
    return resolve_ops_repo_dir(config) / "update_ops_console.ps1"


def _console_update_lock_path(config: dict[str, Any]) -> Path:
    return get_base_dir(config) / "logs" / "console-update.lock"


def _read_console_update_lock(config: dict[str, Any]) -> dict[str, Any] | None:
    path = _console_update_lock_path(config)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"active": True, "reason": "lock_corrupt"}
    if not isinstance(payload, dict):
        return {"active": True, "reason": "lock_invalid"}
    started = float(payload.get("startedAt") or 0)
    if started and (time.time() - started) > _CONSOLE_UPDATE_LOCK_TTL_SEC:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    payload["active"] = True
    return payload


def _write_console_update_lock(config: dict[str, Any], detail: str = "") -> None:
    path = _console_update_lock_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"startedAt": time.time(), "detail": detail}
    path.write_text(json.dumps(payload), encoding="utf-8")


def _clear_console_update_lock(config: dict[str, Any]) -> None:
    try:
        _console_update_lock_path(config).unlink(missing_ok=True)
    except OSError:
        pass


def _git_in_repo(repo_dir: Path, args: list[str], *, timeout: int = 15) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode == 0:
            return (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None


_GIT_WORKTREE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_GIT_WORKTREE_CACHE_TTL_SEC = 30
_GIT_WORKTREE_LOCATION_LABELS = {
    "mirror": "mirror Git",
    "release": "release ativa",
    "workspace": "checkout workspace",
}


def resolve_current_release_dir(base_dir: Path, env_name: str) -> Path | None:
    current = base_dir / "deploy" / env_name / "current"
    if not current.exists():
        return None
    try:
        resolved = current.resolve()
    except OSError:
        return None
    if not resolved.is_dir():
        return None
    return resolved


def _git_worktree_status(repo_dir: Path) -> dict[str, Any]:
    if not repo_dir.is_dir():
        return {"supported": False, "dirty": False, "path": str(repo_dir)}
    if not (repo_dir / ".git").exists():
        return {"supported": False, "dirty": False, "path": str(repo_dir)}
    output = _git_in_repo(repo_dir, ["status", "--porcelain"]) or ""
    lines = [line for line in output.splitlines() if line.strip()]
    return {
        "supported": True,
        "dirty": bool(lines),
        "changeCount": len(lines),
        "path": str(repo_dir),
    }


def read_env_git_worktree_status(
    base_dir: Path,
    env_name: str,
    *,
    repo_dir: Path | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    cache_key = env_name
    now = time.time()
    if use_cache:
        cached = _GIT_WORKTREE_CACHE.get(cache_key)
        if cached and (now - cached[0]) < _GIT_WORKTREE_CACHE_TTL_SEC:
            return cached[1]

    locations: list[dict[str, Any]] = []
    candidates: list[tuple[str, Path | None]] = [
        ("mirror", base_dir / "deploy" / env_name / "mirror"),
        ("release", resolve_current_release_dir(base_dir, env_name)),
        ("workspace", repo_dir),
    ]
    for loc_id, path in candidates:
        if not path:
            continue
        status = _git_worktree_status(path)
        status["id"] = loc_id
        status["label"] = _GIT_WORKTREE_LOCATION_LABELS.get(loc_id, loc_id)
        locations.append(status)

    dirty_locations = [loc for loc in locations if loc.get("dirty")]
    supported = any(loc.get("supported") for loc in locations)
    dirty = bool(dirty_locations)
    if dirty:
        labels = ", ".join(str(loc.get("label") or loc.get("id") or "") for loc in dirty_locations)
        reason = (
            f"Working tree com alteracoes locais ({labels}). "
            "Resolva manualmente antes de atualizar."
        )
    else:
        reason = ""

    payload = {
        "supported": supported,
        "dirty": dirty,
        "locations": locations,
        "reason": reason,
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }
    _GIT_WORKTREE_CACHE[cache_key] = (now, payload)
    return payload


def read_local_console_git_info(config: dict[str, Any]) -> dict[str, Any]:
    repo_dir = resolve_ops_repo_dir(config)
    if not (repo_dir / ".git").exists():
        return {
            "ok": False,
            "supported": False,
            "reason": f"Repositorio Git nao encontrado em {repo_dir}",
        }

    branch = _git_in_repo(repo_dir, ["rev-parse", "--abbrev-ref", "HEAD"]) or ""
    current_full = _git_in_repo(repo_dir, ["rev-parse", "HEAD"]) or ""
    current_short = _git_in_repo(repo_dir, ["rev-parse", "--short", "HEAD"]) or current_full[:7]
    dirty_output = _git_in_repo(repo_dir, ["status", "--porcelain"]) or ""
    return {
        "ok": True,
        "supported": True,
        "dirty": bool(dirty_output.strip()),
        "branch": branch,
        "currentSha": current_short,
        "currentShaFull": current_full,
        "repoDir": str(repo_dir),
    }


def _merge_console_update_payload(
    payload: dict[str, Any],
    local: dict[str, Any],
    *,
    in_progress: bool = False,
) -> dict[str, Any]:
    merged = {**local, **payload}
    for key in ("branch", "currentSha", "currentShaFull", "dirty", "supported", "repoDir"):
        if not merged.get(key) and local.get(key) is not None:
            merged[key] = local.get(key)
    if in_progress:
        merged["inProgress"] = True
    merged["checkedAt"] = datetime.now(timezone.utc).isoformat()
    return merged


def _parse_powershell_json(stdout: str) -> dict[str, Any]:
    text = (stdout or "").strip()
    if not text:
        return {"ok": False, "error": "Resposta vazia do script de atualizacao."}
    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    return {"ok": False, "error": "JSON invalido do script de atualizacao.", "stdout": text[-500:]}


def check_console_update(config: dict[str, Any]) -> dict[str, Any]:
    local = read_local_console_git_info(config)
    lock = _read_console_update_lock(config)
    if lock:
        return _merge_console_update_payload(
            {
                "ok": False,
                "supported": local.get("supported", True),
                "error": "Atualizacao do console ja em andamento.",
                "lockDetail": lock.get("detail") or "",
            },
            local,
            in_progress=True,
        )

    script = _console_update_script(config)
    if not script.is_file():
        return _merge_console_update_payload(
            {
                "ok": False,
                "supported": False,
                "reason": f"Script de atualizacao nao encontrado: {script}",
            },
            local,
        )

    repo_dir = resolve_ops_repo_dir(config)
    args = ["-CheckOnly", "-OpsRepoDir", str(repo_dir)]
    config_path = config.get("_configPath") or config.get("configPath")
    if config_path:
        args.extend(["-ConfigPath", str(config_path)])

    result = run_powershell(script, args, timeout=120)
    stdout = str(result.get("stdout") or "").strip()
    if stdout:
        payload = _parse_powershell_json(stdout)
    else:
        payload = {
            "ok": False,
            "supported": True,
            "reason": (result.get("error") or "Script de atualizacao nao retornou dados.")[:500],
        }
    if not payload.get("ok") and result.get("error"):
        payload.setdefault("error", result.get("error"))
    payload.setdefault("supported", bool(payload.get("supported", True)))
    return _merge_console_update_payload(payload, local)


def _spawn_detached_powershell(script: Path, args: list[str]) -> None:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        *args,
    ]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    subprocess.Popen(
        cmd,
        creationflags=creationflags,
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def apply_console_update(config: dict[str, Any]) -> dict[str, Any]:
    script = _console_update_script(config)
    if not script.is_file():
        return {
            "ok": False,
            "supported": False,
            "error": f"Script de atualizacao nao encontrado: {script}",
        }

    lock = _read_console_update_lock(config)
    if lock:
        return {
            "ok": False,
            "inProgress": True,
            "error": "Atualizacao do console ja em andamento.",
        }

    status = check_console_update(config)
    if not status.get("ok"):
        return status
    if not status.get("supported", True):
        return status
    if status.get("dirty"):
        return status
    if not status.get("updateAvailable"):
        return {
            "ok": True,
            "applied": False,
            "restarting": False,
            "message": "Console ja esta atualizado.",
            "currentSha": status.get("currentSha"),
            "remoteSha": status.get("remoteSha"),
        }

    previous_sha = str(status.get("currentSha") or "")
    target_sha = str(status.get("remoteSha") or "")

    try:
        _write_console_update_lock(config, detail=f"{previous_sha}->{target_sha}")
        repo_dir = resolve_ops_repo_dir(config)
        args = ["-Apply", "-OpsRepoDir", str(repo_dir)]
        config_path = config.get("_configPath") or config.get("configPath")
        if config_path:
            args.extend(["-ConfigPath", str(config_path)])
        _spawn_detached_powershell(script, args)
    except OSError as exc:
        _clear_console_update_lock(config)
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "accepted": True,
        "applied": True,
        "restarting": True,
        "previousSha": previous_sha,
        "targetSha": target_sha,
        "branch": status.get("branch"),
    }
