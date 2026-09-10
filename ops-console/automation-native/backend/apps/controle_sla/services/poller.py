"""Poller singleton: sessão BrFlow em memória + ciclo de avaliação SLA."""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import timedelta
from typing import Any

from django.utils import timezone

from apps.controle_sla.exceptions import AuthFailureError, ControleSlaError, SessionExpiredError
from apps.controle_sla.services.brflow_client import (
    BrflowBrowserSession,
    CancelledError,
    establish_brflow_session,
    fetch_fila,
    _friendly_browser_error,
)
from apps.controle_sla.services.sla_eval import (
    DEFAULT_PROTOCOLOS_DIAS,
    FarolThresholds,
    evaluate_and_persist,
    normalize_protocolos_dias,
)

log = logging.getLogger(__name__)

DEFAULT_POLL_SECONDS = 60
DEFAULT_GAP_SECONDS = 3600
DEFAULT_ALERTA_PCT = 80
DEFAULT_MEDIO_PCT = 90
DEFAULT_ALTO_PCT = 95
DEFAULT_CRITICO_PCT = 100


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return max(15, int(raw))
    except ValueError:
        return default


def _env_pct(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        return float(default)


class ControleSlaPoller:
    """Estado de conexão e thread de poll (processo local)."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: BrflowBrowserSession | None = None
        self._connected = False
        self._connecting = False
        self._matricula: str | None = None
        self._senha: str | None = None
        self._salvar_dados = False
        self._message = ""
        self._auth_error = False
        self._last_poll_at: str | None = None
        self._next_poll_at: str | None = None
        self._last_error: str | None = None
        self._last_stats: dict[str, int] = {}
        self._poll_seconds = _env_int("CONTROLE_SLA_POLL_SECONDS", DEFAULT_POLL_SECONDS)
        self._gap_seconds = _env_int("CONTROLE_SLA_GAP_SECONDS", DEFAULT_GAP_SECONDS)
        self._farol = FarolThresholds(
            alerta=_env_pct("CONTROLE_SLA_ALERTA_PCT", DEFAULT_ALERTA_PCT),
            medio=_env_pct("CONTROLE_SLA_MEDIO_PCT", DEFAULT_MEDIO_PCT),
            alto=_env_pct("CONTROLE_SLA_ALTO_PCT", DEFAULT_ALTO_PCT),
            critico=_env_pct("CONTROLE_SLA_CRITICO_PCT", DEFAULT_CRITICO_PCT),
        ).normalized()
        try:
            self._protocolos_dias = normalize_protocolos_dias(
                int(os.getenv("CONTROLE_SLA_PROTOCOLOS_DIAS") or DEFAULT_PROTOCOLOS_DIAS)
            )
        except ValueError:
            self._protocolos_dias = DEFAULT_PROTOCOLOS_DIAS
        self._last_gap_at = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._cancel = threading.Event()
        self._connect_thread: threading.Thread | None = None
        self._active_driver: Any = None
        self._connect_gen = 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            farol = self._farol.as_dict()
            return {
                "connected": self._connected and self._session is not None,
                "connecting": self._connecting,
                "auth_error": self._auth_error,
                "message": self._message,
                "last_error": self._last_error,
                "matricula": self._matricula if self._connected or self._salvar_dados or self._connecting else None,
                "salvar_dados": self._salvar_dados,
                "poll_seconds": self._poll_seconds,
                "gap_seconds": self._gap_seconds,
                "protocolos_dias": self._protocolos_dias,
                "last_poll_at": self._last_poll_at,
                "next_poll_at": self._next_poll_at,
                "last_stats": dict(self._last_stats),
                **farol,
            }

    def connect(
        self,
        *,
        matricula: str,
        senha: str,
        salvar_dados: bool = False,
        headless: bool = False,
        force: bool = False,
    ) -> dict[str, Any]:
        """Inicia conexão em background. Retorna imediatamente (poll status / cancel)."""
        with self._lock:
            busy = self._connecting or (self._connect_thread is not None and self._connect_thread.is_alive())
            if busy and not force:
                # Se o usuário cancelou, a thread antiga pode ainda estar morrendo —
                # force limpa e permite novo login.
                if self._cancel.is_set() or not self._connecting:
                    self._force_cleanup_unlocked()
                else:
                    return {
                        **self.status(),
                        "ok": False,
                        "message": "Conexão já em andamento. Use Cancelar para interromper.",
                    }
            elif busy and force:
                self._force_cleanup_unlocked()

            self._cancel.clear()
            self._connecting = True
            self._auth_error = False
            self._message = "Conectando ao Okta/BrFlow…"
            self._last_error = None
            self._matricula = (matricula or "").strip()
            self._connect_gen += 1
            gen = self._connect_gen

            thread = threading.Thread(
                target=self._connect_worker,
                kwargs={
                    "matricula": matricula,
                    "senha": senha,
                    "salvar_dados": salvar_dados,
                    "headless": headless,
                    "gen": gen,
                },
                name="controle-sla-connect",
                daemon=True,
            )
            self._connect_thread = thread
            thread.start()

        return {**self.status(), "ok": True, "started": True}

    def _force_cleanup_unlocked(self) -> None:
        """Interrompe tentativa anterior sem esperar a thread terminar."""
        self._cancel.set()
        self._stop_thread_unlocked()
        drv = self._active_driver
        self._active_driver = None
        session = self._session
        self._session = None
        self._connected = False
        self._connecting = False
        self._connect_thread = None
        self._connect_gen += 1
        if drv is not None:
            threading.Thread(target=self._quit_driver_silent, args=(drv,), daemon=True).start()
        if session is not None:
            threading.Thread(target=self._close_session_silent, args=(session,), daemon=True).start()

    @staticmethod
    def _quit_driver_silent(drv: Any) -> None:
        try:
            drv.quit()
        except Exception:
            pass

    @staticmethod
    def _close_session_silent(session: BrflowBrowserSession) -> None:
        try:
            session.close()
        except Exception:
            pass

    def cancel(self) -> dict[str, Any]:
        """Cancela conexão em andamento e/ou desconecta sessão ativa."""
        with self._lock:
            self._force_cleanup_unlocked()
            self._clear_credentials_unlocked()
            self._auth_error = False
            self._message = "Conexão cancelada."
            self._last_error = None
            # Pronto para novo connect imediatamente
            self._cancel.set()
        return {**self.status(), "ok": True}

    def disconnect(self) -> dict[str, Any]:
        return self.cancel()

    def set_poll_seconds(self, seconds: int) -> dict[str, Any]:
        with self._lock:
            self._poll_seconds = max(15, int(seconds))
        return self.status()

    def set_gap_seconds(self, seconds: int) -> dict[str, Any]:
        with self._lock:
            self._gap_seconds = max(15, int(seconds))
        return self.status()

    def set_protocolos_dias(self, dias: int) -> dict[str, Any]:
        with self._lock:
            self._protocolos_dias = normalize_protocolos_dias(dias)
        return self.status()

    def set_farol_thresholds(
        self,
        *,
        alerta: float | None = None,
        medio: float | None = None,
        alto: float | None = None,
        critico: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            current = self._farol
            self._farol = FarolThresholds(
                alerta=float(alerta) if alerta is not None else current.alerta,
                medio=float(medio) if medio is not None else current.medio,
                alto=float(alto) if alto is not None else current.alto,
                critico=float(critico) if critico is not None else current.critico,
            ).normalized()
        return self.status()

    def _is_cancelled(self) -> bool:
        return self._cancel.is_set()

    def _on_driver(self, driver: Any) -> None:
        with self._lock:
            self._active_driver = driver

    def _connect_worker(
        self,
        *,
        matricula: str,
        senha: str,
        salvar_dados: bool,
        headless: bool,
        gen: int,
    ) -> None:
        def stale() -> bool:
            with self._lock:
                return gen != self._connect_gen or self._cancel.is_set()

        try:
            session = establish_brflow_session(
                matricula=matricula,
                senha=senha,
                headless=headless,
                is_cancelled=lambda: stale(),
                on_driver=self._on_driver,
            )
        except CancelledError:
            with self._lock:
                if gen == self._connect_gen:
                    self._session = None
                    self._connected = False
                    self._connecting = False
                    self._active_driver = None
                    self._clear_credentials_unlocked()
                    self._auth_error = False
                    self._message = "Conexão cancelada."
                    self._last_error = None
                    self._connect_thread = None
            return
        except AuthFailureError as exc:
            with self._lock:
                if gen == self._connect_gen:
                    self._finish_connect_error_unlocked(str(exc), auth_error=True)
            return
        except SessionExpiredError as exc:
            with self._lock:
                if gen == self._connect_gen:
                    self._finish_connect_error_unlocked(str(exc), auth_error=False)
            return
        except ControleSlaError as exc:
            log.exception("Controle SLA connect failed")
            with self._lock:
                if gen == self._connect_gen:
                    self._finish_connect_error_unlocked(str(exc), auth_error=False)
            return
        except Exception as exc:
            log.exception("Controle SLA connect failed")
            with self._lock:
                if gen == self._connect_gen:
                    self._finish_connect_error_unlocked(
                        _friendly_browser_error(exc),
                        auth_error=False,
                    )
            return

        if stale():
            try:
                session.close()
            except Exception:
                pass
            with self._lock:
                if gen == self._connect_gen:
                    self._session = None
                    self._connected = False
                    self._connecting = False
                    self._active_driver = None
                    self._clear_credentials_unlocked()
                    self._message = "Conexão cancelada."
                    self._connect_thread = None
            return

        with self._lock:
            if gen != self._connect_gen or self._cancel.is_set():
                try:
                    session.close()
                except Exception:
                    pass
                return
            old = self._session
            self._session = session
            self._connected = True
            self._connecting = False
            self._active_driver = None
            self._auth_error = False
            self._matricula = (matricula or "").strip()
            if salvar_dados:
                self._senha = senha
                self._salvar_dados = True
            else:
                self._senha = None
                self._salvar_dados = False
            self._message = "Conectado ao BrFlow."
            self._last_error = None
            self._connect_thread = None
            self._ensure_thread_unlocked()

        if old is not None:
            try:
                old.close()
            except Exception:
                pass

        try:
            self._run_cycle(force_gaps=True)
        except Exception as exc:
            log.warning("Primeiro ciclo Controle SLA falhou: %s", exc)

    def _finish_connect_error_unlocked(self, message: str, *, auth_error: bool) -> None:
        self._session = None
        self._connected = False
        self._connecting = False
        self._active_driver = None
        self._connect_thread = None
        self._next_poll_at = None
        self._clear_credentials_unlocked()
        self._auth_error = auth_error
        self._message = message
        self._last_error = message
        self._stop_thread_unlocked()

    def _clear_credentials_unlocked(self) -> None:
        self._senha = None
        self._salvar_dados = False
        self._matricula = None

    def _ensure_thread_unlocked(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="controle-sla-poller", daemon=True)
        self._thread.start()

    def _stop_thread_unlocked(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(timeout=1.0):
            with self._lock:
                connected = self._connected and self._session is not None
                interval = self._poll_seconds
            if not connected:
                continue
            started = time.monotonic()
            try:
                self._run_cycle(force_gaps=False)
            except Exception:
                log.exception("Ciclo Controle SLA")
            elapsed = time.monotonic() - started
            wait = max(1.0, interval - elapsed)
            with self._lock:
                self._next_poll_at = (timezone.now() + timedelta(seconds=wait)).isoformat()
            if self._stop.wait(timeout=wait):
                break

    def _sync_config_from_robot_manager(self) -> None:
        """Relê poll/gap/farol do robot_config.json a cada ciclo (Salvar sem reiniciar)."""
        try:
            from apps.automacoes.services import get_robot_manager

            cfg = get_robot_manager().robot_configs(mode="controle_sla").get("controle_sla") or {}
        except Exception:
            return
        if not isinstance(cfg, dict) or not cfg:
            return
        with self._lock:
            if cfg.get("poll_seconds") is not None:
                try:
                    self._poll_seconds = max(15, int(cfg["poll_seconds"]))
                except (TypeError, ValueError):
                    pass
            if cfg.get("gap_seconds") is not None:
                try:
                    self._gap_seconds = max(15, int(cfg["gap_seconds"]))
                except (TypeError, ValueError):
                    pass
            if cfg.get("protocolos_dias") is not None:
                try:
                    self._protocolos_dias = normalize_protocolos_dias(cfg["protocolos_dias"])
                except (TypeError, ValueError):
                    pass
            try:
                self._farol = FarolThresholds(
                    alerta=float(cfg["sla_alerta_pct"]) if cfg.get("sla_alerta_pct") is not None else self._farol.alerta,
                    medio=float(cfg["sla_medio_pct"]) if cfg.get("sla_medio_pct") is not None else self._farol.medio,
                    alto=float(cfg["sla_alto_pct"]) if cfg.get("sla_alto_pct") is not None else self._farol.alto,
                    critico=float(cfg["sla_critico_pct"]) if cfg.get("sla_critico_pct") is not None else self._farol.critico,
                ).normalized()
            except (TypeError, ValueError):
                pass

    def _run_cycle(self, *, force_gaps: bool) -> None:
        self._sync_config_from_robot_manager()
        with self._lock:
            session = self._session
            connected = self._connected
            gap_due = force_gaps or (time.monotonic() - self._last_gap_at) >= self._gap_seconds
            farol = self._farol
            protocolos_dias = self._protocolos_dias
        if not connected or session is None:
            return

        try:
            rows = fetch_fila(session)
        except SessionExpiredError as exc:
            self._handle_session_expired(str(exc))
            return
        except AuthFailureError as exc:
            self._handle_auth_failure(str(exc))
            return
        except ControleSlaError as exc:
            with self._lock:
                self._last_error = str(exc)
                self._message = str(exc)
            return

        stats = evaluate_and_persist(
            rows,
            update_gaps=gap_due,
            thresholds=farol,
            protocolos_dias=protocolos_dias,
        )
        with self._lock:
            self._last_stats = stats
            self._last_poll_at = timezone.now().isoformat()
            self._next_poll_at = (
                timezone.now() + timedelta(seconds=max(15, int(self._poll_seconds)))
            ).isoformat()
            self._last_error = None
            self._message = (
                f"Conectado ao BrFlow · alerta≥{int(farol.alerta)}% · "
                f"{stats.get('breaches', 0)} em acompanhamento / {stats.get('rows_in', 0)} filas"
            )
            if gap_due:
                self._last_gap_at = time.monotonic()

    def _handle_auth_failure(self, message: str) -> None:
        with self._lock:
            self._stop_thread_unlocked()
            session = self._session
            self._session = None
            self._connected = False
            self._next_poll_at = None
            self._clear_credentials_unlocked()
            self._auth_error = True
            self._message = message
            self._last_error = message
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        log.warning("Controle SLA auth failure: %s", message)

    def _handle_session_expired(self, message: str) -> None:
        with self._lock:
            salvar = self._salvar_dados
            matricula = self._matricula
            senha = self._senha
            session = self._session
            self._session = None
            self._connected = False

        if session is not None:
            try:
                session.close()
            except Exception:
                pass

        if not (salvar and matricula and senha):
            with self._lock:
                self._stop_thread_unlocked()
                self._clear_credentials_unlocked()
                self._auth_error = False
                self._message = "Sessão expirada. Conecte novamente."
                self._last_error = message
            return

        log.info("Controle SLA: sessão expirada — tentando reconectar com credenciais em memória.")
        result = self.connect(
            matricula=matricula,
            senha=senha,
            salvar_dados=True,
            headless=False,
        )
        if not result.get("ok"):
            with self._lock:
                if not self._auth_error and not self._connecting:
                    self._message = "Falha na reconexão automática. Conecte novamente."
                    self._last_error = message


_poller: ControleSlaPoller | None = None
_poller_lock = threading.Lock()


def get_poller() -> ControleSlaPoller:
    global _poller
    with _poller_lock:
        if _poller is None:
            _poller = ControleSlaPoller()
        return _poller
