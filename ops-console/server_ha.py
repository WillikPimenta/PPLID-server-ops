"""Read-only HA summary for the local Ops Console."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _machine_candidates(base_dir: Path) -> list[Path]:
    candidates = []
    explicit = (os.environ.get("PPLID_MACHINE_CONFIG") or "").strip()
    if explicit:
        candidates.append(Path(explicit))
    candidates.append(base_dir / "machine.config.json")
    candidates.append(Path("C:/PPLID/machine.config.json"))
    candidates.append(Path(os.environ.get("ProgramData", "C:/ProgramData")) / "PPLID" / "machine.config.json")
    return candidates


def load_machine_config(base_dir: Path, provided: dict[str, Any] | None = None) -> dict[str, Any]:
    if provided is not None:
        return dict(provided)
    for path in _machine_candidates(base_dir):
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return {}


def _endpoint(value: str, default_port: int) -> tuple[str, int] | None:
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlparse(value if "://" in value else f"//{value}")
    if not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or default_port


def _probe(value: str, default_port: int) -> dict[str, Any]:
    target = _endpoint(value, default_port)
    if not target:
        return {"status": "not_configured", "reachable": False}
    host, port = target
    try:
        with socket.create_connection((host, port), timeout=0.75):
            return {"status": "reachable", "reachable": True, "host": host, "port": port}
    except OSError as exc:
        return {"status": "unreachable", "reachable": False, "host": host, "port": port, "error": str(exc)[:160]}


def build_ha_overview(
    config: dict[str, Any],
    runtime_by_env: dict[str, dict[str, Any]],
    *,
    machine: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base_dir = Path(config.get("logDir", "C:/PPLID/logs")).parent
    machine_data = load_machine_config(base_dir, machine)
    root_ha = config.get("databaseHa") if isinstance(config.get("databaseHa"), dict) else {}
    machine_ha = machine_data.get("databaseHa") if isinstance(machine_data.get("databaseHa"), dict) else {}
    effective = {**root_ha, **machine_ha}

    enabled = bool(effective.get("enabled", False))
    mode = str(effective.get("failoverMode") or "manual").strip().lower()
    endpoint = str(effective.get("endpoint") or "")
    port = _as_int(effective.get("port"), 5432)
    witness = _probe(str(effective.get("witness") or ""), 80)
    peer_url = str(machine_data.get("peerOpsUrl") or effective.get("peerOpsUrl") or "")
    peer = _probe(peer_url, 5190)
    fencing_state = str(effective.get("fencingState") or "unknown").strip().lower()
    fencing_ready = fencing_state in {"ready", "configured"}

    environments: dict[str, Any] = {}
    for env_name, runtime in runtime_by_env.items():
        runtime_ha = runtime.get("ha") if isinstance(runtime.get("ha"), dict) else {}
        replication = runtime_ha.get("replication") if isinstance(runtime_ha.get("replication"), dict) else {}
        environments[env_name] = {
            "role": runtime_ha.get("role") or "unknown",
            "writeReady": bool(runtime_ha.get("writeReady")),
            "database": runtime_ha.get("database") or runtime.get("database"),
            "replicationStatus": replication.get("status") or "unknown",
            "reachable": bool(runtime.get("reachable")),
        }

    replication_ready = bool(environments) and all(
        data["role"] in {"primary", "standby"}
        and data["replicationStatus"] == "healthy"
        for data in environments.values()
    )
    automatic_allowed = bool(
        enabled
        and mode == "automatic"
        and witness["reachable"]
        and peer["reachable"]
        and fencing_ready
        and replication_ready
    )

    if not enabled:
        status = "disabled"
    elif mode == "manual":
        status = "manual"
    elif automatic_allowed:
        status = "ready"
    else:
        status = "blocked"

    return {
        "enabled": enabled,
        "status": status,
        "node": {
            "id": str(machine_data.get("nodeId") or socket.gethostname()),
            "peerOpsUrl": peer_url,
            "peer": peer,
        },
        "database": {
            "endpoint": endpoint,
            "port": port,
            "failoverMode": mode,
            "synchronousCommit": str(effective.get("synchronousCommit") or "remote_apply"),
            "maxLagBytes": _as_int(effective.get("maxLagBytes")),
        },
        "witness": witness,
        "fencing": {"state": fencing_state, "ready": fencing_ready},
        "replicationReady": replication_ready,
        "automaticFailoverAllowed": automatic_allowed,
        "environments": environments,
        "lastFailover": effective.get("lastFailover") if isinstance(effective.get("lastFailover"), dict) else None,
    }
