from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import subprocess
import time
import re

from .config import Config
from .manager import Manager
from .store import atomic_json, environment_lock, utc, read_json


def pipeline(config, manager, sha, run_id):
    """Keep ONE Python-owned named mutex over build, validation and promotion."""
    if not sha:
        raise ValueError("A target SHA is required")
    config.release(sha)
    state = manager.read()
    if not state.get("active"):
        raise RuntimeError("Initial prepare/activate must be completed before automatic deployment")
    full = subprocess.check_output(["git", "-C", str(config.root / "mirror"), "rev-parse", "--verify", sha + "^{commit}"],
                                   text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).strip()
    if len(full) != 40 or not full.startswith(sha):
        raise ValueError("Target commit is not present in the environment mirror")
    # Rebuilding a running directory could change imports underneath its process.
    if any(s["shaFull"] == full for s in state["slots"].values()):
        return manager.promote(sha, run_id)
    manager.resources()
    run_id = run_id or "bg-" + str(int(time.time()))
    script = Path(__file__).parents[1] / "bluegreen_prepare.ps1"
    log = config.root / "logs/runs" / run_id / "bluegreen-prepare.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    started_at = utc()
    error = None
    result_state = None
    try:
        environment = dict(os.environ)
        if not getattr(config, 'machine_path', None):
            raise RuntimeError('Build requires an explicit loaded machine configuration')
        environment['OPS_MACHINE_CONFIG'] = str(config.machine_path)
        # Build with destination configuration. A dist compiled in DEV may
        # embed DEV API URLs and must not be blindly reused in HOM/MAIN.
        environment.pop("PPLID_PROMOTE_SOURCE", None)
        with log.open("ab") as stream:
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                                     "-Environment", config.environment, "-TargetSha", sha, "-TargetShaFull", full, "-RunId", run_id],
                                    stdout=stream, stderr=subprocess.STDOUT, env=environment,
                                    timeout=config.timeouts["commandSeconds"],
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(f"Build/validation failed; active version preserved. See {log}")
        result_state = manager.promote(sha, run_id)
        return result_state
    except Exception as failure:
        error = str(failure)
        raise
    finally:
        finished = utc()
        summary = {"environment": config.environment, "runId": run_id, "fromSha": state["slots"][state["active"]]["sha"],
                   "toSha": sha, "result": "failed" if error else "success", "startedAt": started_at,
                   "finishedAt": finished, "durationSec": round(time.monotonic() - started, 3),
                   "lastError": error, "trigger": "blue-green"}
        atomic_json(log.parent / "run-summary.json", summary)
        path = config.root / "deploy-state.json"
        legacy = read_json(path) if path.exists() else {}
        legacy.update(status="failed" if error else "idle", targetSha=sha, runId=run_id,
                      lastError=error, finishedAt=finished)
        atomic_json(path, legacy)
        steps_path = log.parent / "steps.json"
        if steps_path.exists() and not error:
            steps = json.loads(steps_path.read_text(encoding="utf-8-sig"))
            for step in steps:
                if step.get("status") in ("pending", "running"):
                    step.update(status="success", finishedAt=finished)
            atomic_json(steps_path, steps)


def main():
    parser = argparse.ArgumentParser(description="Opt-in blue-green operations; never installs services")
    parser.add_argument("action", choices=("prepare", "activate", "pipeline", "promote", "rollback", "recover", "status", "cancel", "shutdown", "workers-start", "workers-stop"))
    parser.add_argument("--machine-config", type=Path, default=os.environ.get("OPS_MACHINE_CONFIG"))
    parser.add_argument("--environment", choices=("MAIN", "DEV", "HOM"), required=True)
    parser.add_argument("--sha", default="")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    try:
        if not args.machine_config:
            raise ValueError("Pass an explicit --machine-config (or OPS_MACHINE_CONFIG)")
        config = Config.load(Path(args.machine_config), args.environment)
        if args.run_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.run_id):
            raise ValueError("Invalid run ID")
        manager = Manager(config)
        if args.action == "status":
            result = manager.read(missing=True)
        elif args.action == "cancel":
            # Cooperative request intentionally does not acquire the mutex
            # currently held by the deploy. Never taskkill a process tree.
            atomic_json(config.root / "cancel-requested.json", {"runId": args.run_id, "at": utc()})
            result = {"cancelRequested": True}
        else:
            with environment_lock(args.environment, config.root):
                if args.action == "prepare":
                    result = manager.prepare(args.sha)
                elif args.action == "activate":
                    result = manager.activate()
                elif args.action == "promote":
                    result = manager.promote(args.sha, args.run_id)
                elif args.action == "pipeline":
                    result = pipeline(config, manager, args.sha, args.run_id)
                elif args.action == "rollback":
                    result = manager.rollback(args.sha)
                elif args.action == "shutdown":
                    result = manager.shutdown()
                elif args.action == "workers-start":
                    result = manager.workers_start(args.sha)
                elif args.action == "workers-stop":
                    result = manager.workers_stop()
                else:
                    result = manager.recover()
        print(json.dumps({"ok": True, "state": result}, ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
