"""Bot OneDrive: monitora status e recupera sessão via scripts PowerShell."""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger("robots.bot_onedrive")
parar_event = threading.Event()

_status_callback = None
_progress_callback = None

_BOTS_DIR = Path(__file__).resolve().parent
_SCRIPT_VERIFICAR = _BOTS_DIR / "Verificar-Status-OneDrive.ps1"
_SCRIPT_RECUPERAR = _BOTS_DIR / "Recuperar-Sessao-OneDrive.ps1"

_DEFAULT_CHECK_INTERVAL_SECONDS = 1800
_DEFAULT_VERIFY_TIMEOUT_SECONDS = 600
_DEFAULT_RECOVER_TIMEOUT_SECONDS = 180
_POST_RECOVER_WAIT_SECONDS = 90


def set_status_callback(fn):
    global _status_callback
    _status_callback = fn


def set_progress_callback(fn):
    global _progress_callback
    _progress_callback = fn


def _emit_status(msg: str):
    try:
        if callable(_status_callback):
            _status_callback(str(msg or ""))
    except Exception:
        pass
    log.info(msg)


def _emit_progress(pct: int, msg: str = ""):
    try:
        if callable(_progress_callback):
            _progress_callback(int(pct), str(msg or ""))
    except Exception:
        pass
    log.info("Progress %s: %s", pct, msg)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _check_interval_seconds() -> int:
    return max(60, _env_int("ONEDRIVE_CHECK_INTERVAL_SECONDS", _DEFAULT_CHECK_INTERVAL_SECONDS))


def _verify_timeout_seconds() -> int:
    return max(30, _env_int("ONEDRIVE_VERIFY_TIMEOUT_SECONDS", _DEFAULT_VERIFY_TIMEOUT_SECONDS))


def _recover_timeout_seconds() -> int:
    return max(30, _env_int("ONEDRIVE_RECOVER_TIMEOUT_SECONDS", _DEFAULT_RECOVER_TIMEOUT_SECONDS))


def _log_script_output(output: str):
    for line in str(output or "").splitlines():
        line = line.strip()
        if line:
            log.info(line)


def _decode_process_output(raw: bytes | None) -> str:
    if not raw:
        return ""
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run_powershell_script(script_path: Path, timeout: int) -> tuple[int, str, str]:
    if os.name != "nt":
        return -1, "", "Ambiente não Windows"

    if not script_path.exists():
        return -1, "", f"Script não encontrado: {script_path}"

    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        stdout = _decode_process_output(result.stdout)
        stderr = _decode_process_output(result.stderr)
        _log_script_output(stdout)
        if stderr.strip():
            log.warning(stderr.strip())
        return int(result.returncode), stdout, stderr
    except subprocess.TimeoutExpired:
        log.error("Timeout ao executar %s (%ss)", script_path.name, timeout)
        return -1, "", f"Timeout após {timeout}s"
    except Exception as exc:
        log.exception("Erro ao executar %s", script_path.name)
        return -1, "", str(exc)


def _extract_problem_count(output: str) -> int | None:
    match = re.search(
        r"Arquivos com poss(?:í|i)vel problema de sincroniza(?:ç|c)(?:ã|a)o:\s*(\d+)",
        output,
        re.IGNORECASE,
    )
    if match:
        return int(match.group(1))

    match = re.search(
        r"Foram encontrados arquivos com poss(?:í|i)vel problema de sincroniza(?:ç|c)(?:ã|a)o",
        output,
        re.IGNORECASE,
    )
    if match:
        return None

    for line in output.splitlines():
        if "ArquivosComProblema" in line:
            count_match = re.search(r"(\d+)", line)
            if count_match:
                return int(count_match.group(1))
    return None


def _extract_dessync_reasons(output: str) -> list[str]:
    reasons: list[str] = []
    capture = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("motivos de dessincronizacao:"):
            capture = True
            continue
        if capture:
            if stripped.startswith("- "):
                reasons.append(stripped[2:].strip())
                continue
            if stripped == "" or stripped.startswith("Arquivos com problema:") or stripped.startswith("STATUS FINAL:"):
                break
    return reasons


def _is_dessync_output(output: str) -> bool:
    return bool(
        re.search(r"STATUS FINAL:\s*Poss(?:í|i)vel dessincroniza(?:ç|c)(?:ã|a)o detectada", output, re.IGNORECASE)
        or re.search(r"PossivelDessync\s*:\s*True", output, re.IGNORECASE)
    )


def verificar_status_onedrive() -> tuple[bool, int, str]:
    exit_code, stdout, stderr = _run_powershell_script(
        _SCRIPT_VERIFICAR,
        timeout=_verify_timeout_seconds(),
    )
    output = "\n".join(part for part in (stdout, stderr) if part).strip()
    ok = exit_code == 0 and not _is_dessync_output(output)
    return ok, exit_code, output


