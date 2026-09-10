"""Persistent automation control plane for the Ops Console.

The HTTP server never imports Django or the bots.  Each run is delegated to a
detached supervisor using an immutable runtime bundle, so replacing a PPLID
release (or restarting this console) cannot replace code underneath a bot.
"""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENVIRONMENTS = ("MAIN", "DEV", "HOM")
PILOT_MODES = ("production", "rotina")
MODE_LABELS = {"production": "Produção (H/H)", "rotina": "Rotina diária"}
# Mirrors RobotProcessManager defaults in automation-native/automacoes/app/services/robot_manager.py
DEFAULT_PRODUCTION_CONFIG: dict[str, Any] = {
    "headless": False,
    "executar_imediatamente": False,
    "tempo_espera_minutos": 60,
    "dias_download_brflow": 3,
    "executar_confer": True,
    "executar_brflow": True,
    "baixar_monitor_com_producao": True,
    "baixar_log_eventos_com_producao": True,
    "executar_ged_irregularidade": True,
    "executar_produtividade_case": False,
}
DEFAULT_ROTINA_CONFIG: dict[str, Any] = {
    "headless": False,
    "executar_imediatamente": False,
    "rotina_data_inicio": "",
    "rotina_data_fim": "",
    "tarefas": [],
    "ged_execucao_imediata": "",
}
ROTINA_TASK_IDS = (
    "rotinas_1d",
    "rotinas_2d",
    "rotinas_3d",
    "produtividade_d1",
    "auditoria_replicados_d1",
    "auditoria_etapas",
    "irregularidade",
    "monitor_eventos",
    "confer_producao",
    "log_eventos",
    "prod_unificado",
    "monitor_unificado",
)
PRODUCTION_TEMPO_MIN = 5
PRODUCTION_TEMPO_MAX = 180
PRODUCTION_DIAS_MIN = 1
PRODUCTION_DIAS_MAX = 31
MAX_TARGET_ENVIRONMENTS = 2
STATUS_PRIORITY = {"error": 5, "interrupted": 4, "stopping": 3, "starting": 2, "running": 1, "idle": 0}
_VALIDATED_CREDENTIALS: dict[str, tuple[str, float]] = {}


