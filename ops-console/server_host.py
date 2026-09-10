"""Windows host telemetry for the PPLID operations console.

The collector is deliberately independent from the environment health collector:
host resources are sampled once per machine, never once per PPLID environment.
"""
from __future__ import annotations

import json
import logging
import math
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import server_ops

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - exercised by degraded-mode tests
    psutil = None  # type: ignore


HOST_TARGET = "HOST"
HOST_METRICS = {
    "host_cpu_pct",
    "host_memory_used_pct",
    "host_memory_used_bytes",
    "host_commit_used_pct",
    "host_commit_used_bytes",
    "host_swap_used_pct",
    "host_disk_used_pct",
    "host_disk_free_bytes",
    "host_disk_read_bps",
    "host_disk_write_bps",
    "host_net_rx_bps",
    "host_net_tx_bps",
    "host_gpu_util_pct",
    "host_gpu_memory_used_pct",
    "host_gpu_temperature_c",
}

DEFAULT_HOST_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "intervalSec": 15,
    "retentionDays": 7,
    "processLimit": 12,
    "gpuEnabled": True,
    "thresholds": {
        "cpuWarnPct": 85,
        "cpuCriticalPct": 95,
        "memoryWarnPct": 85,
        "memoryCriticalPct": 95,
        "commitWarnPct": 85,
        "commitCriticalPct": 95,
        "diskWarnFreePct": 15,
        "diskCriticalFreePct": 8,
        "gpuMemoryWarnPct": 90,
        "gpuTemperatureWarnC": 80,
        "gpuTemperatureCriticalC": 90,
    },
}

_logger = logging.getLogger("ops.host")
_STOP = threading.Event()
_THREAD: threading.Thread | None = None
_CONFIG: dict[str, Any] | None = None
_SNAPSHOT_LOCK = threading.Lock()
_LATEST_SNAPSHOT: dict[str, Any] | None = None
_LAST_SAMPLE_MONOTONIC = 0.0
_LAST_RATE_COUNTERS: dict[str, tuple[float, float, float]] = {}
_ALERT_STATES: dict[str, str] = {}
_LAST_EVENTS: dict[str, float] = {}
_GPU_INVENTORY_CACHE: tuple[float, list[dict[str, Any]]] | None = None
_PROCESS_CACHE: tuple[
    float,
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
] | None = None
_EVENT_DEDUPE_SEC = 15 * 60


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _import_ops_store(config: dict[str, Any] | None = None):
    ops_root = server_ops.resolve_ops_lib_dir(config)
    if str(ops_root) not in sys.path:
        sys.path.insert(0, str(ops_root))
    import ops_store  # type: ignore

    return ops_store


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def get_host_settings(config: dict[str, Any]) -> dict[str, Any]:
    settings = dict(DEFAULT_HOST_SETTINGS)
    settings["thresholds"] = dict(DEFAULT_HOST_SETTINGS["thresholds"])
    base_dir = server_ops.get_base_dir(config)
    machine_path = base_dir / "machine.config.json"
    if machine_path.is_file():
        try:
            machine = json.loads(machine_path.read_text(encoding="utf-8-sig"))
            host_cfg = machine.get("hostMonitoring") or machine.get("monitoring", {}).get("host") or {}
            if isinstance(host_cfg, dict):
                settings = _deep_merge(settings, host_cfg)
        except (OSError, json.JSONDecodeError):
            pass
    inline = config.get("hostMonitoring") or {}
    if isinstance(inline, dict):
        settings = _deep_merge(settings, inline)
    settings["intervalSec"] = max(5, min(int(settings.get("intervalSec") or 15), 300))
    settings["processLimit"] = max(3, min(int(settings.get("processLimit") or 12), 50))
    return settings


def _safe_rate(key: str, first: float, second: float = 0.0) -> tuple[float, float]:
    now = time.monotonic()
    previous = _LAST_RATE_COUNTERS.get(key)
    _LAST_RATE_COUNTERS[key] = (now, float(first), float(second))
    if not previous:
        return 0.0, 0.0
    elapsed = max(now - previous[0], 0.001)
    return max(0.0, (float(first) - previous[1]) / elapsed), max(
        0.0, (float(second) - previous[2]) / elapsed
    )


