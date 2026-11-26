"""Process ownership checks: never kill by port or process name."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import time

import psutil


def identity(process_id: int) -> dict:
    process = psutil.Process(process_id)
    return {"pid": process.pid, "created": process.create_time(),
            "exe": str(Path(process.exe()).resolve()), "cmdline": process.cmdline()}


def owned(record: dict) -> psutil.Process | None:
    try:
        process = psutil.Process(record["pid"])
        if abs(process.create_time() - record["created"]) > .01:
            return None
        if os.path.normcase(process.exe()) != os.path.normcase(record["exe"]):
            return None
        if process.cmdline() != record["cmdline"]:
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
        return None


def assert_free(port: int, host="127.0.0.1"):
    with socket.socket() as stream:
        if os.name == "nt":
            stream.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        stream.bind((host, port))


def owns_pid(record: dict, process_id: int) -> bool:
    """Windows venv launchers keep the actual interpreter as a child."""
    process = owned(record)
    if process is None or not isinstance(process_id, int):
        return False
    try:
        return process_id == process.pid or any(child.pid == process_id for child in process.children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def launch(command: list[str], log: Path, *, cwd: Path | None = None, env=None):
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab", buffering=0) as output:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return process


def stop(record: dict, control: Path | None, timeout: float, *, force=False):
    process = owned(record)
    if process is None:
        # Identity mismatch is never permission to terminate another process.
        return
    if control:
        control.touch()
    try:
        process.wait(timeout=timeout)
    except psutil.TimeoutExpired:
        if not force:
            raise RuntimeError("Instance still draining; preserved for explicit recovery")
        process = owned(record)
        if process:
            # Only force=True callers (isolated test cleanup) use this path.
            # Stopping just a Windows venv launcher leaves its interpreter alive.
            descendants = []
            for child in process.children(recursive=True):
                try:
                    descendants.append(identity(child.pid))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            for child_record in reversed(descendants):
                child = owned(child_record)
                if child:
                    try:
                        child.terminate()
                        child.wait(timeout=10)
                    except psutil.NoSuchProcess:
                        pass
            process = owned(record)
            if process:
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except psutil.NoSuchProcess:
                    pass
