"""Status da VPN Cisco Secure Client via vpncli.exe (Windows)."""
from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

DEFAULT_VPNCLI_PATH = (
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    / "Cisco"
    / "Cisco Secure Client"
    / "vpncli.exe"
)

_STATE_RE = re.compile(r"state\s*:\s*(\w+)", re.IGNORECASE)

# connected | disconnected | unknown
VpnState = str


@dataclass(frozen=True)
class VpncliResult:
    state: VpnState
    raw_output: str
    command: str
    exe: Path | None


def find_vpncli_exe() -> Path | None:
    env_raw = os.getenv("CISCO_VPNCLI_PATH", "").strip()
    if env_raw:
        candidate = Path(env_raw)
        if candidate.is_file():
            return candidate
        log.warning("CISCO_VPNCLI_PATH inválido: %s", env_raw)

    if DEFAULT_VPNCLI_PATH.is_file():
        return DEFAULT_VPNCLI_PATH
    return None


def parse_vpncli_state(output: str) -> VpnState:
    """Extrai o último state: da saída do vpncli. Retorna connected|disconnected|unknown."""
    matches = _STATE_RE.findall(output or "")
    if not matches:
        return "unknown"
    last = matches[-1].strip().lower()
    if last == "connected":
        return "connected"
    if last == "disconnected":
        return "disconnected"
    return "unknown"


def _decode_output(raw: bytes | None) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run_vpncli(exe: Path, arg: str, timeout: float) -> tuple[int, str]:
    try:
        result = subprocess.run(
            [str(exe), arg],
            capture_output=True,
            timeout=timeout,
            check=False,
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        stdout = _decode_output(result.stdout)
        stderr = _decode_output(result.stderr)
        output = "\n".join(part for part in (stdout, stderr) if part).strip()
        return result.returncode, output
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_output(exc.stdout if isinstance(exc.stdout, bytes) else None)
        stderr = _decode_output(exc.stderr if isinstance(exc.stderr, bytes) else None)
        output = "\n".join(part for part in (stdout, stderr) if part).strip()
        log.warning("vpncli %s timeout após %ss", arg, timeout)
        return -1, output or f"timeout after {timeout}s"
    except OSError as exc:
        log.warning("Falha ao executar vpncli %s: %s", arg, exc)
        return -1, str(exc)


def get_vpn_state(*, timeout: float | None = None) -> VpncliResult:
    """Consulta vpncli state (fallback status). state: connected|disconnected|unknown."""
    if timeout is None:
        try:
            timeout = float(os.getenv("CISCO_VPNCLI_TIMEOUT_SECONDS", "12"))
        except (TypeError, ValueError):
            timeout = 12.0
    timeout = max(3.0, timeout)

    if os.name != "nt":
        return VpncliResult(state="unknown", raw_output="not Windows", command="", exe=None)

    exe = find_vpncli_exe()
    if exe is None:
        return VpncliResult(state="unknown", raw_output="vpncli.exe not found", command="", exe=None)

    for arg in ("state", "status"):
        exit_code, output = _run_vpncli(exe, arg, timeout)
        state = parse_vpncli_state(output)
        if state in ("connected", "disconnected"):
            return VpncliResult(state=state, raw_output=output, command=arg, exe=exe)
        if state == "unknown" and output and "state:" in output.lower():
            # Teve state explícito (ex.: Connecting) — não tentar status de novo se já rodou state
            return VpncliResult(state="unknown", raw_output=output, command=arg, exe=exe)
        if arg == "state" and (exit_code != 0 or not output):
            log.info("vpncli state inconclusivo (exit %s); tentando status", exit_code)
            continue
        if arg == "state":
            # Saída sem state reconhecível — tenta status
            continue
        return VpncliResult(state=state, raw_output=output, command=arg, exe=exe)

    return VpncliResult(state="unknown", raw_output="", command="status", exe=exe)
