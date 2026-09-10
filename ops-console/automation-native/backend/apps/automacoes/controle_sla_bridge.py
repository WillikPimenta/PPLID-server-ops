"""Bridge: card Automações 'controle_sla' ↔ poller BrFlow do Controle de SLA."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

CONTROLE_SLA_MODE = "controle_sla"


def _poller():
    from apps.controle_sla.services.poller import get_poller

    return get_poller()


def _append_robot_log(message: str) -> None:
    try:
        from apps.automacoes.services import get_robot_manager

        rm = get_robot_manager()
        if hasattr(rm, "_append_log"):
            rm._append_log(CONTROLE_SLA_MODE, message)
    except Exception:
        log.debug("Não foi possível gravar log Automações do Controle SLA", exc_info=True)


def status_entry() -> dict[str, Any]:
    st = _poller().status()
    connected = bool(st.get("connected"))
    connecting = bool(st.get("connecting"))
    running = connected or connecting
    message = str(st.get("message") or ("Conectado" if connected else "Parado"))
    last_poll = st.get("last_poll_at")
    now = datetime.now().isoformat(timespec="seconds")
    progress = 100 if connected else (40 if connecting else 0)
    return {
        "running": running,
        "pid": None,
        "execution": {
            "started_at": last_poll if connected else None,
            "ended_at": None if running else now,
            "duration_seconds": None,
            "result": None if running else ("ok" if connected else "stopped"),
            "output_paths": None,
        },
        "runtime": {
            "progress": progress,
            "status": message,
            "updated_at": last_poll or now,
        },
        "controle_sla": {
            "connected": connected,
            "connecting": connecting,
            "auth_error": bool(st.get("auth_error")),
            "last_error": st.get("last_error"),
            "last_poll_at": last_poll,
            "last_stats": st.get("last_stats") or {},
            "poll_seconds": st.get("poll_seconds"),
            "gap_seconds": st.get("gap_seconds"),
            "sla_alerta_pct": st.get("sla_alerta_pct"),
            "sla_medio_pct": st.get("sla_medio_pct"),
            "sla_alto_pct": st.get("sla_alto_pct"),
            "sla_critico_pct": st.get("sla_critico_pct"),
            "matricula": st.get("matricula"),
        },
    }


def merge_robots_status(robots: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(robots or {})
    payload[CONTROLE_SLA_MODE] = status_entry()
    return payload


def apply_controle_sla_config(robot_config: dict | None = None) -> dict[str, Any]:
    """Persiste limiares/intervalos no poller em memória (útil após Salvar sem reiniciar)."""
    cfg = robot_config if isinstance(robot_config, dict) else {}
    poller = _poller()
    if cfg.get("poll_seconds") is not None:
        try:
            poller.set_poll_seconds(int(cfg["poll_seconds"]))
        except Exception:
            pass
    if cfg.get("gap_seconds") is not None:
        try:
            poller.set_gap_seconds(int(cfg["gap_seconds"]))
        except Exception:
            pass
    try:
        poller.set_farol_thresholds(
            alerta=float(cfg["sla_alerta_pct"]) if cfg.get("sla_alerta_pct") is not None else None,
            medio=float(cfg["sla_medio_pct"]) if cfg.get("sla_medio_pct") is not None else None,
            alto=float(cfg["sla_alto_pct"]) if cfg.get("sla_alto_pct") is not None else None,
            critico=float(cfg["sla_critico_pct"]) if cfg.get("sla_critico_pct") is not None else None,
        )
    except Exception:
        pass
    if cfg.get("protocolos_dias") is not None:
        try:
            poller.set_protocolos_dias(int(cfg["protocolos_dias"]))
        except Exception:
            pass
    return poller.status()


def start_controle_sla(
    *,
    matricula: str,
    senha: str,
    robot_config: dict | None = None,
    require_okta_validation: bool = True,
    okta_session_id: str | None = None,
) -> tuple[bool, str]:
    """Valida Okta (como os demais bots) e inicia o poller BrFlow no processo Django."""
    from apps.automacoes.services import get_robot_manager

    user = (matricula or "").strip()
    password = senha or ""
    if not user or not password:
        return False, "Informe matrícula e senha no painel para executar o robô"

    rm = get_robot_manager()
    if require_okta_validation:
        sid = rm._normalize_okta_session_id(okta_session_id) or "default"
        with rm._lock:
            _, cred_state = rm._ensure_okta_session(sid)
            credentials_ok = bool(cred_state.get("validated"))
            same_credentials = cred_state.get("fingerprint") == rm._credentials_fingerprint(user, password)
            if not credentials_ok or not same_credentials:
                return (
                    False,
                    "Credenciais não validadas no Okta. Clique em 'Validar credenciais' antes de iniciar.",
                )

    cfg = robot_config if isinstance(robot_config, dict) else {}
    # Persiste config no robot_manager (poll interval etc.)
    try:
        rm.update_robot_config(CONTROLE_SLA_MODE, cfg)
        cfg = rm.robot_configs(mode=CONTROLE_SLA_MODE).get(CONTROLE_SLA_MODE) or cfg
    except Exception:
        pass

    poll_seconds = cfg.get("poll_seconds")
    gap_seconds = cfg.get("gap_seconds")
    headless = bool(cfg.get("headless", False))

    apply_controle_sla_config(cfg)
    poller = _poller()

    result = poller.connect(
        matricula=user,
        senha=password,
        salvar_dados=True,  # reconexão automática se a sessão BrFlow cair
        headless=headless,
        force=True,
    )
    if not result.get("ok"):
        msg = str(result.get("message") or "Falha ao iniciar Controle de SLA.")
        _append_robot_log(f"[start] falhou: {msg}")
        return False, msg

    alerta = result.get("sla_alerta_pct")
    _append_robot_log(
        f"[start] conexão BrFlow iniciada (headless={headless}, poll={result.get('poll_seconds')}s, alerta≥{alerta}%)"
    )
    return True, "Controle de SLA iniciado — conectando ao BrFlow…"


def stop_controle_sla() -> tuple[bool, str]:
    poller = _poller()
    st = poller.status()
    if not st.get("connected") and not st.get("connecting"):
        return False, "Controle de SLA não está em execução"
    poller.cancel()
    _append_robot_log("[stop] sessão BrFlow encerrada")
    return True, "Controle de SLA parado"


def ensure_mode_in_robot_manager() -> None:
    """Garante estruturas de log/config se o modo já estiver em ROBOT_MODES."""
    try:
        from apps.automacoes.services import get_robot_manager

        rm = get_robot_manager()
        if CONTROLE_SLA_MODE not in getattr(rm, "_logs", {}):
            return
    except Exception:
        pass
