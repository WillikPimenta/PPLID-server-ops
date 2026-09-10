"""Detached supervisor for a single Ops-owned bot run."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pplid-supervised", action="store_true")
    parser.add_argument("--mode", required=True, choices=("production", "rotina"))
    parser.add_argument("--environment", required=True, choices=("MAIN", "DEV", "HOM"))
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--runtime-root", required=True)
    parser.add_argument("--command-file", required=True)
    parser.add_argument("--backend-env-file", required=True)
    parser.add_argument("--expected-database", required=True)
    args = parser.parse_args()

    bundle = Path(args.bundle).resolve()
    runtime = Path(args.runtime_root).resolve()
    instance_key = f"{args.mode}__{args.environment}"
    state_path = runtime / "state" / f"{instance_key}.json"
    stop_path = runtime / "commands" / f"{instance_key}.stop"
    stop_path.unlink(missing_ok=True)
    command = read_json(Path(args.command_file))
    state = {
        **command,
        "controller": "ops",
        "status": "starting",
        "running": True,
        "supervisorPid": os.getpid(),
        "runnerPid": None,
        "startedAt": now(),
        "endedAt": None,
        "error": None,
    }
    atomic_json(state_path, state)

    automation_dir = bundle / "automacoes"
    backend_dir = bundle / "backend"
    sys.path[:0] = [str(automation_dir), str(backend_dir)]
    os.environ.update(
        {
            "PPLID_ENVIRONMENT": args.environment,
            "PPLID_ENV_PROFILE": args.environment.lower(),
            "PPLID_BACKEND_DIR": str(backend_dir),
            "PPLID_BACKEND_ENV_FILE": str(Path(args.backend_env_file).resolve()),
            "DJANGO_SETTINGS_MODULE": "config.settings",
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "STATUS_TIMEOUT_ENABLED": "false",
            "DAILY_STATUS_RESET_ENABLED": "false",
            "QUALIDADE_DASHBOARD_WARM_ENABLED": "false",
            "QUALIDADE_PROJECTION_ASYNC_ENABLED": "false",
        }
    )
    os.chdir(automation_dir)

    try:
        import django

        django.setup()
        from django.conf import settings as django_settings
        from django.db import connection

        expected_db = args.expected_database
        actual_db = str(django_settings.DATABASES["default"].get("NAME") or "")
        if actual_db != expected_db:
            raise RuntimeError(f"Banco resolvido '{actual_db}' difere do esperado '{expected_db}'")
        connection.ensure_connection()

        from app.services.robot_manager import robot_manager

        robot_config = command.get("config") if isinstance(command.get("config"), dict) else {}
        ok, message = robot_manager.start(
            mode=args.mode,
            matricula=os.environ.pop("OPS_BOT_USER", ""),
            senha=os.environ.pop("OPS_BOT_PASSWORD", ""),
            executar_imediatamente=bool(robot_config.get("executar_imediatamente", False)),
            rotina_data_inicio=str(robot_config.get("rotina_data_inicio") or ""),
            rotina_data_fim=str(robot_config.get("rotina_data_fim") or ""),
            robot_config=robot_config,
            require_okta_validation=False,
        )
        if not ok:
            raise RuntimeError(message)

        status = robot_manager.status()[args.mode]
        state.update({"status": "running", "runnerPid": status.get("pid"), "message": message, "updatedAt": now()})
        atomic_json(state_path, state)
        print(f"[supervisor] {message}; env={args.environment}; runnerPid={state.get('runnerPid')}", flush=True)

        while robot_manager.status()[args.mode].get("running"):
            if stop_path.exists():
                robot_manager.stop(args.mode)
                stop_path.unlink(missing_ok=True)
                state["status"] = "stopping"
                atomic_json(state_path, state)
            time.sleep(1)

        final = robot_manager.status()[args.mode]
        result = (final.get("execution") or {}).get("result") or "finished"
        state.update({"status": result, "running": False, "endedAt": now(), "updatedAt": now()})
        atomic_json(state_path, state)
        return 0
    except Exception as exc:
        state.update({"status": "error", "running": False, "error": str(exc), "endedAt": now(), "updatedAt": now()})
        atomic_json(state_path, state)
        print(f"[supervisor] ERRO: {exc}", flush=True)
        return 1
    finally:
        os.environ.pop("OPS_BOT_PASSWORD", None)
        os.environ.pop("OPS_BOT_USER", None)


if __name__ == "__main__":
    raise SystemExit(main())
