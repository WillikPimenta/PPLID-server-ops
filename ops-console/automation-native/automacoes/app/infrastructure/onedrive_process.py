"""Reinício leve do processo OneDrive (Windows)."""
from __future__ import annotations

import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

_ONEDRIVE_EXE_CANDIDATES = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "OneDrive" / "OneDrive.exe",
    Path(r"C:\Program Files\Microsoft OneDrive\OneDrive.exe"),
    Path(r"C:\Program Files (x86)\Microsoft OneDrive\OneDrive.exe"),
)


def find_onedrive_exe() -> Path | None:
    for path in _ONEDRIVE_EXE_CANDIDATES:
        if path.is_file():
            return path
    return None


def is_onedrive_running() -> bool:
    """Retorna True se OneDrive.exe estiver em execução."""
    if os.name != "nt":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq OneDrive.exe", "/NH"],
            capture_output=True,
            text=True,
            check=False,
            creationflags=_CREATE_NO_WINDOW,
        )
        output = (result.stdout or "").lower()
        return "onedrive.exe" in output and "no tasks" not in output
    except Exception as exc:
        log.warning("Falha ao verificar processo OneDrive: %s", exc)
        return False


def _simulate_restart() -> bool:
    return os.getenv("ONEDRIVE_SIMULATE_RESTART", "").strip() == "1"


def _launch_onedrive_background(exe: Path, *, simulate: bool) -> bool:
    if simulate:
        log.info("SIMULAÇÃO: %s /background", exe)
        return True
    try:
        subprocess.Popen(
            [str(exe), "/background"],
            creationflags=_CREATE_NO_WINDOW,
        )
        log.info("OneDrive iniciado: %s /background", exe)
        return True
    except Exception as exc:
        log.exception("Falha ao iniciar OneDrive: %s", exc)
        return False


def start_onedrive_background(simulate: bool | None = None) -> bool:
    """Inicia OneDrive.exe /background se o executável existir."""
    if os.name != "nt":
        log.error("Início do OneDrive disponível apenas em Windows")
        return False

    if simulate is None:
        simulate = _simulate_restart()

    exe = find_onedrive_exe()
    if exe is None:
        log.error("OneDrive.exe não encontrado")
        return False

    if is_onedrive_running():
        log.info("OneDrive.exe já em execução — início ignorado")
        return True

    return _launch_onedrive_background(exe, simulate=simulate)


def restart_onedrive_background(simulate: bool | None = None) -> bool:
    """Encerra OneDrive.exe e reinicia com /background."""
    if os.name != "nt":
        log.error("Reinício do OneDrive disponível apenas em Windows")
        return False

    if simulate is None:
        simulate = _simulate_restart()

    exe = find_onedrive_exe()
    if exe is None:
        log.error("OneDrive.exe não encontrado")
        return False

    if simulate:
        log.info("SIMULAÇÃO: taskkill OneDrive.exe + %s /background", exe)
        return True

    try:
        subprocess.run(
            ["taskkill", "/IM", "OneDrive.exe", "/F"],
            check=False,
            capture_output=True,
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception as exc:
        log.warning("taskkill OneDrive.exe: %s", exc)

    time.sleep(2)

    return _launch_onedrive_background(exe, simulate=simulate)
