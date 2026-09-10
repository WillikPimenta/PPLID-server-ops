"""Shared bot callback/progress/stop runtime — replaces per-bot boilerplate."""

from __future__ import annotations

from typing import Callable, Optional

from app.core.common import CallbackManager, StopEvent
from app.services.progress_profiles import fallback_linear_pct, get_profile_store


class BotRuntime:
    """Module-level runtime for status/progress callbacks and stop events."""

    def __init__(self, mode: str | None = None) -> None:
        self.mode = mode
        self.callback_mgr = CallbackManager()
        self.parar_event = StopEvent()
        self._last_progress_pct = 0

    def set_mode(self, mode: str | None) -> None:
        self.mode = mode

    def set_status_callback(self, fn: Optional[Callable[[str], None]]) -> None:
        """Register status callback."""
        self.callback_mgr.set_status_callback(fn)

    def set_progress_callback(self, fn: Optional[Callable[[int, str], None]]) -> None:
        """Register progress callback."""
        self.callback_mgr.set_progress_callback(fn)

    def set_status(self, msg: str) -> None:
        """Emit status event."""
        self.callback_mgr.emit_status(msg)

    def reset_progress_state(self) -> None:
        """Reset monotonic progress tracking for a new run."""
        self._last_progress_pct = 0

    def begin_cycle(self) -> None:
        """Reset monotonic progress for a new cycle within a long-running loop."""
        self._last_progress_pct = 0

    def _resolve_progress_pct(self, pct: int, msg: str) -> int:
        # O D-1 possui fases e contadores determinísticos. Perfis históricos
        # distorcidos não devem substituir esses marcos (ex.: 0 -> 99%).
        if self.mode == "replicacao_auditoria_d1":
            linear = fallback_linear_pct(self.mode, msg)
            return linear if linear is not None else pct
        store = get_profile_store()
        if store.has_profile(self.mode):
            resolved = store.resolve_pct(self.mode, msg, pct)
            if resolved is not None:
                return resolved
        linear = fallback_linear_pct(self.mode, msg)
        if linear is not None:
            return linear
        return pct

    def set_progress(self, pct: int, msg: str = "") -> None:
        """Emit monotonic progress (0-100), preventing regression."""
        try:
            pct_int = int(pct)
        except Exception:
            pct_int = self._last_progress_pct

        pct_int = self._resolve_progress_pct(pct_int, msg)
        pct_int = max(0, min(100, pct_int))
        if pct_int < self._last_progress_pct:
            pct_int = self._last_progress_pct

        self._last_progress_pct = pct_int
        self.callback_mgr.emit_progress(pct_int, msg)

    def set_progress_with_error_feedback(self, pct: int, msg: str = "") -> None:
        """Monotonic progress, but allows regression when msg signals error (monitor_excel)."""
        try:
            pct_int = int(pct)
        except Exception:
            pct_int = self._last_progress_pct

        pct_int = self._resolve_progress_pct(pct_int, msg)
        pct_int = max(0, min(100, pct_int))
        msg_l = str(msg or "").lower()

        if "✗" in str(msg or "") or "erro" in msg_l or "falha" in msg_l:
            self.callback_mgr.emit_progress(pct_int, msg)
            return

        if pct_int < self._last_progress_pct:
            pct_int = self._last_progress_pct

        self._last_progress_pct = pct_int
        self.callback_mgr.emit_progress(pct_int, msg)
