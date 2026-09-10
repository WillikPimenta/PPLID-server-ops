# -*- coding: utf-8 -*-
"""Semáforo + single-flight para builds pesados (pools configuráveis por settings)."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from django.conf import settings

T = TypeVar("T")


class QueueTimeoutError(Exception):
    """Não conseguiu slot no semáforo dentro do wait timeout."""


class BuildTimeoutError(Exception):
    """Follower esperou demais pelo build do líder."""


class _FlightSlot:
    def __init__(self, label: str) -> None:
        self._label = label
        self._event = threading.Event()
        self._result: Any = None
        self._error: BaseException | None = None

    def set_result(self, value: Any) -> None:
        self._result = value
        self._event.set()

    def set_error(self, exc: BaseException) -> None:
        self._error = exc
        self._event.set()

    def wait(self, timeout: float) -> Any:
        ok = self._event.wait(timeout=timeout)
        if not ok:
            raise BuildTimeoutError(f"Timeout aguardando build de {self._label}.")
        if self._error is not None:
            raise self._error
        return self._result


class HeavyRequestGate:
    """Gate com pool próprio (settings prefix) — não compartilha semáforo entre workloads."""

    def __init__(
        self,
        *,
        label: str,
        max_concurrent_setting: str,
        queue_wait_ms_setting: str,
        build_wait_s_setting: str,
        default_max_concurrent: int = 1,
        default_queue_wait_ms: int = 15_000,
        default_build_wait_s: float = 60,
    ) -> None:
        self._label = label
        self._max_concurrent_setting = max_concurrent_setting
        self._queue_wait_ms_setting = queue_wait_ms_setting
        self._build_wait_s_setting = build_wait_s_setting
        self._default_max_concurrent = default_max_concurrent
        self._default_queue_wait_ms = default_queue_wait_ms
        self._default_build_wait_s = default_build_wait_s
        self._lock = threading.Lock()
        self._flights: dict[str, _FlightSlot] = {}
        self._semaphore: threading.BoundedSemaphore | None = None
        self._sem_capacity: int | None = None

    def _max_concurrent(self) -> int:
        return max(
            1,
            int(
                getattr(settings, self._max_concurrent_setting, self._default_max_concurrent)
                or self._default_max_concurrent
            ),
        )

    def _queue_wait_s(self) -> float:
        ms = int(
            getattr(settings, self._queue_wait_ms_setting, self._default_queue_wait_ms)
            or self._default_queue_wait_ms
        )
        return max(0.1, ms / 1000.0)

    def _build_wait_s(self) -> float:
        return float(
            getattr(settings, self._build_wait_s_setting, self._default_build_wait_s)
            or self._default_build_wait_s
        )

    def _get_semaphore(self) -> threading.BoundedSemaphore:
        capacity = self._max_concurrent()
        if self._semaphore is None or self._sem_capacity != capacity:
            self._semaphore = threading.BoundedSemaphore(capacity)
            self._sem_capacity = capacity
        return self._semaphore

    def run(self, key: str, builder: Callable[[], T]) -> T:
        """
        Single-flight por `key` + semáforo do pool.
        Raises QueueTimeoutError / BuildTimeoutError.
        """
        is_leader = False
        slot: _FlightSlot | None = None

        with self._lock:
            existing = self._flights.get(key)
            if existing is not None:
                slot = existing
            else:
                slot = _FlightSlot(self._label)
                self._flights[key] = slot
                is_leader = True

        if not is_leader:
            assert slot is not None
            return slot.wait(self._build_wait_s())

        assert slot is not None
        sem = self._get_semaphore()
        acquired = sem.acquire(timeout=self._queue_wait_s())
        if not acquired:
            with self._lock:
                self._flights.pop(key, None)
            msg = f"Fila de {self._label} esgotou o tempo de espera."
            slot.set_error(QueueTimeoutError(msg))
            raise QueueTimeoutError(msg)

        try:
            result = builder()
            slot.set_result(result)
            return result
        except Exception as exc:
            slot.set_error(exc)
            raise
        finally:
            sem.release()
            with self._lock:
                self._flights.pop(key, None)