def _gpu_inventory() -> list[dict[str, Any]]:
    global _GPU_INVENTORY_CACHE
    now = time.time()
    if _GPU_INVENTORY_CACHE and now - _GPU_INVENTORY_CACHE[0] < 3600:
        return _GPU_INVENTORY_CACHE[1]
    inventory: list[dict[str, Any]] = []
    if os.name == "nt":
        command = (
            "Get-CimInstance Win32_VideoController | "
            "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress"
        )
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                raw = json.loads(completed.stdout)
                rows = raw if isinstance(raw, list) else [raw]
                for row in rows:
                    inventory.append(
                        {
                            "name": str(row.get("Name") or "GPU"),
                            "memoryTotalBytes": int(row.get("AdapterRAM") or 0) or None,
                            "driverVersion": row.get("DriverVersion"),
                        }
                    )
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
            pass
    _GPU_INVENTORY_CACHE = (now, inventory)
    return inventory


def _nvidia_gpus() -> list[dict[str, Any]]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return []
    fields = "index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,driver_version"
    try:
        completed = subprocess.run(
            [executable, f"--query-gpu={fields}", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    result: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 8:
            continue

        def number(value: str) -> float | None:
            try:
                return float(value)
            except ValueError:
                return None

        used_mb, total_mb = number(parts[3]), number(parts[4])
        used_pct = (used_mb * 100.0 / total_mb) if used_mb is not None and total_mb else None
        result.append(
            {
                "index": int(number(parts[0]) or 0),
                "name": parts[1],
                "utilizationPct": number(parts[2]),
                "memoryUsedBytes": int(used_mb * 1024 * 1024) if used_mb is not None else None,
                "memoryTotalBytes": int(total_mb * 1024 * 1024) if total_mb is not None else None,
                "memoryUsedPct": round(used_pct, 1) if used_pct is not None else None,
                "temperatureC": number(parts[5]),
                "powerWatts": number(parts[6]),
                "driverVersion": parts[7],
                "metricsAvailable": True,
            }
        )
    return result


def _collect_gpus(enabled: bool) -> list[dict[str, Any]]:
    if not enabled:
        return []
    nvidia = _nvidia_gpus()
    if nvidia:
        return nvidia
    return [{**row, "index": idx, "metricsAvailable": False} for idx, row in enumerate(_gpu_inventory())]


def _port_pid_map(config: dict[str, Any]) -> dict[int, tuple[str, str]]:
    wanted: dict[int, tuple[str, str]] = {}
    for env in ("MAIN", "DEV", "HOM"):
        cfg = config.get(env) or {}
        for field, service in (("backendPort", "backend"), ("frontendPort", "frontend")):
            try:
                wanted[int(cfg.get(field))] = (env, service)
            except (TypeError, ValueError):
                pass
    if psutil is None:
        return {}
    mapped: dict[int, tuple[str, str]] = {}
    try:
        for conn in psutil.net_connections(kind="inet"):
            port = getattr(getattr(conn, "laddr", None), "port", None)
            if port in wanted and conn.pid:
                mapped[int(conn.pid)] = wanted[port]
    except (psutil.AccessDenied, OSError):
        pass
    return mapped


def _classify_process(
    config: dict[str, Any], pid: int, name: str, exe: str, cwd: str, mapped: dict[int, tuple[str, str]]
) -> tuple[str | None, str | None]:
    if pid == os.getpid():
        return "HOST", "ops-console"
    if pid in mapped:
        return mapped[pid]
    haystack = " ".join((name, exe, cwd)).lower().replace("/", "\\")
    for env in ("MAIN", "DEV", "HOM"):
        cfg = config.get(env) or {}
        repo_name = str(cfg.get("repoName") or f"PPLID_{env}").lower()
        if repo_name in haystack or f"deploy\\{env.lower()}\\current" in haystack:
            if "node" in name.lower():
                return env, "frontend"
            return env, "backend"
    if "postgres" in name.lower():
        return "HOST", "postgresql"
    if "pplid" in haystack:
        return "HOST", "pplid-worker"
    return None, None


def _collect_processes(
    config: dict[str, Any], limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    global _PROCESS_CACHE
    now_monotonic = time.monotonic()
    if _PROCESS_CACHE and now_monotonic - _PROCESS_CACHE[0] < 60:
        return list(_PROCESS_CACHE[1]), list(_PROCESS_CACHE[2]), list(_PROCESS_CACHE[3])
    if psutil is None:
        return [], [], []
    mapped = _port_pid_map(config)
    rows: list[dict[str, Any]] = []
    now = time.time()
    attrs = ["pid", "name", "exe", "cwd", "cpu_percent", "memory_info", "create_time", "status"]
    for proc in psutil.process_iter(attrs=attrs, ad_value=None):
        try:
            info = proc.info
            memory_info = info.get("memory_info")
            rss = int(getattr(memory_info, "rss", 0) or 0)
            commit = int(
                getattr(memory_info, "private", 0)
                or getattr(memory_info, "pagefile", 0)
                or 0
            )
            env, service = _classify_process(
                config,
                int(info.get("pid") or 0),
                str(info.get("name") or "processo"),
                str(info.get("exe") or ""),
                str(info.get("cwd") or ""),
                mapped,
            )
            rows.append(
                {
                    "pid": int(info.get("pid") or 0),
                    "name": str(info.get("name") or "processo"),
                    "environment": env,
                    "service": service,
                    "cpuPct": round(float(info.get("cpu_percent") or 0), 1),
                    "memoryBytes": rss,
                    "commitBytes": commit,
                    "uptimeSec": max(0, int(now - float(info.get("create_time") or now))),
                    "status": str(info.get("status") or "unknown"),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError, TypeError):
            continue
    rows.sort(key=lambda item: (item["cpuPct"], item["memoryBytes"]), reverse=True)
    pplid_rows = [item for item in rows if item.get("service")]
    commit_rows = sorted(rows, key=lambda item: item["commitBytes"], reverse=True)
    result = (pplid_rows[: max(limit * 2, 20)], rows[:limit], commit_rows[:limit])
    _PROCESS_CACHE = (now_monotonic, result[0], result[1], result[2])
    return list(result[0]), list(result[1]), list(result[2])


def _fallback_memory() -> dict[str, Any]:
    if os.name != "nt":
        return {"totalBytes": 0, "availableBytes": 0, "usedBytes": 0, "usedPct": 0.0}
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memoryLoad", ctypes.c_ulong),
                ("totalPhys", ctypes.c_ulonglong),
                ("availPhys", ctypes.c_ulonglong),
                ("totalPageFile", ctypes.c_ulonglong),
                ("availPageFile", ctypes.c_ulonglong),
                ("totalVirtual", ctypes.c_ulonglong),
                ("availVirtual", ctypes.c_ulonglong),
                ("availExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        used = int(status.totalPhys - status.availPhys)
        return {
            "totalBytes": int(status.totalPhys),
            "availableBytes": int(status.availPhys),
            "usedBytes": used,
            "usedPct": float(status.memoryLoad),
        }
    except (OSError, AttributeError):
        return {"totalBytes": 0, "availableBytes": 0, "usedBytes": 0, "usedPct": 0.0}


def _windows_commit_memory() -> dict[str, Any]:
    """Return system commit charge/limit, which is distinct from physical RAM."""
    unavailable = {
        "supported": False,
        "usedBytes": None,
        "limitBytes": None,
        "availableBytes": None,
        "peakBytes": None,
        "usedPct": None,
    }
    if os.name != "nt":
        return unavailable
    try:
        import ctypes

        class PerformanceInformation(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("commitTotal", ctypes.c_size_t),
                ("commitLimit", ctypes.c_size_t),
                ("commitPeak", ctypes.c_size_t),
                ("physicalTotal", ctypes.c_size_t),
                ("physicalAvailable", ctypes.c_size_t),
                ("systemCache", ctypes.c_size_t),
                ("kernelTotal", ctypes.c_size_t),
                ("kernelPaged", ctypes.c_size_t),
                ("kernelNonPaged", ctypes.c_size_t),
                ("pageSize", ctypes.c_size_t),
                ("handleCount", ctypes.c_ulong),
                ("processCount", ctypes.c_ulong),
                ("threadCount", ctypes.c_ulong),
            ]

        info = PerformanceInformation()
        info.cb = ctypes.sizeof(info)
        if not ctypes.windll.psapi.GetPerformanceInfo(ctypes.byref(info), info.cb):
            return unavailable
        page_size = int(info.pageSize)
        used = int(info.commitTotal) * page_size
        limit = int(info.commitLimit) * page_size
        return {
            "supported": True,
            "usedBytes": used,
            "limitBytes": limit,
            "availableBytes": max(0, limit - used),
            "peakBytes": int(info.commitPeak) * page_size,
            "usedPct": round(used * 100.0 / limit, 1) if limit else 0.0,
        }
    except (OSError, AttributeError, TypeError, ValueError):
        return unavailable


def collect_host_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    settings = get_host_settings(config)
    generated_at = _utc_now_iso()
    errors: list[str] = []
    if psutil is None:
        errors.append("psutil não instalado; coleta limitada")
        memory = _fallback_memory()
        root = Path(server_ops.get_base_dir(config).anchor or "C:/")
        try:
            usage = shutil.disk_usage(root)
            disks = [
                {
                    "device": str(root),
                    "mount": str(root),
                    "totalBytes": usage.total,
                    "usedBytes": usage.used,
                    "freeBytes": usage.free,
                    "usedPct": round(usage.used * 100.0 / usage.total, 1) if usage.total else 0,
                    "freePct": round(usage.free * 100.0 / usage.total, 1) if usage.total else 0,
                }
            ]
        except OSError as exc:
            errors.append(str(exc))
            disks = []
        cpu = {"usedPct": 0.0, "perCorePct": [], "logicalCores": os.cpu_count() or 0, "physicalCores": None}
        swap = {"totalBytes": 0, "usedBytes": 0, "usedPct": 0.0}
        network = {"rxBps": 0.0, "txBps": 0.0, "bytesReceived": 0, "bytesSent": 0, "interfaces": []}
        disk_io = {"readBps": 0.0, "writeBps": 0.0}
        boot_time = None
        pplid_processes, top_processes, top_commit_processes = [], [], []
    else:
        cpu = {
            "usedPct": round(float(psutil.cpu_percent(interval=None)), 1),
            "perCorePct": [round(float(v), 1) for v in psutil.cpu_percent(interval=None, percpu=True)],
            "logicalCores": psutil.cpu_count(logical=True) or 0,
            "physicalCores": psutil.cpu_count(logical=False),
        }
        vm = psutil.virtual_memory()
        memory = {
            "totalBytes": int(vm.total),
            "availableBytes": int(vm.available),
            "usedBytes": int(vm.used),
            "usedPct": round(float(vm.percent), 1),
        }
        sm = psutil.swap_memory()
        swap = {"totalBytes": int(sm.total), "usedBytes": int(sm.used), "usedPct": round(float(sm.percent), 1)}
        disks = []
        seen_mounts: set[str] = set()
        try:
            for partition in psutil.disk_partitions(all=False):
                mount = str(partition.mountpoint)
                if mount in seen_mounts:
                    continue
                seen_mounts.add(mount)
                try:
                    usage = psutil.disk_usage(mount)
                except (OSError, PermissionError):
                    continue
                disks.append(
                    {
                        "device": str(partition.device),
                        "mount": mount,
                        "fileSystem": str(partition.fstype or ""),
                        "totalBytes": int(usage.total),
                        "usedBytes": int(usage.used),
                        "freeBytes": int(usage.free),
                        "usedPct": round(float(usage.percent), 1),
                        "freePct": round(100.0 - float(usage.percent), 1),
                    }
                )
        except (OSError, PermissionError) as exc:
            errors.append(f"discos: {exc}")
        io = psutil.disk_io_counters()
        read_bps, write_bps = _safe_rate("disk", getattr(io, "read_bytes", 0), getattr(io, "write_bytes", 0))
        disk_io = {"readBps": round(read_bps, 1), "writeBps": round(write_bps, 1)}
        net = psutil.net_io_counters()
        rx_bps, tx_bps = _safe_rate("network", getattr(net, "bytes_recv", 0), getattr(net, "bytes_sent", 0))
        interfaces = []
        try:
            stats = psutil.net_if_stats()
            interfaces = [name for name, item in stats.items() if item.isup]
        except (OSError, AttributeError):
            pass
        network = {
            "rxBps": round(rx_bps, 1),
            "txBps": round(tx_bps, 1),
            "bytesReceived": int(getattr(net, "bytes_recv", 0)),
            "bytesSent": int(getattr(net, "bytes_sent", 0)),
            "interfaces": interfaces,
        }
        boot_time = float(psutil.boot_time())
        pplid_processes, top_processes, top_commit_processes = _collect_processes(
            config, int(settings["processLimit"])
        )

    gpus = _collect_gpus(bool(settings.get("gpuEnabled", True)))
    commit = _windows_commit_memory()
    return {
        "generatedAt": generated_at,
        "hostname": platform.node() or "unknown",
        "platform": {"system": platform.system(), "release": platform.release(), "version": platform.version()},
        "bootAt": datetime.fromtimestamp(boot_time, tz=timezone.utc).isoformat().replace("+00:00", "Z") if boot_time else None,
        "uptimeSec": max(0, int(time.time() - boot_time)) if boot_time else None,
        "cpu": cpu,
        "memory": memory,
        "commit": commit,
        "swap": swap,
        "disks": disks,
        "diskIo": disk_io,
        "network": network,
        "gpus": gpus,
        "pplidProcesses": pplid_processes,
        "topProcesses": top_processes,
        "topCommitProcesses": top_commit_processes,
        "collector": {
            "mode": "psutil" if psutil is not None else "degraded",
            "intervalSec": settings["intervalSec"],
            "errors": errors,
        },
    }


def _snapshot_samples(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    at = snapshot["generatedAt"]
    rows: list[dict[str, Any]] = []

    def add(metric: str, value: Any, labels: dict[str, Any] | None = None) -> None:
        if value is not None:
            rows.append(
                {"environment": HOST_TARGET, "metric_key": metric, "value": float(value), "labels": labels, "recorded_at": at}
            )

    add("host_cpu_pct", snapshot.get("cpu", {}).get("usedPct"))
    add("host_memory_used_pct", snapshot.get("memory", {}).get("usedPct"))
    add("host_memory_used_bytes", snapshot.get("memory", {}).get("usedBytes"))
    add("host_commit_used_pct", snapshot.get("commit", {}).get("usedPct"))
    add("host_commit_used_bytes", snapshot.get("commit", {}).get("usedBytes"))
    add("host_swap_used_pct", snapshot.get("swap", {}).get("usedPct"))
    add("host_disk_read_bps", snapshot.get("diskIo", {}).get("readBps"))
    add("host_disk_write_bps", snapshot.get("diskIo", {}).get("writeBps"))
    add("host_net_rx_bps", snapshot.get("network", {}).get("rxBps"))
    add("host_net_tx_bps", snapshot.get("network", {}).get("txBps"))
    for disk in snapshot.get("disks") or []:
        labels = {"mount": disk.get("mount"), "device": disk.get("device")}
        add("host_disk_used_pct", disk.get("usedPct"), labels)
        add("host_disk_free_bytes", disk.get("freeBytes"), labels)
    for gpu in snapshot.get("gpus") or []:
        labels = {"index": gpu.get("index"), "name": gpu.get("name")}
        add("host_gpu_util_pct", gpu.get("utilizationPct"), labels)
        add("host_gpu_memory_used_pct", gpu.get("memoryUsedPct"), labels)
        add("host_gpu_temperature_c", gpu.get("temperatureC"), labels)
    return rows


def _is_sustained(ops_store, db_path: Path, metric: str, threshold: float, seconds: int) -> bool:
    since = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    points = ops_store.query_monitor_series(HOST_TARGET, metric, since=since, limit=1000, db_path=db_path)
    interval = int(get_host_settings(_CONFIG or {}).get("intervalSec") or 15)
    required = max(2, int(seconds / interval * 0.6))
    return len(points) >= required and all(float(p.get("value") or 0) >= threshold for p in points)


def _record_alert(ops_store, db_path: Path, key: str, severity: str, title: str, detail: str) -> None:
    previous = _ALERT_STATES.get(key, "ok")
    if previous == severity:
        return
    now = time.time()
    if severity != "ok" and now - _LAST_EVENTS.get(f"{key}:{severity}", 0) < _EVENT_DEDUPE_SEC:
        _ALERT_STATES[key] = severity
        return
    if severity == "ok":
        if previous != "ok":
            ops_store.insert_monitor_event(
                HOST_TARGET, "info", "host", f"Recuperado: {title}", detail=detail, db_path=db_path
            )
    else:
        ops_store.insert_monitor_event(HOST_TARGET, severity, "host", title, detail=detail, db_path=db_path)
        _LAST_EVENTS[f"{key}:{severity}"] = now
    _ALERT_STATES[key] = severity


def _evaluate_alerts(config: dict[str, Any], snapshot: dict[str, Any], ops_store, db_path: Path) -> None:
    thresholds = get_host_settings(config).get("thresholds") or {}
    cpu = float(snapshot.get("cpu", {}).get("usedPct") or 0)
    cpu_sev = "ok"
    if cpu >= float(thresholds.get("cpuCriticalPct", 95)) and _is_sustained(
        ops_store, db_path, "host_cpu_pct", float(thresholds.get("cpuCriticalPct", 95)), 120
    ):
        cpu_sev = "critical"
    elif cpu >= float(thresholds.get("cpuWarnPct", 85)) and _is_sustained(
        ops_store, db_path, "host_cpu_pct", float(thresholds.get("cpuWarnPct", 85)), 300
    ):
        cpu_sev = "warn"
    _record_alert(ops_store, db_path, "cpu", cpu_sev, "CPU do host sob pressão", f"Uso atual: {cpu:.1f}%")

    memory = float(snapshot.get("memory", {}).get("usedPct") or 0)
    mem_sev = "ok"
    if memory >= float(thresholds.get("memoryCriticalPct", 95)) and _is_sustained(
        ops_store, db_path, "host_memory_used_pct", float(thresholds.get("memoryCriticalPct", 95)), 120
    ):
        mem_sev = "critical"
    elif memory >= float(thresholds.get("memoryWarnPct", 85)) and _is_sustained(
        ops_store, db_path, "host_memory_used_pct", float(thresholds.get("memoryWarnPct", 85)), 300
    ):
        mem_sev = "warn"
    _record_alert(ops_store, db_path, "memory", mem_sev, "Memória RAM do host sob pressão", f"Uso atual: {memory:.1f}%")

    commit_pct = snapshot.get("commit", {}).get("usedPct")
    if commit_pct is not None:
        commit = float(commit_pct)
        commit_sev = "ok"
        if commit >= float(thresholds.get("commitCriticalPct", 95)) and _is_sustained(
            ops_store, db_path, "host_commit_used_pct", float(thresholds.get("commitCriticalPct", 95)), 120
        ):
            commit_sev = "critical"
        elif commit >= float(thresholds.get("commitWarnPct", 85)) and _is_sustained(
            ops_store, db_path, "host_commit_used_pct", float(thresholds.get("commitWarnPct", 85)), 300
        ):
            commit_sev = "warn"
        used = int(snapshot.get("commit", {}).get("usedBytes") or 0)
        limit = int(snapshot.get("commit", {}).get("limitBytes") or 0)
        _record_alert(
            ops_store,
            db_path,
            "commit",
            commit_sev,
            "Memória virtual comprometida sob pressão",
            f"Uso atual: {commit:.1f}% ({used} de {limit} bytes)",
        )

    for disk in snapshot.get("disks") or []:
        free = float(disk.get("freePct") or 0)
        key = f"disk:{disk.get('mount')}"
        severity = "critical" if free <= float(thresholds.get("diskCriticalFreePct", 8)) else (
            "warn" if free <= float(thresholds.get("diskWarnFreePct", 15)) else "ok"
        )
        _record_alert(
            ops_store,
            db_path,
            key,
            severity,
            f"Pouco espaço livre em {disk.get('mount')}",
            f"Espaço livre: {free:.1f}%",
        )

    for gpu in snapshot.get("gpus") or []:
        idx = gpu.get("index", 0)
        vram = gpu.get("memoryUsedPct")
        if vram is not None:
            threshold = float(thresholds.get("gpuMemoryWarnPct", 90))
            severity = "warn" if float(vram) >= threshold and _is_sustained(
                ops_store, db_path, "host_gpu_memory_used_pct", threshold, 300
            ) else "ok"
            _record_alert(
                ops_store, db_path, f"gpu-vram:{idx}", severity, "VRAM da GPU sob pressão", f"Uso atual: {float(vram):.1f}%"
            )
        temp = gpu.get("temperatureC")
        if temp is not None:
            temp_f = float(temp)
            severity = "critical" if temp_f >= float(thresholds.get("gpuTemperatureCriticalC", 90)) else (
                "warn" if temp_f >= float(thresholds.get("gpuTemperatureWarnC", 80)) else "ok"
            )
            _record_alert(
                ops_store, db_path, f"gpu-temp:{idx}", severity, "Temperatura elevada da GPU", f"Temperatura atual: {temp_f:.1f} °C"
            )


def collect_and_store(config: dict[str, Any]) -> dict[str, Any]:
    global _LATEST_SNAPSHOT, _LAST_SAMPLE_MONOTONIC
    snapshot = collect_host_snapshot(config)
    ops_store = _import_ops_store(config)
    db_path = server_ops.get_ops_store_db_path(server_ops.get_base_dir(config))
    if not db_path:
        db_path = server_ops.get_base_dir(config) / "ops" / "data" / "ops-store.db"
    ops_store.init_store(db_path)
    samples = _snapshot_samples(snapshot)
    if samples:
        ops_store.insert_monitor_samples_batch(samples, db_path=db_path)
    _evaluate_alerts(config, snapshot, ops_store, db_path)
    with _SNAPSHOT_LOCK:
        _LATEST_SNAPSHOT = snapshot
        _LAST_SAMPLE_MONOTONIC = time.monotonic()
    return snapshot


def _collector_loop() -> None:
    while not _STOP.is_set():
        config = _CONFIG or {}
        started = time.monotonic()
        try:
            collect_and_store(config)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("host collector tick failed: %s", exc)
        interval = float(get_host_settings(config).get("intervalSec") or 15)
        _STOP.wait(max(1.0, interval - (time.monotonic() - started)))


def start_host_collector(config: dict[str, Any]) -> None:
    global _THREAD, _CONFIG
    _CONFIG = config
    if not get_host_settings(config).get("enabled", True):
        return
    if _THREAD and _THREAD.is_alive():
        return
    _STOP.clear()
    _THREAD = threading.Thread(target=_collector_loop, name="ops-host-collector", daemon=True)
    _THREAD.start()


def stop_host_collector() -> None:
    _STOP.set()


def _latest_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    with _SNAPSHOT_LOCK:
        snapshot = dict(_LATEST_SNAPSHOT) if _LATEST_SNAPSHOT else None
    if snapshot:
        return snapshot
    # Startup/tests: provide a useful snapshot without waiting for the first thread tick.
    return collect_host_snapshot(config)


def build_host_summary(config: dict[str, Any]) -> dict[str, Any]:
    snapshot = _latest_snapshot(config)
    settings = get_host_settings(config)
    age = max(0.0, time.monotonic() - _LAST_SAMPLE_MONOTONIC) if _LAST_SAMPLE_MONOTONIC else None
    stale = age is None or age > float(settings["intervalSec"]) * 3
    snapshot["collector"] = {
        **(snapshot.get("collector") or {}),
        "enabled": bool(settings.get("enabled", True)),
        "status": "stale" if stale else "ok",
        "sampleAgeSec": round(age, 1) if age is not None else None,
        "psutilAvailable": psutil is not None,
    }
    snapshot["thresholds"] = settings.get("thresholds") or {}
    orphan_path = server_ops.get_base_dir(config) / "ops" / "data" / "orphan-bots.json"
    try:
        orphan = json.loads(orphan_path.read_text(encoding="utf-8"))
        if not isinstance(orphan, dict):
            raise ValueError("invalid orphan state")
    except (OSError, ValueError, json.JSONDecodeError):
        orphan = {"scannedAt": None, "detected": 0, "stopped": 0, "failed": 0, "items": []}
    snapshot["orphanBots"] = {
        "scannedAt": orphan.get("scannedAt"),
        "detected": int(orphan.get("detected") or 0),
        "stopped": int(orphan.get("stopped") or 0),
        "failed": int(orphan.get("failed") or 0),
        "items": orphan.get("items") if isinstance(orphan.get("items"), list) else [],
    }
    return snapshot


def _downsample(points: list[dict[str, Any]], maximum: int = 360) -> list[dict[str, Any]]:
    if len(points) <= maximum:
        return points
    stride = len(points) / maximum
    return [points[min(int(i * stride), len(points) - 1)] for i in range(maximum)]


def build_host_series(config: dict[str, Any], metric: str, *, hours: int = 24) -> dict[str, Any]:
    if metric not in HOST_METRICS:
        raise ValueError("Métrica de host inválida")
    hours = max(1, min(int(hours), 168))
    ops_store = _import_ops_store(config)
    db_path = server_ops.get_ops_store_db_path(server_ops.get_base_dir(config))
    if not db_path:
        db_path = server_ops.get_base_dir(config) / "ops" / "data" / "ops-store.db"
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    bucket_seconds = max(1, math.ceil(hours * 3600 / 360))
    raw = ops_store.query_monitor_series_bucketed(
        HOST_TARGET,
        metric,
        since=since,
        bucket_seconds=bucket_seconds,
        limit=360,
        db_path=db_path,
    )
    points = [
        {"t": item.get("recorded_at"), "v": item.get("value"), "labels": item.get("labels") or {}}
        for item in raw
    ]
    points = _downsample(points)
    return {
        "target": HOST_TARGET,
        "metricKey": metric,
        "hours": hours,
        "since": since,
        "points": points,
        "pointCount": len(points),
        "lastSampleAt": points[-1]["t"] if points else None,
    }


_SENSITIVE_KEYS = ("password", "secret", "token", "cookie", "authorization", "private", "credential")


def sanitize_diagnostic(value: Any) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if any(part in str(key).lower() for part in _SENSITIVE_KEYS):
                clean[key] = "***"
            else:
                clean[key] = sanitize_diagnostic(item)
        return clean
    if isinstance(value, list):
        return [sanitize_diagnostic(item) for item in value]
    return value


def build_diagnostic_snapshot(
    config: dict[str, Any], *, overview: dict[str, Any] | None = None, incidents: dict[str, Any] | None = None
) -> dict[str, Any]:
    payload = {
        "generatedAt": _utc_now_iso(),
        "host": build_host_summary(config),
        "overview": overview or {},
        "incidents": incidents or {},
    }
    return sanitize_diagnostic(payload)
