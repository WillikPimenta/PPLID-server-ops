"""Legacy API wrapper for nivel/monitor/production robots."""

import logging
import os
from typing import Callable, Optional

from app.config import *
from app.core.credentials import get_credentials as _core_get_credentials

LOG = logging.getLogger("robots")

try:
    from app.bots import nivel_h as nivel
except Exception:
    nivel = None

try:
    from app.bots import monitor as monitor_mod
except Exception:
    monitor_mod = None

try:
    from app.bots import bot_production as bproduction
except Exception:
    bproduction = None


class RobotWrapper:
    """Wrapper genérico para controlar robôs (nivel, monitor)."""

    def __init__(self, module, robot_name: str):
        self.module = module
        self.name = robot_name
        self.logger = LOG

    def _get_function(self, *names) -> Optional[Callable]:
        if not self.module:
            return None
        for name in names:
            fn = getattr(self.module, name, None)
            if callable(fn):
                return fn
        return None

    def set_status_callback(self, fn: Callable[[str], None]):
        if not self.module:
            self.logger.error("%s não disponível", self.name)
            return
        cb_fn = self._get_function("set_status_callback", "set_status")
        if not cb_fn:
            self.logger.warning("%s não expõe set_status_callback", self.name)
            return
        try:
            cb_fn(fn)
        except Exception:
            pass

    def set_progress_callback(self, fn: Callable[[int, str], None]):
        if not self.module:
            return
        cb_fn = self._get_function("set_progress_callback")
        if cb_fn:
            try:
                cb_fn(fn)
            except Exception:
                pass

    def start(self, settings: Optional[dict] = None, intervalo: int = 30, tempo_max: Optional[int] = None) -> bool:
        if not self.module:
            self.logger.error("%s não disponível", self.name)
            return False
        start_fn = self._get_function("start", f"start_{self.name}", f"iniciar_{self.name}")
        if not start_fn:
            self.logger.error("Função de start não encontrada em %s", self.name)
            return False
        try:
            start_fn(settings=settings, intervalo=intervalo, tempo_max=tempo_max)
            return True
        except TypeError:
            try:
                start_fn()
                return True
            except Exception:
                return False
        except Exception:
            return False

    def stop(self, timeout: int = 5):
        if not self.module:
            self.logger.error("%s não disponível", self.name)
            return
        stop_fn = self._get_function("stop", f"stop_{self.name}", f"parar_{self.name}")
        if not stop_fn:
            return
        try:
            stop_fn(timeout=timeout)
        except TypeError:
            try:
                stop_fn()
            except Exception:
                pass
        except Exception:
            pass

    def is_running(self) -> bool:
        if not self.module:
            return False
        thread = getattr(self.module, "_thread", None)
        return bool(thread and getattr(thread, "is_alive", lambda: False)())

    def status(self) -> dict:
        return {
            "name": self.name,
            "running": self.is_running(),
            "thread": repr(getattr(self.module, "_thread", None)) if self.module else None,
        }


_nivel_wrapper = RobotWrapper(nivel, "nivel")
_monitor_wrapper = RobotWrapper(monitor_mod, "monitor")
_production_wrapper = RobotWrapper(bproduction, "production")


def set_status_callback_nivel(fn: Callable[[str], None]):
    _nivel_wrapper.set_status_callback(fn)


def set_progress_callback_nivel(fn: Callable[[int, str], None]):
    _nivel_wrapper.set_progress_callback(fn)


def iniciar_nivel_hierarquico(settings: Optional[dict] = None, intervalo: int = 30, tempo_max: Optional[int] = None) -> bool:
    return _nivel_wrapper.start(settings, intervalo, tempo_max)


def pausar_nivel_hierarquico(timeout: int = 5):
    _nivel_wrapper.stop(timeout)


def status_nivel_hierarquico() -> dict:
    return _nivel_wrapper.status()


def iniciar_bproduction(settings: Optional[dict] = None, intervalo: int = 30, tempo_max: Optional[int] = None) -> bool:
    return _production_wrapper.start(settings, intervalo, tempo_max)


def pausar_bproduction(timeout: int = 5):
    _production_wrapper.stop(timeout)


def status_bproduction() -> dict:
    return _production_wrapper.status()


def set_status_callback_monitor(fn: Callable[[str], None]):
    _monitor_wrapper.set_status_callback(fn)


def set_progress_callback_monitor(fn: Callable[[int, str], None]):
    _monitor_wrapper.set_progress_callback(fn)


def iniciar_monitor(settings: Optional[dict] = None, intervalo: int = 30, tempo_max: Optional[int] = None) -> bool:
    return _monitor_wrapper.start(settings, intervalo, tempo_max)


def pausar_monitor(timeout: int = 5):
    _monitor_wrapper.stop(timeout)


def status_monitor() -> dict:
    return _monitor_wrapper.status()


def pausar_robots(timeout: int = 5):
    pausar_nivel_hierarquico(timeout)
    pausar_monitor(timeout)
    pausar_bproduction(timeout)


def status_robots() -> dict:
    return {
        "nivel": status_nivel_hierarquico(),
        "monitor": status_monitor(),
        "production": status_bproduction(),
    }


start_nivel = iniciar_nivel_hierarquico
stop_nivel = pausar_nivel_hierarquico
start_monitor = iniciar_monitor
stop_monitor = pausar_monitor
start_production = iniciar_bproduction
stop_production = pausar_bproduction


def set_status_callback(fn):
    set_status_callback_nivel(fn)
    set_status_callback_monitor(fn)


def set_progress_callback(fn):
    set_progress_callback_nivel(fn)
    set_progress_callback_monitor(fn)


def get_credentials():
    """Obtém credenciais do processo atual (interface web)."""
    user = os.getenv("MONITOR_USER") or os.getenv("OKTA_USER") or os.getenv("NIVEL_USER")
    pwd = os.getenv("MONITOR_PASS") or os.getenv("OKTA_PASS") or os.getenv("NIVEL_PASS")
    if user and pwd:
        return user, pwd
    return _core_get_credentials()


if __name__ == "__main__":
    pass
