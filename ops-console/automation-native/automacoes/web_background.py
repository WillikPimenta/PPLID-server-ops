"""Inicia/para a interface web em segundo plano (sem .bat/.ps1 — compatível com política corporativa)."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
PID_FILE = PROJECT_ROOT / ".web-server.pid"
LOG_DIR = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / "PLAN_IDF_SERASA_BOTS" / "logs"
LOG_FILE = LOG_DIR / "web-server.log"

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_DETACHED_PROCESS = 0x00000008
_NEW_PROCESS_GROUP = 0x00000200
_WIN_FLAGS = _CREATE_NO_WINDOW | _DETACHED_PROCESS | _NEW_PROCESS_GROUP


def _default_port() -> str:
    return os.getenv("FLASK_PORT", "5009")


def _process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def _python_executable() -> str:
    """Usa python.exe (nao pythonw) para o servidor e subprocessos dos robos."""
    exe = Path(sys.executable)
    if exe.stem.lower() == "pythonw":
        candidate = exe.with_name("python.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def start_server() -> int:
    existing = _read_pid()
    if existing and _process_running(existing):
        print(f"Servidor ja esta rodando (PID {existing}).")
        print(f"URL: http://127.0.0.1:{_default_port()}")
        print("Para parar: python web_background.py stop")
        return 0

    if PID_FILE.exists():
        PID_FILE.unlink(missing_ok=True)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = open(LOG_FILE, "a", encoding="utf-8")
    log_handle.write(f"\n--- iniciando web server (pid file: {PID_FILE}) ---\n")
    log_handle.flush()

    proc = subprocess.Popen(
        [_python_executable(), str(PROJECT_ROOT / "run.py")],
        cwd=str(PROJECT_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        creationflags=_WIN_FLAGS,
        close_fds=True,
    )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")

    print(f"Servidor iniciado em background (PID {proc.pid}).")
    print(f"URL: http://127.0.0.1:{_default_port()}")
    print(f"Log: {LOG_FILE}")
    print("Para parar: python web_background.py stop")
    return 0


def stop_server() -> int:
    pid = _read_pid()
    if pid is None:
        print("Nenhum servidor em background registrado.")
        return 0

    if _process_running(pid):
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                check=False,
                creationflags=_CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGTERM)
        print(f"Servidor parado (PID {pid}).")
    else:
        print(f"Processo {pid} nao encontrado (ja encerrado).")

    PID_FILE.unlink(missing_ok=True)
    return 0


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        raise SystemExit(start_server())
    parser = argparse.ArgumentParser(description="Gerencia o servidor web Flask em background.")
    parser.add_argument("action", choices=("start", "stop"), help="start ou stop")
    args = parser.parse_args(argv)
    code = start_server() if args.action == "start" else stop_server()
    raise SystemExit(code)


if __name__ == "__main__":
    main()
