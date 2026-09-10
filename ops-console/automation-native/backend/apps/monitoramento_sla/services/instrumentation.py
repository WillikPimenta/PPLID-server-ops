from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator


class SyncMetrics:
    """Métricas leves por etapa, persistidas no SlaUtilSyncRun."""

    def __init__(self) -> None:
        self.started_at = perf_counter()
        self.stages: dict[str, float] = {}
        self.counts: dict[str, int | float | bool | str] = {}

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = perf_counter()
        try:
            yield
        finally:
            self.stages[name] = round(perf_counter() - started, 4)

    def count(self, name: str, value: int | float | bool | str) -> None:
        self.counts[name] = value

    def payload(self) -> dict:
        return {
            "duration_seconds": round(perf_counter() - self.started_at, 4),
            "stages_seconds": dict(self.stages),
            "counts": dict(self.counts),
        }