def _default_bot_config(mode: str) -> dict[str, Any]:
    if mode == "production":
        return dict(DEFAULT_PRODUCTION_CONFIG)
    if mode == "rotina":
        return dict(DEFAULT_ROTINA_CONFIG)
    return {}


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def normalize_bot_config(mode: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    base = _default_bot_config(mode)
    if not isinstance(raw, dict):
        return base
    merged = {**base, **raw}
    merged["headless"] = _parse_bool(merged.get("headless"), bool(base.get("headless", False)))
    if mode == "production":
        merged["executar_imediatamente"] = _parse_bool(merged.get("executar_imediatamente"), False)
        merged["tempo_espera_minutos"] = _bounded_int(
            merged.get("tempo_espera_minutos"), int(base["tempo_espera_minutos"]), PRODUCTION_TEMPO_MIN, PRODUCTION_TEMPO_MAX
        )
        merged["dias_download_brflow"] = _bounded_int(
            merged.get("dias_download_brflow"), int(base["dias_download_brflow"]), PRODUCTION_DIAS_MIN, PRODUCTION_DIAS_MAX
        )
        for flag in (
            "executar_confer",
            "executar_brflow",
            "baixar_monitor_com_producao",
            "baixar_log_eventos_com_producao",
            "executar_ged_irregularidade",
            "executar_produtividade_case",
        ):
            merged[flag] = _parse_bool(merged.get(flag), bool(base.get(flag, False)))
        return {key: merged[key] for key in DEFAULT_PRODUCTION_CONFIG}
    if mode == "rotina":
        merged["executar_imediatamente"] = _parse_bool(merged.get("executar_imediatamente"), False)
        merged["rotina_data_inicio"] = str(merged.get("rotina_data_inicio") or "").strip()
        merged["rotina_data_fim"] = str(merged.get("rotina_data_fim") or "").strip()
        ged = str(merged.get("ged_execucao_imediata") or "").strip().lower()
        merged["ged_execucao_imediata"] = ged if ged in ("diurno", "noturno") else ""
        tarefas_raw = merged.get("tarefas")
        if isinstance(tarefas_raw, str):
            tarefas_raw = [tarefas_raw]
        if not isinstance(tarefas_raw, list):
            tarefas_raw = []
        allowed = set(ROTINA_TASK_IDS)
        tarefas = []
        for item in tarefas_raw:
            key = str(item or "").strip().lower()
            if key in allowed and key not in tarefas:
                tarefas.append(key)
        merged["tarefas"] = tarefas
        return {key: merged[key] for key in DEFAULT_ROTINA_CONFIG}
    return base


def _portal_robot_config_path() -> Path | None:
    override = os.environ.get("ROBOT_CONFIG_DIR", "").strip()
    if override:
        return Path(override) / "robot_config.json"
    appdata = os.environ.get("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "PLAN_IDF_SERASA_BOTS" / "config" / "robot_config.json"
    return None


def _import_portal_config(mode: str) -> dict[str, Any]:
    path = _portal_robot_config_path()
    if not path or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    section = payload.get(mode)
    return section if isinstance(section, dict) else {}


def _bots_path(config: dict[str, Any]) -> Path:
    return runtime_root(config) / "bots.json"


def _database_profiles_path(config: dict[str, Any]) -> Path:
    return runtime_root(config) / "database-profiles.json"


def list_bots(config: dict[str, Any]) -> list[dict[str, Any]]:
    stored = _read_json(_bots_path(config), {}).get("bots")
    stored = stored if isinstance(stored, list) else []
    by_id = {str(item.get("id")): item for item in stored if isinstance(item, dict) and item.get("id")}
    for mode in PILOT_MODES:
        by_id.setdefault(mode, {"id": mode, "name": MODE_LABELS[mode], "type": mode, "enabled": True, "legacyMode": mode})
    return list(by_id.values())


def save_bot(config: dict[str, Any], body: dict[str, Any], bot_id: str | None = None) -> dict[str, Any]:
    bot_id = str(bot_id or body.get("id") or "").strip().lower()
    if not bot_id or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in bot_id):
        raise ValueError("Identificador de bot invÃ¡lido")
    bots = list_bots(config)
    current = next((item for item in bots if item.get("id") == bot_id), {"id": bot_id})
    for key in ("name", "type", "description", "enabled", "legacyMode"):
        if key in body:
            current[key] = body[key]
    current["name"] = str(current.get("name") or bot_id)[:120]
    bots = [item for item in bots if item.get("id") != bot_id] + [current]
    _atomic_json(_bots_path(config), {"bots": bots, "updatedAt": _now()})
    return current


def delete_bot(config: dict[str, Any], bot_id: str) -> None:
    if bot_id in PILOT_MODES and mode_status(config, bot_id).get("running"):
        raise ValueError("Pare o bot antes de removÃª-lo")
    _atomic_json(_bots_path(config), {"bots": [item for item in list_bots(config) if item.get("id") != bot_id], "updatedAt": _now()})


def list_database_profiles(config: dict[str, Any]) -> list[dict[str, Any]]:
    stored = _read_json(_database_profiles_path(config), {}).get("profiles")
    profiles = stored if isinstance(stored, list) else []
    by_env = {str(item.get("environment")): item for item in profiles if isinstance(item, dict) and item.get("environment")}
    for env_name in ENVIRONMENTS:
        env = config.get(env_name) or {}
        by_env.setdefault(env_name, {"environment": env_name, "name": env_name, "database": env.get("postgresDb"), "enabled": env.get("enabled", True) is not False})
    return list(by_env.values())


def save_database_profile(config: dict[str, Any], environment: str, body: dict[str, Any]) -> dict[str, Any]:
    environment = str(environment or "").upper()
    if environment not in ENVIRONMENTS:
        raise ValueError("Ambiente invÃ¡lido")
    profiles = list_database_profiles(config)
    current = next(item for item in profiles if item.get("environment") == environment)
    for key in ("name", "host", "port", "database", "schema", "enabled", "secretRef"):
        if key in body:
            current[key] = body[key]
    _atomic_json(_database_profiles_path(config), {"profiles": profiles, "updatedAt": _now()})
    return current


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_dir(config: dict[str, Any]) -> Path:
    return Path(config.get("logDir") or "C:/PPLID/logs").parent


def _deploy_dir(config: dict[str, Any]) -> Path:
    return Path(config.get("deployDir") or (_base_dir(config) / "deploy"))


def runtime_root(config: dict[str, Any]) -> Path:
    override = ((config.get("automationRuntime") or {}).get("root") or "").strip()
    return Path(override) if override else _base_dir(config) / "ops" / "data" / "automation-runtime"


def _settings_path(config: dict[str, Any]) -> Path:
    return runtime_root(config) / "settings.json"


def _instance_key(mode: str, environment: str) -> str:
    return f"{mode}__{environment}"


def _state_path(config: dict[str, Any], mode: str, environment: str | None = None) -> Path:
    if environment:
        return runtime_root(config) / "state" / f"{_instance_key(mode, environment)}.json"
    return runtime_root(config) / "state" / f"{mode}.json"


def _command_path(config: dict[str, Any], mode: str, environment: str | None = None) -> Path:
    if environment:
        return runtime_root(config) / "commands" / f"{_instance_key(mode, environment)}.json"
    return runtime_root(config) / "commands" / f"{mode}.json"


def _stop_path(config: dict[str, Any], mode: str, environment: str | None = None) -> Path:
    if environment:
        return runtime_root(config) / "commands" / f"{_instance_key(mode, environment)}.stop"
    return runtime_root(config) / "commands" / f"{mode}.stop"


def _log_path(config: dict[str, Any], mode: str, environment: str | None = None) -> Path:
    if environment:
        return runtime_root(config) / "logs" / f"{_instance_key(mode, environment)}.log"
    return runtime_root(config) / "logs" / f"{mode}.log"


def _collect_instance_state_paths(config: dict[str, Any], mode: str) -> list[Path]:
    state_dir = runtime_root(config) / "state"
    paths: list[Path] = []
    legacy = state_dir / f"{mode}.json"
    if legacy.is_file():
        paths.append(legacy)
    if state_dir.is_dir():
        paths.extend(sorted(state_dir.glob(f"{mode}__*.json")))
    return paths


def _environment_from_state_path(path: Path, mode: str, state: dict[str, Any]) -> str | None:
    env = str(state.get("targetEnvironment") or "").strip().upper()
    if env in ENVIRONMENTS:
        return env
    stem = path.stem
    if stem.startswith(f"{mode}__"):
        candidate = stem.split("__", 1)[1].upper()
        if candidate in ENVIRONMENTS:
            return candidate
    return None


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            Path(tmp_name).unlink(missing_ok=True)
        except OSError:
            pass


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else dict(default or {})
    except (OSError, json.JSONDecodeError, TypeError):
        return dict(default or {})


def _default_settings() -> dict[str, Any]:
    return {
        "targetEnvironment": "MAIN",
        "targetEnvironments": ["MAIN"],
        "activeBundle": None,
        "previousBundle": None,
        "updatedAt": None,
        "updatedBy": None,
    }


def _normalize_settings(value: dict[str, Any]) -> dict[str, Any]:
    targets_raw = value.get("targetEnvironments")
    targets: list[str] = []
    if isinstance(targets_raw, list):
        for item in targets_raw:
            env = str(item or "").strip().upper()
            if env in ENVIRONMENTS and env not in targets:
                targets.append(env)
    if not targets:
        primary = str(value.get("targetEnvironment") or "MAIN").strip().upper()
        if primary not in ENVIRONMENTS:
            primary = "MAIN"
        targets = [primary]
    value["targetEnvironments"] = targets[:MAX_TARGET_ENVIRONMENTS]
    value["targetEnvironment"] = value["targetEnvironments"][0]
    return value


def get_settings(config: dict[str, Any]) -> dict[str, Any]:
    value = _default_settings()
    value.update(_read_json(_settings_path(config)))
    return _normalize_settings(value)


def probe_target_availability(config: dict[str, Any], environment: str) -> dict[str, Any]:
    env = str(environment or "").strip().upper()
    expected_database = str((config.get(env) or {}).get("postgresDb") or f"pplid_{env.lower()}")
    result: dict[str, Any] = {
        "environment": env,
        "database": expected_database,
        "available": False,
        "reason": "",
    }
    if env not in ENVIRONMENTS:
        result["reason"] = "ambiente inválido"
        return result
    if config.get(env, {}).get("enabled", True) is False:
        result["reason"] = "ambiente desativado"
        return result
    backend_env_file = _deploy_dir(config) / env / "shared" / "backend.env"
    if not backend_env_file.is_file():
        result["reason"] = "backend.env ausente"
        return result
    try:
        _validate_target_database(backend_env_file, expected_database)
        result["available"] = True
    except ValueError as exc:
        result["reason"] = str(exc)
    return result


def list_target_availability(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {env: probe_target_availability(config, env) for env in ENVIRONMENTS}


def update_settings(config: dict[str, Any], body: dict[str, Any], username: str) -> dict[str, Any]:
    if _any_running(config):
        raise ValueError("Pare os bots antes de alterar o banco de destino")
    raw_targets = body.get("targetEnvironments")
    if raw_targets is None and body.get("targetEnvironment"):
        raw_targets = [body.get("targetEnvironment")]
    if not isinstance(raw_targets, list):
        raise ValueError("targetEnvironments deve ser uma lista")
    targets: list[str] = []
    for item in raw_targets:
        env = str(item or "").strip().upper()
        if env not in ENVIRONMENTS:
            raise ValueError(f"Ambiente de destino inválido: {env}")
        if env in targets:
            continue
        targets.append(env)
    if not targets:
        raise ValueError("Selecione ao menos um banco de destino")
    if len(targets) > MAX_TARGET_ENVIRONMENTS:
        raise ValueError(f"Selecione no máximo {MAX_TARGET_ENVIRONMENTS} bancos de destino")
    for env in targets:
        probe = probe_target_availability(config, env)
        if not probe["available"]:
            raise ValueError(f"{env} indisponível: {probe['reason']}")
    settings = get_settings(config)
    settings.update(
        {
            "targetEnvironments": targets,
            "targetEnvironment": targets[0],
            "updatedAt": _now(),
            "updatedBy": username,
        }
    )
    _atomic_json(_settings_path(config), settings)
    return settings


def _pid_alive(pid: Any) -> bool:
    try:
        number = int(pid)
    except (TypeError, ValueError):
        return False
    if number <= 0:
        return False
    try:
        import psutil

        return psutil.pid_exists(number) and psutil.Process(number).is_running()
    except Exception:
        try:
            os.kill(number, 0)
            return True
        except OSError:
            return False


def _find_global_runner(mode: str) -> int | None:
    """Find a legacy or Ops runner without exposing its command line in APIs."""
    try:
        import psutil

        marker = "app.orchestration.robot_runner"
        for process in psutil.process_iter(["pid", "cmdline"]):
            try:
                args = [str(item) for item in (process.info.get("cmdline") or [])]
                lowered = [item.lower() for item in args]
                joined = " ".join(lowered)
                if marker not in joined:
                    continue
                for index, item in enumerate(lowered[:-1]):
                    if item == "--mode" and lowered[index + 1] == mode:
                        return int(process.info["pid"])
            except (psutil.AccessDenied, psutil.NoSuchProcess, ValueError, TypeError):
                continue
    except Exception:
        return None
    return None


def _refresh_instance_state(config: dict[str, Any], mode: str, path: Path, state: dict[str, Any]) -> dict[str, Any]:
    supervisor_alive = _pid_alive(state.get("supervisorPid"))
    runner_alive = _pid_alive(state.get("runnerPid"))
    status = str(state.get("status") or "idle")
    running = supervisor_alive and (status == "starting" or runner_alive)
    if status in {"starting", "running", "stopping"} and not running:
        state.update({"status": "interrupted", "running": False, "endedAt": state.get("endedAt") or _now()})
        status = "interrupted"
        running = False
    environment = _environment_from_state_path(path, mode, state)
    database = state.get("database") or (config.get(environment or "", {}) or {}).get("postgresDb")
    payload = {
        **state,
        "mode": mode,
        "environment": environment,
        "targetEnvironment": environment,
        "status": status,
        "running": running,
        "database": database,
        "supervisorPid": state.get("supervisorPid"),
        "runnerPid": state.get("runnerPid"),
        "error": state.get("error"),
        "logPath": str(_log_path(config, mode, environment) if environment else _log_path(config, mode)),
    }
    if status == "interrupted":
        _atomic_json(path, state)
    return payload


def _aggregate_status(statuses: list[str]) -> str:
    if not statuses:
        return "idle"
    return max(statuses, key=lambda item: STATUS_PRIORITY.get(item, 0))


def mode_status(config: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode not in PILOT_MODES:
        raise ValueError("Modo não suportado")
    settings = get_settings(config)
    configured_targets = list(settings.get("targetEnvironments") or [settings["targetEnvironment"]])
    instances: list[dict[str, Any]] = []
    any_ops_running = False
    for path in _collect_instance_state_paths(config, mode):
        state = _read_json(path, {"mode": mode, "status": "idle"})
        instance = _refresh_instance_state(config, mode, path, state)
        instances.append(instance)
        if instance.get("running"):
            any_ops_running = True

    if not any_ops_running:
        global_runner = _find_global_runner(mode)
        if global_runner:
            legacy = _read_json(_state_path(config, mode), {"mode": mode, "status": "idle"})
            owned = str(legacy.get("runnerPid") or "") == str(global_runner)
            return {
                **legacy,
                "mode": mode,
                "label": MODE_LABELS[mode],
                "status": "running",
                "running": True,
                "controller": "ops" if owned else "portal",
                "runnerPid": global_runner,
                "supervisorPid": legacy.get("supervisorPid") if owned else None,
                "targetEnvironment": legacy.get("targetEnvironment") if owned else None,
                "targetEnvironments": configured_targets,
                "instances": instances,
                "bundle": legacy.get("bundle") if owned else None,
                "logPath": str(_log_path(config, mode, configured_targets[0])),
            }

    running = any(instance.get("running") for instance in instances)
    statuses = [str(instance.get("status") or "idle") for instance in instances]
    agg_status = _aggregate_status(statuses)
    active_targets = [
        str(instance.get("environment"))
        for instance in instances
        if instance.get("running") and instance.get("environment") in ENVIRONMENTS
    ]
    target_environments = active_targets or configured_targets
    primary = next((item for item in instances if item.get("running")), instances[0] if instances else None)
    errors = [
        instance
        for instance in instances
        if instance.get("error") and instance.get("status") in {"error", "interrupted"}
    ]
    error_message = "; ".join(
        f"{item.get('environment')}: {item.get('error')}"
        for item in errors
        if item.get("environment") and item.get("error")
    )
    return {
        "mode": mode,
        "label": MODE_LABELS[mode],
        "status": agg_status,
        "running": running,
        "controller": "ops" if any_ops_running else "idle",
        "targetEnvironment": (primary or {}).get("environment") or configured_targets[0],
        "targetEnvironments": target_environments,
        "instances": instances,
        "runnerPid": (primary or {}).get("runnerPid"),
        "supervisorPid": (primary or {}).get("supervisorPid"),
        "error": error_message or None,
        "logPath": str(_log_path(config, mode, configured_targets[0])),
    }


def overview(config: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings(config)
    bot_states = {mode: mode_status(config, mode) for mode in PILOT_MODES}
    bundles = []
    bundles_dir = runtime_root(config) / "bundles"
    if bundles_dir.is_dir():
        for item in bundles_dir.iterdir():
            meta = _read_json(item / "bundle.json") if item.is_dir() else {}
            if meta:
                bundles.append(meta)
    bundles.sort(key=lambda item: str(item.get("publishedAt") or ""), reverse=True)
    environment_info = {}
    target_availability = list_target_availability(config)
    for name in ENVIRONMENTS:
        release_meta = _read_json(_deploy_dir(config) / name / "current" / "meta.json")
        sha = release_meta.get("sha") or str(release_meta.get("shaFull") or "")[:12] or None
        source_root = Path(str(config.get("automationSourceDir") or ""))
        if not sha and (source_root / ".git").exists():
            try:
                result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(source_root), capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    sha = result.stdout.strip() or None
            except (OSError, subprocess.SubprocessError):
                pass
        probe = target_availability.get(name) or {}
        environment_info[name] = {
            "enabled": config.get(name, {}).get("enabled", True) is not False,
            "database": config.get(name, {}).get("postgresDb"),
            "sha": sha,
            "available": bool(probe.get("available")),
            "reason": probe.get("reason") or "",
        }
    return {
        "ok": True,
        "settings": settings,
        "bots": bot_states,
        "botsCatalog": list_bots(config),
        "databaseProfiles": list_database_profiles(config),
        "targetAvailability": target_availability,
        "summary": {
            "running": sum(1 for state in bot_states.values() if state.get("running")),
            "errors": sum(1 for state in bot_states.values() if state.get("status") in {"error", "interrupted"}),
            "total": len(list_bots(config)),
        },
        "configs": {mode: get_config(config, mode)["config"] for mode in PILOT_MODES},
        "bundles": bundles[:10],
        "environments": environment_info,
        "generatedAt": _now(),
    }


def _release_sha(current: Path) -> tuple[str, str]:
    meta = _read_json(current / "meta.json")
    full = str(meta.get("shaFull") or meta.get("sha") or "").strip()
    if not full and (current / ".git").exists():
        try:
            result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(current), capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                full = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    short = str(meta.get("sha") or full[:12]).strip()
    if not short:
        raise ValueError("A release atual não possui meta.json com SHA")
    return short, full or short


def _ignore_runtime_copy(_directory: str, names: list[str]) -> set[str]:
    ignored = {
        ".venv", "__pycache__", ".pytest_cache", ".git", "node_modules", "media", "staticfiles",
        ".env", ".env.local", ".ruff_cache", ".mypy_cache", "tmp", "temp",
    }
    return {name for name in names if name in ignored or name.startswith(".tmp") or name.endswith((".pyc", ".pyo", ".egg-info"))}


def _run_checked(args: list[str], *, cwd: Path | None = None, timeout: int = 1800) -> None:
    result = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        tail = "\n".join(((result.stdout or "") + "\n" + (result.stderr or "")).splitlines()[-20:])
        raise RuntimeError(tail or f"Comando falhou com código {result.returncode}")


def _any_running(config: dict[str, Any]) -> bool:
    return any(mode_status(config, mode).get("running") for mode in PILOT_MODES)


def publish_runtime(config: dict[str, Any], source_environment: str, username: str) -> dict[str, Any]:
    source = str(source_environment or "").strip().upper()
    if source not in ENVIRONMENTS:
        raise ValueError("Ambiente de origem inválido")
    if _any_running(config):
        raise ValueError("Pare Production e Rotina antes de publicar um runtime")

    root = runtime_root(config)
    current = _deploy_dir(config) / source / "current"
    if not current.is_dir():
        source_root = Path(str(config.get("automationSourceDir") or ""))
        if (source_root / "automacoes").is_dir() and (source_root / "backend").is_dir():
            current = source_root
    if not (current / "automacoes").is_dir() or not (current / "backend").is_dir():
        raise ValueError(f"Release atual de {source} não contém backend e automacoes")
    short_sha, full_sha = _release_sha(current)
    bundle_id = f"{source.lower()}-{short_sha}"
    final_dir = root / "bundles" / bundle_id
    staging = root / "staging" / f"{bundle_id}-{os.getpid()}-{int(time.time())}"
    if final_dir.exists():
        meta = _read_json(final_dir / "bundle.json")
    else:
        try:
            ops_bot_source = Path(str(config.get("automationOpsDir") or "")) / "automacoes"
            bot_source = ops_bot_source if ops_bot_source.is_dir() else current / "automacoes"
            shutil.copytree(bot_source, staging / "automacoes", ignore=_ignore_runtime_copy)
            shutil.copytree(current / "backend", staging / "backend", ignore=_ignore_runtime_copy)
            venv_dir = staging / ".venv"
            _run_checked([sys.executable, "-m", "venv", str(venv_dir)])
            python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            pip = venv_dir / ("Scripts/pip.exe" if os.name == "nt" else "bin/pip")
            _run_checked([str(pip), "install", "-r", str(staging / "backend" / "requirements.txt")])
            _run_checked([str(pip), "install", str(staging / "automacoes")])
            _run_checked(
                [str(python), "-c", "import app; import django; from app.services.robot_manager import RobotProcessManager"],
                cwd=staging / "automacoes",
            )
            meta = {
                "id": bundle_id,
                "sourceEnvironment": source,
                "sha": short_sha,
                "shaFull": full_sha,
                "publishedAt": _now(),
                "publishedBy": username,
            }
            _atomic_json(staging / "bundle.json", meta)
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final_dir)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    settings = get_settings(config)
    previous = settings.get("activeBundle")
    settings.update(
        {
            "activeBundle": meta,
            "previousBundle": previous if previous and previous.get("id") != bundle_id else settings.get("previousBundle"),
            "updatedAt": _now(),
            "updatedBy": username,
        }
    )
    _atomic_json(_settings_path(config), settings)
    return {"ok": True, "bundle": meta, "settings": settings}


def rollback_runtime(config: dict[str, Any], username: str) -> dict[str, Any]:
    if _any_running(config):
        raise ValueError("Pare Production e Rotina antes do rollback")
    settings = get_settings(config)
    previous = settings.get("previousBundle")
    active = settings.get("activeBundle")
    if not previous or not (runtime_root(config) / "bundles" / str(previous.get("id"))).is_dir():
        raise ValueError("Não existe bundle anterior disponível")
    settings.update({"activeBundle": previous, "previousBundle": active, "updatedAt": _now(), "updatedBy": username})
    _atomic_json(_settings_path(config), settings)
    return {"ok": True, "settings": settings}


def get_config(config: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode not in PILOT_MODES:
        raise ValueError("Modo não suportado")
    path = runtime_root(config) / "config" / f"{mode}.json"
    if path.is_file():
        stored = _read_json(path)
    else:
        stored = _import_portal_config(mode) or {}
    normalized = normalize_bot_config(mode, stored if stored else None)
    if path.is_file():
        if normalize_bot_config(mode, stored) != stored:
            _atomic_json(path, normalized)
    elif stored:
        _atomic_json(path, normalized)
    return {"ok": True, "mode": mode, "config": normalized}


def update_config(config: dict[str, Any], mode: str, body: dict[str, Any]) -> dict[str, Any]:
    if mode not in PILOT_MODES:
        raise ValueError("Modo não suportado")
    status = mode_status(config, mode)
    if status.get("controller") == "ops" and status.get("running"):
        raise ValueError("Pare o bot Ops antes de alterar a configuração")
    value = body.get("config")
    if not isinstance(value, dict):
        raise ValueError("config deve ser um objeto")
    forbidden = {key for key in value if any(token in key.lower() for token in ("password", "senha", "postgres", "database_url"))}
    if forbidden:
        raise ValueError("Configuração contém campos sensíveis ou de banco não permitidos")
    normalized = normalize_bot_config(mode, value)
    _atomic_json(runtime_root(config) / "config" / f"{mode}.json", normalized)
    return {"ok": True, "mode": mode, "config": normalized}


def _active_bundle(config: dict[str, Any]) -> tuple[dict[str, Any], Path]:
    bundle = get_settings(config).get("activeBundle")
    if not isinstance(bundle, dict) or not bundle.get("id"):
        root = runtime_root(config)
        native = root / "native-bundle"
        bot_source = Path(str(config.get("automationOpsDir") or "")) / "automacoes"
        native_root = Path(str(config.get("automationOpsDir") or ""))
        backend_source = native_root / "backend" if (native_root / "backend").is_dir() else Path(str(config.get("automationSourceDir") or "")) / "backend"
        if not bot_source.is_dir() or not backend_source.is_dir():
            raise ValueError("Código nativo dos bots não está instalado no Ops")
        if not (native / "automacoes").is_dir() or not (native / "backend").is_dir():
            native.mkdir(parents=True, exist_ok=True)
            shutil.copytree(bot_source, native / "automacoes", ignore=_ignore_runtime_copy, dirs_exist_ok=True)
            shutil.copytree(backend_source, native / "backend", ignore=_ignore_runtime_copy, dirs_exist_ok=True)
        bundle = {"id": "ops-native", "sourceEnvironment": "OPS", "sha": "internal", "publishedAt": _now()}
        settings = get_settings(config)
        settings.update({"activeBundle": bundle, "updatedAt": _now(), "updatedBy": "ops"})
        _atomic_json(_settings_path(config), settings)
    path = runtime_root(config) / "bundles" / str(bundle["id"])
    if bundle.get("id") == "ops-native":
        path = runtime_root(config) / "native-bundle"
    if not path.is_dir():
        raise ValueError("Bundle ativo não foi encontrado em disco")
    return bundle, path


def _bundle_python(bundle_dir: Path) -> Path:
    candidate = bundle_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return candidate if candidate.is_file() else Path(sys.executable)


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _validate_target_database(env_file: Path, expected_database: str) -> None:
    values = _load_env_file(env_file)
    actual = values.get("POSTGRES_DB", "")
    if actual != expected_database:
        raise ValueError(f"Banco configurado '{actual}' difere do destino esperado '{expected_database}'")
    try:
        import psycopg
        with psycopg.connect(host=values.get("POSTGRES_HOST", "localhost"), port=int(values.get("POSTGRES_PORT", "5432")), dbname=actual, user=values.get("POSTGRES_USER", "postgres"), password=values.get("POSTGRES_PASSWORD", ""), connect_timeout=4) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database()")
                resolved = str(cursor.fetchone()[0])
        if resolved != expected_database:
            raise ValueError(f"Conexao abriu o banco '{resolved}', esperado '{expected_database}'")
    except ValueError:
        raise
    except Exception as exc:
        reason = str(exc).splitlines()[0]
        raise ValueError(f"Banco {expected_database} indisponivel: {reason}") from exc


def _credential_digest(matricula: str, senha: str) -> str:
    return hashlib.sha256(f"{matricula}\0{senha}".encode("utf-8", errors="ignore")).hexdigest()


def validate_credentials(config: dict[str, Any], body: dict[str, Any], username: str) -> dict[str, Any]:
    matricula = str(body.get("matricula") or "").strip()
    senha = str(body.get("senha") or "")
    if not matricula or not senha:
        raise ValueError("Matrícula e senha são obrigatórias")
    _bundle, bundle_dir = _active_bundle(config)
    python = _bundle_python(bundle_dir)
    helper = Path(__file__).with_name("automation_credentials.py")
    env = os.environ.copy()
    env.update({"OPS_BOT_USER": matricula, "OPS_BOT_PASSWORD": senha, "PYTHONUTF8": "1"})
    try:
        result = subprocess.run(
            [str(python), str(helper), "--bundle", str(bundle_dir)],
            cwd=str(bundle_dir / "automacoes"), env=env, capture_output=True, text=True, timeout=210,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Validação Okta excedeu o tempo limite") from exc
    finally:
        env.pop("OPS_BOT_PASSWORD", None)
        env.pop("OPS_BOT_USER", None)
    payload: dict[str, Any] = {}
    for line in reversed((result.stdout or "").splitlines()):
        try:
            candidate = json.loads(line)
            if isinstance(candidate, dict) and "ok" in candidate:
                payload = candidate
                break
        except json.JSONDecodeError:
            continue
    if result.returncode or not payload.get("ok"):
        message = str(payload.get("message") or "Falha na validação das credenciais no Okta")
        raise ValueError(message)
    _VALIDATED_CREDENTIALS["global"] = (_credential_digest(matricula, senha), time.time() + 15 * 60)
    return {"ok": True, "message": str(payload.get("message") or "Credenciais validadas"), "expiresInSeconds": 900}


def _credentials_are_valid(matricula: str, senha: str) -> bool:
    saved = _VALIDATED_CREDENTIALS.get("global")
    if not saved:
        return False
    digest, expires_at = saved
    if time.time() >= expires_at:
        _VALIDATED_CREDENTIALS.pop("global", None)
        return False
    return digest == _credential_digest(matricula, senha)


def credentials_status() -> dict[str, Any]:
    saved = _VALIDATED_CREDENTIALS.get("global")
    if not saved or time.time() >= saved[1]:
        _VALIDATED_CREDENTIALS.pop("global", None)
        return {"validated": False, "expiresInSeconds": 0}
    return {"validated": True, "expiresInSeconds": max(0, int(saved[1] - time.time()))}


def _spawn_bot_instance(
    config: dict[str, Any],
    mode: str,
    target: str,
    bundle: dict[str, Any],
    bundle_dir: Path,
    python: Path,
    matricula: str,
    senha: str,
    username: str,
) -> dict[str, Any]:
    backend_env_file = _deploy_dir(config) / target / "shared" / "backend.env"
    expected_database = str(config.get(target, {}).get("postgresDb") or f"pplid_{target.lower()}")
    _validate_target_database(backend_env_file, expected_database)
    command_payload = {
        "mode": mode,
        "targetEnvironment": target,
        "bundle": bundle,
        "config": get_config(config, mode)["config"],
        "requestedBy": username,
        "requestedAt": _now(),
    }
    command_file = _command_path(config, mode, target)
    _atomic_json(command_file, command_payload)
    _stop_path(config, mode, target).unlink(missing_ok=True)
    env = os.environ.copy()
    env.update({"OPS_BOT_USER": matricula, "OPS_BOT_PASSWORD": senha, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
    supervisor = Path(__file__).with_name("automation_supervisor.py")
    args = [
        str(python), str(supervisor), "--pplid-supervised", "--mode", mode,
        "--environment", target, "--bundle", str(bundle_dir), "--runtime-root", str(runtime_root(config)),
        "--command-file", str(command_file),
        "--backend-env-file", str(backend_env_file),
        "--expected-database", expected_database,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt":
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) | 0x00000008 | 0x00004000
    log_path = _log_path(config, mode, target)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log_handle:
        proc = subprocess.Popen(
            args, cwd=str(bundle_dir / "automacoes"), env=env,
            stdin=subprocess.DEVNULL, stdout=log_handle, stderr=subprocess.STDOUT,
            creationflags=creationflags, start_new_session=(os.name != "nt"), close_fds=(os.name != "nt"),
        )
    state = _read_json(_state_path(config, mode, target))
    if not state:
        state = {
            **command_payload,
            "controller": "ops",
            "status": "starting",
            "running": True,
            "runnerPid": None,
            "startedAt": _now(),
            "endedAt": None,
            "database": expected_database,
        }
    state["supervisorPid"] = proc.pid
    state["targetEnvironment"] = target
    state["database"] = expected_database
    _atomic_json(_state_path(config, mode, target), state)
    return state


def start_bot(config: dict[str, Any], mode: str, body: dict[str, Any], username: str) -> dict[str, Any]:
    if mode not in PILOT_MODES:
        raise ValueError("Modo não suportado nesta fase")
    current = mode_status(config, mode)
    if current.get("running"):
        raise ValueError(f"{MODE_LABELS[mode]} já está em execução")
    matricula = str(body.get("matricula") or "").strip()
    senha = str(body.get("senha") or "")
    if not matricula or not senha:
        raise ValueError("Matrícula e senha são obrigatórias")
    if not _credentials_are_valid(matricula, senha):
        raise ValueError("Valide as credenciais globais no Okta antes de iniciar")

    settings = get_settings(config)
    targets = list(settings.get("targetEnvironments") or [settings["targetEnvironment"]])
    for target in targets:
        probe = probe_target_availability(config, target)
        if not probe["available"]:
            raise ValueError(f"{target} indisponível: {probe['reason']}")

    bundle, bundle_dir = _active_bundle(config)
    python = _bundle_python(bundle_dir)
    spawned: list[dict[str, Any]] = []
    try:
        for target in targets:
            spawned.append(
                _spawn_bot_instance(config, mode, target, bundle, bundle_dir, python, matricula, senha, username)
            )
    except Exception:
        for instance in spawned:
            env = instance.get("targetEnvironment")
            if env:
                _stop_path(config, mode, env).touch()
        raise

    bot = mode_status(config, mode)
    env_label = " · ".join(targets)
    return {"ok": True, "message": f"{MODE_LABELS[mode]} iniciado em {env_label}", "bot": bot, "instances": spawned}


def stop_bot(config: dict[str, Any], mode: str) -> dict[str, Any]:
    state = mode_status(config, mode)
    if not state.get("running"):
        raise ValueError(f"{MODE_LABELS[mode]} não está em execução")
    touched = False
    for instance in state.get("instances") or []:
        if not instance.get("running"):
            continue
        environment = instance.get("environment")
        if environment not in ENVIRONMENTS:
            continue
        stop_file = _stop_path(config, mode, environment)
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.touch()
        inst_state = _read_json(_state_path(config, mode, environment))
        inst_state["status"] = "stopping"
        _atomic_json(_state_path(config, mode, environment), inst_state)
        touched = True
    legacy_stop = _stop_path(config, mode)
    if legacy_stop.parent.exists() and _state_path(config, mode).is_file():
        legacy_stop.touch()
        touched = True
    if not touched:
        raise ValueError(f"{MODE_LABELS[mode]} não está em execução")
    return {"ok": True, "message": "Parada solicitada", "bot": mode_status(config, mode)}


def read_logs(config: dict[str, Any], mode: str, tail: int = 120) -> dict[str, Any]:
    if mode not in PILOT_MODES:
        raise ValueError("Modo não suportado")
    count = max(1, min(int(tail), 500))
    settings = get_settings(config)
    targets = list(settings.get("targetEnvironments") or [settings["targetEnvironment"]])
    merged: list[str] = []
    per_target = max(1, count // max(len(targets), 1))
    for environment in targets:
        path = _log_path(config, mode, environment)
        try:
            chunk = path.read_text(encoding="utf-8", errors="replace").splitlines()[-per_target:]
            merged.extend([f"[{environment}] {line}" for line in chunk])
        except OSError:
            continue
    legacy_path = _log_path(config, mode)
    if legacy_path.is_file():
        try:
            chunk = legacy_path.read_text(encoding="utf-8", errors="replace").splitlines()[-per_target:]
            merged.extend([f"[legacy] {line}" for line in chunk])
        except OSError:
            pass
    return {"ok": True, "mode": mode, "lines": merged[-count:]}


def clear_logs(config: dict[str, Any], mode: str) -> dict[str, Any]:
    if mode_status(config, mode).get("running"):
        raise ValueError("Não é possível limpar o log durante a execução")
    settings = get_settings(config)
    targets = list(settings.get("targetEnvironments") or [settings["targetEnvironment"]])
    for environment in targets:
        path = _log_path(config, mode, environment)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    legacy_path = _log_path(config, mode)
    if legacy_path.is_file():
        legacy_path.write_text("", encoding="utf-8")
    return {"ok": True, "mode": mode}