def recuperar_sessao_onedrive() -> tuple[bool, int, str]:
    exit_code, stdout, stderr = _run_powershell_script(
        _SCRIPT_RECUPERAR,
        timeout=_recover_timeout_seconds(),
    )
    output = "\n".join(part for part in (stdout, stderr) if part).strip()
    return exit_code == 0, exit_code, output


def _format_interval_minutes(seconds: int) -> int:
    return max(1, round(seconds / 60))


def _handle_verification_result(ok: bool, exit_code: int, output: str) -> bool:
    if ok:
        _emit_progress(100, "OneDrive OK")
        return True

    reasons = _extract_dessync_reasons(output)
    if exit_code == 2 or _is_dessync_output(output):
        problem_count = _extract_problem_count(output)
        if reasons:
            _emit_status(f"Possível dessincronização: {reasons[0]}")
            for reason in reasons[1:3]:
                _emit_status(f"OneDrive: {reason}")
        elif problem_count is not None:
            _emit_status(f"Possível dessincronização detectada ({problem_count} arquivos com problema)")
        else:
            _emit_status("Possível dessincronização detectada")
    elif exit_code == -1:
        _emit_status(f"Falha ao verificar OneDrive: {output or 'erro desconhecido'}")
    else:
        _emit_status(f"Verificação do OneDrive retornou código {exit_code}")

    _emit_progress(40, "OneDrive com problema detectado")
    return False


def _attempt_recovery() -> bool:
    _emit_status("Dessincronização detectada - iniciando recuperação de sessão")
    _emit_progress(50, "Recuperando sessão OneDrive")

    success, exit_code, output = recuperar_sessao_onedrive()
    if not success:
        _emit_status(f"Falha na recuperação (exit {exit_code}) - verifique logs")
        if output:
            log.warning(output)
        _emit_progress(0, "Falha na recuperação")
        return False

    _emit_status("Recuperação iniciada - conclua o login nas janelas do OneDrive, se aparecerem")
    _emit_status("Recuperação de sessão concluída - revalidando...")
    _emit_progress(70, "Revalidando OneDrive")
    if parar_event.wait(_POST_RECOVER_WAIT_SECONDS):
        return False

    ok, exit_code, output = verificar_status_onedrive()
    if ok:
        _emit_status("OneDrive recuperado com sucesso")
        _emit_progress(100, "OneDrive recuperado")
        return True

    _emit_status(f"OneDrive ainda com problemas após recuperação (exit {exit_code})")
    if output:
        log.warning(output)
    _emit_progress(60, "OneDrive ainda com problemas")
    return False


def _validate_scripts() -> bool:
    missing = [script for script in (_SCRIPT_VERIFICAR, _SCRIPT_RECUPERAR) if not script.exists()]
    if not missing:
        return True

    for script in missing:
        _emit_status(f"Script não encontrado: {script.name}")
        log.error("Script OneDrive não encontrado: %s", script)
    _emit_progress(0, "Scripts PowerShell ausentes")
    return False


def _run_cycle() -> bool:
    _emit_status("Verificando status do OneDrive...")
    _emit_progress(10, "Verificando OneDrive")

    ok, exit_code, output = verificar_status_onedrive()
    if _handle_verification_result(ok, exit_code, output):
        return True

    return _attempt_recovery()


def _run_loop(settings=None):
    del settings

    if os.name != "nt":
        _emit_status("Bot OneDrive disponível apenas em Windows")
        _emit_progress(0, "Ambiente não suportado")
        return

    interval_seconds = _check_interval_seconds()
    interval_minutes = _format_interval_minutes(interval_seconds)

    _emit_status("Bot OneDrive iniciado")
    _emit_progress(0, f"Monitoramento a cada {interval_minutes} min")

    if not _validate_scripts():
        _emit_status("Bot OneDrive encerrado - scripts ausentes")
        return

    while not parar_event.is_set():
        try:
            cycle_ok = _run_cycle()
            if not parar_event.is_set():
                if cycle_ok:
                    _emit_status(f"OneDrive OK - próxima verificação em {interval_minutes} min")
                else:
                    _emit_status(
                        f"Problemas persistem no OneDrive - próxima tentativa em {interval_minutes} min"
                    )
        except Exception as exc:
            log.exception("Erro no ciclo de monitoramento OneDrive")
            _emit_progress(0, f"Erro no ciclo: {exc}")
            _emit_status(f"Erro no monitoramento OneDrive: {exc}")

        if parar_event.wait(interval_seconds):
            break

    _emit_status("Bot OneDrive parado")
    _emit_progress(0, "Parado")


def start(settings=None):
    if parar_event.is_set():
        parar_event.clear()

    worker = threading.Thread(target=_run_loop, args=(settings,), daemon=True)
    worker.start()
    return worker


def stop():
    parar_event.set()
